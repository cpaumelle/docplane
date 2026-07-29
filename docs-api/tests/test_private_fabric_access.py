from __future__ import annotations

from fastapi.testclient import TestClient

from app.application import app

client = TestClient(app)


def test_managed_profile_is_fail_closed_and_operator_issued(monkeypatch):
    monkeypatch.setenv("DOCPLANE_ACCESS_PROFILE", "managed")
    discovery = client.get("/.well-known/docplane.json").json()
    acquisition = discovery["authentication"]["token_acquisition"]
    assert discovery["contract_version"] == "docplane-agent-discovery-v3"
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
    assert discovery["surfaces"]["self_issue"] == "/api/v1/auth/self-issue"
    assert discovery["quick_start"]["authenticate"][0] == "POST /api/v1/auth/self-issue"


def test_private_fabric_profile_does_not_trust_direct_api_reachability(monkeypatch):
    monkeypatch.setenv("DOCPLANE_ACCESS_PROFILE", "private_fabric")
    response = client.post("/api/v1/auth/self-issue", json={"display_name": "direct-agent"})
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "FABRIC_ADMISSION_REQUIRED"
    assert "routed" in detail["remedy"]


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
    assert not any(parameter.get("name", "").lower() == "authorization" for parameter in pages.get("parameters", []))
    self_issue = schema["paths"]["/api/v1/auth/self-issue"]["post"]
    assert not self_issue.get("security")
    assert not any(parameter.get("name") in {"X-DocPlane-Fabric-Admission", "X-DocPlane-Source-IP"} for parameter in self_issue.get("parameters", []))
