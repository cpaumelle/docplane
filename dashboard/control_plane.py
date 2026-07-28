"""HTTP client for the authoritative DocPlane control-plane API.

The dashboard is the first-class human control surface, but it is not a second authority. Every read,
preview, mutation, certification action and reorganisation operation is executed through versioned
DocPlane endpoints using the signed-in human principal's bearer token.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class ControlPlaneError(RuntimeError):
    status_code: int
    code: str
    detail: Any

    def __str__(self) -> str:
        return f"{self.code} ({self.status_code})"


class ControlPlaneClient:
    def __init__(self, *, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or os.environ.get("DOCPLANE_API_URL", "http://docs-api:8010")).rstrip("/")
        self.timeout = timeout or float(os.environ.get("DOCPLANE_API_TIMEOUT", "10"))

    def _request(
        self,
        method: str,
        path: str,
        *,
        authorization: str | None = None,
        idempotency_key: str | None = None,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            for _k, _v in extra_headers.items():
                if _v is not None:
                    headers[_k] = _v
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.request(
                    method,
                    path,
                    headers=headers,
                    json=json_body,
                    params=params,
                )
        except httpx.HTTPError as exc:
            raise ControlPlaneError(
                status_code=503,
                code="CONTROL_PLANE_UNAVAILABLE",
                detail={"type": type(exc).__name__},
            ) from exc

        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = {"message": response.text[:1000]}
            code = "CONTROL_PLANE_REQUEST_FAILED"
            if isinstance(detail, dict):
                body_detail = detail.get("detail", detail)
                if isinstance(body_detail, dict):
                    code = str(body_detail.get("code", code))
            raise ControlPlaneError(response.status_code, code, detail)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get(
        self,
        path: str,
        *,
        authorization: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("GET", path, authorization=authorization, params=params)

    def post(
        self,
        path: str,
        *,
        authorization: str,
        idempotency_key: str | None = None,
        json_body: Any = None,
    ) -> Any:
        return self._request(
            "POST",
            path,
            authorization=authorization,
            idempotency_key=idempotency_key,
            json_body=json_body,
        )

    def discovery(self) -> dict[str, Any]:
        return self.get("/.well-known/docplane.json")

    def create_principal(self, *, display_name: str, principal_kind: str, bootstrap_token: str) -> Any:
        """Operator token issuance. The bootstrap secret is supplied per call and
        forwarded to the authoritative API; it is never stored by the dashboard."""
        return self._request(
            "POST",
            "/api/v1/bootstrap/principals",
            json_body={"display_name": display_name, "principal_kind": principal_kind},
            extra_headers={"X-DocPlane-Bootstrap-Token": bootstrap_token},
        )
