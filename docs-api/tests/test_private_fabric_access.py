from __future__ import annotations

import psycopg2.extras
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.agent_access_api import _rate_limit
from app.application import app
from app.db import get_conn

client = TestClient(app)


def test_managed_profile_is_fail_closed_and_operator_issued(monkeypatch):
    monkeypatch.setenv("DOCPLANE_ACCESS_PROFILE", "managed")
    discovery = client.get("/.well-known/docplane.json").json()
    acquisition = discovery["authentication"]["token_acquisition"]
    assert discovery["contract_version"] == "docplane-agent-discovery-v4"
    assert acquisition["access_profile"] == "managed"
    assert acquisition["self_service"] is False
    assert acquisition["endpoint"] is None
    response = client.post("/api/v1/auth/self-issue", json={"display_name": "agent"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SELF_ISSUE_DISABLED"


def test_private_fabric_discovery_is_an_unambiguous_zero_credential_path(monkeypatch):
    monkeypatch.setenv("DOCPLANE_ACCESS_PROFILE", "private_fabric")
    monkeypatch.setenv("DOCPLANE_SELF_ISSUE_TTL_SECONDS", "3600")
    discovery = client.get("/.well-known/docplane.json").json()
    acquisition = discovery["authentication"]["token_acquisition"]
    assert acquisition["access_profile"] == "private_fabric"
    assert acquisition["self_service"] is True
    assert acquisition["requires_existing_credential"] is False
    assert acquisition["endpoint"] == "/api/v1/auth/self-issue"
    assert acquisition["principal_kind"] == "AGENT"
    assert acquisition["role"] == "CONTRIBUTOR"
    assert acquisition["default_ttl_seconds"] == 3600
    assert acquisition["maximum_ttl_seconds"] == 86400
    assert acquisition["rate_limit"] == {
        "scope": "observed routed source fingerprint",
        "burst": {"limit": 12, "window_seconds": 60},
        "sustained": {"limit": 30, "window_seconds": 3600},
        "global_sustained": {"limit": 300, "window_seconds": 3600},
        "rejection": {
            "status": 429,
            "retry_header": "Retry-After",
            "error_code": "SELF_ISSUE_RATE_LIMITED",
        },
    }
    assert discovery["surfaces"]["self_issue"] == "/api/v1/auth/self-issue"
    assert discovery["quick_start"]["authenticate"][0] == "POST /api/v1/auth/self-issue"


def test_private_fabric_profile_does_not_trust_direct_api_reachability(monkeypatch):
    monkeypatch.setenv("DOCPLANE_ACCESS_PROFILE", "private_fabric")
    response = client.post("/api/v1/auth/self-issue", json={"display_name": "direct-agent"})
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "FABRIC_ADMISSION_REQUIRED"
    assert "hostname" in detail["remedy"].lower()
    assert "direct docs-api" in detail["remedy"].lower()


def test_head_uses_get_status_and_headers_without_a_body(monkeypatch):
    monkeypatch.setenv("DOCPLANE_ACCESS_PROFILE", "private_fabric")
    response = client.head("/.well-known/docplane.json")
    assert response.status_code == 200
    assert response.content == b""
    unauthenticated = client.head("/api/v1/pages")
    assert unauthenticated.status_code == 401
    assert unauthenticated.content == b""
    assert unauthenticated.headers["www-authenticate"] == "Bearer"


def test_openapi_uses_bearer_security_and_hides_internal_admission_headers():
    app.openapi_schema = None
    schema = client.get("/openapi.json").json()
    pages = schema["paths"]["/api/v1/pages"]["get"]
    assert pages["security"] == [{"DocPlaneBearer": []}]
    assert not any(
        parameter.get("name", "").lower() == "authorization"
        for parameter in pages.get("parameters", [])
    )
    self_issue = schema["paths"]["/api/v1/auth/self-issue"]["post"]
    assert not self_issue.get("security")
    assert not any(
        parameter.get("name") in {"X-DocPlane-Fabric-Admission", "X-DocPlane-Source-IP"}
        for parameter in self_issue.get("parameters", [])
    )


def _insert_issuances(cur, *, fingerprint: str, count: int, age_seconds: int = 0) -> None:
    metadata = psycopg2.extras.Json(
        {
            "issuance_mode": "private_fabric_self_service",
            "source_fingerprint": fingerprint,
        }
    )
    cur.execute(
        """
        INSERT INTO docplane.principals
            (principal_kind, display_name, status, metadata, created_at)
        SELECT 'AGENT',
               'rate-limit-test-' || n,
               'ACTIVE',
               %s,
               now() - make_interval(secs => %s)
          FROM generate_series(1, %s) AS n
        """,
        (metadata, age_seconds, count),
    )


def _check_rate_limit(cur, fingerprint: str) -> None:
    _rate_limit(
        cur,
        fingerprint=fingerprint,
        source_burst_limit=12,
        source_burst_window_seconds=60,
        source_limit=30,
        global_limit=300,
    )


def test_normal_agent_burst_is_admitted():
    with get_conn() as conn:
        cur = conn.cursor()
        _insert_issuances(cur, fingerprint="normal-burst", count=11)
        _check_rate_limit(cur, "normal-burst")
        conn.rollback()


def test_abusive_burst_returns_precise_structured_retry(caplog):
    with get_conn() as conn:
        cur = conn.cursor()
        _insert_issuances(cur, fingerprint="abusive-burst", count=12)
        with pytest.raises(HTTPException) as raised:
            _check_rate_limit(cur, "abusive-burst")
        conn.rollback()

    error = raised.value
    assert error.status_code == 429
    assert error.detail["code"] == "SELF_ISSUE_RATE_LIMITED"
    assert error.detail["scope"] == "source_burst"
    assert error.detail["limit"] == 12
    assert error.detail["window_seconds"] == 60
    assert 1 <= error.detail["retry_after_seconds"] <= 60
    assert error.headers["Retry-After"] == str(error.detail["retry_after_seconds"])
    assert error.headers["X-RateLimit-Limit"] == "12"
    assert error.headers["X-RateLimit-Remaining"] == "0"
    assert "event=self_issue_rate_limited scope=source_burst" in caplog.text


def test_source_recovers_when_burst_window_expires():
    with get_conn() as conn:
        cur = conn.cursor()
        _insert_issuances(
            cur,
            fingerprint="recovered-source",
            count=12,
            age_seconds=61,
        )
        _check_rate_limit(cur, "recovered-source")
        conn.rollback()


def test_rate_limit_isolated_between_observed_sources():
    with get_conn() as conn:
        cur = conn.cursor()
        _insert_issuances(cur, fingerprint="busy-neighbour", count=12)
        _check_rate_limit(cur, "independent-source")
        conn.rollback()


def test_sustained_limit_reports_its_own_scope_and_remaining_window():
    with get_conn() as conn:
        cur = conn.cursor()
        _insert_issuances(
            cur,
            fingerprint="sustained-source",
            count=30,
            age_seconds=120,
        )
        with pytest.raises(HTTPException) as raised:
            _check_rate_limit(cur, "sustained-source")
        conn.rollback()

    detail = raised.value.detail
    assert detail["scope"] == "source_sustained"
    assert detail["limit"] == 30
    assert detail["window_seconds"] == 3600
    assert 3400 <= detail["retry_after_seconds"] <= 3480
