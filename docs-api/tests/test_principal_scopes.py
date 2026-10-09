import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import agent_auth
from app.agent_auth import ArtifactScope, Principal
from app.agent_models import PrincipalCreate
from app.principal_scopes import require_artifact_scope


def _principal(scopes):
    return Principal("principal-1", "AUTOMATION", "scoped", "token-1", scopes)


def _request(method: str, path: str) -> Request:
    return Request({
        "type": "http", "method": method, "path": path,
        "headers": [], "query_string": b"", "server": ("test", 443),
        "scheme": "https", "client": ("127.0.0.1", 1234),
    })


def test_scoped_principal_is_blocked_from_other_mutation_routes(monkeypatch):
    principal = _principal((ArtifactScope("trevarn", "GENERATE", "trevarn", None, "model/schema-catalogue/trevarn/"),))
    monkeypatch.setattr(agent_auth, "authenticate", lambda _authorization: principal)

    with pytest.raises(HTTPException) as denied:
        agent_auth.require_contributor(_request("POST", "/api/v1/pages"), authorization="Bearer test")
    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "PRINCIPAL_WRITE_SCOPE_DENIED"


def test_scoped_principal_can_reach_only_scoped_write_surfaces(monkeypatch):
    principal = _principal((ArtifactScope("trevarn", "OBSERVE", None, "FRESHNESS_CHECK", None),))
    monkeypatch.setattr(agent_auth, "authenticate", lambda _authorization: principal)

    assert agent_auth.require_contributor(
        _request("POST", "/api/v1/observations"), authorization="Bearer test"
    ) == principal
    with pytest.raises(HTTPException):
        agent_auth.require_contributor(
            _request("POST", "/api/v1/work/captures"), authorization="Bearer test"
        )


def test_artifact_scope_binds_operation_key_kind_and_page_prefix():
    principal = _principal((ArtifactScope("trevarn", "GENERATE", "trevarn", None, "model/schema-catalogue/trevarn/"),))
    require_artifact_scope(
        principal, "trevarn", "GENERATE",
        page_paths=["model/schema-catalogue/trevarn/index.md"],
    )
    with pytest.raises(HTTPException):
        require_artifact_scope(principal, "other", "GENERATE")
    with pytest.raises(HTTPException):
        require_artifact_scope(
            principal, "trevarn", "GENERATE",
            page_paths=["model/schema-catalogue/charliehub/index.md"],
        )


def test_legacy_principal_without_scope_rows_remains_unscoped():
    require_artifact_scope(_principal(None), "any-artifact", "GENERATE")


def test_bootstrap_scope_contract_is_explicit_and_automation_only():
    request = PrincipalCreate.model_validate({
        "display_name": "Trevarn catalogue generator",
        "principal_kind": "AUTOMATION",
        "artifact_scopes": [{
            "artifact_key": "schema-catalogue-trevarn",
            "operation": "GENERATE",
            "source_entity_key": "trevarn",
            "page_path_prefix": "model/schema-catalogue/trevarn/",
        }],
    })
    assert request.artifact_scopes[0].source_entity_key == "trevarn"
    with pytest.raises(ValueError):
        PrincipalCreate.model_validate({
            "display_name": "Too broad",
            "principal_kind": "HUMAN",
            "artifact_scopes": [{
                "artifact_key": "schema-catalogue-trevarn",
                "operation": "GENERATE",
                "source_entity_key": "trevarn",
                "page_path_prefix": "../",
            }],
        })
