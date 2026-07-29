"""Deployment-level admission policy for DocPlane credentials.

`managed` is the safe default for public or externally reachable installations.
`private_fabric` treats reachability of the trusted routed service as admission
and allows short-lived, individually attributable AGENT credentials to be minted
over HTTP. Bearer authentication remains mandatory for protected API operations.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_ACCESS_PROFILES = {"managed", "private_fabric"}
_MAX_SELF_ISSUE_TTL_SECONDS = 24 * 60 * 60

def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value

@dataclass(frozen=True)
class AccessPolicy:
    profile: str
    self_service: bool
    self_issue_ttl_seconds: int
    source_limit_per_hour: int
    global_limit_per_hour: int

def current_access_policy() -> AccessPolicy:
    profile = os.environ.get("DOCPLANE_ACCESS_PROFILE", "managed").strip().lower()
    if profile not in _ACCESS_PROFILES:
        raise RuntimeError("DOCPLANE_ACCESS_PROFILE must be one of: " + ", ".join(sorted(_ACCESS_PROFILES)))
    ttl = _bounded_int("DOCPLANE_SELF_ISSUE_TTL_SECONDS",_MAX_SELF_ISSUE_TTL_SECONDS,minimum=300,maximum=_MAX_SELF_ISSUE_TTL_SECONDS)
    return AccessPolicy(
        profile=profile,
        self_service=profile == "private_fabric",
        self_issue_ttl_seconds=ttl,
        source_limit_per_hour=_bounded_int("DOCPLANE_SELF_ISSUE_SOURCE_LIMIT_PER_HOUR",10,minimum=1,maximum=1000),
        global_limit_per_hour=_bounded_int("DOCPLANE_SELF_ISSUE_GLOBAL_LIMIT_PER_HOUR",120,minimum=1,maximum=10000),
    )
