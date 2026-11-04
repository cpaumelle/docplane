"""Build source connection arguments without serializing credentials into a DSN."""
from __future__ import annotations

import os
import ipaddress

import psycopg2.extensions

from secret_source import read_secret


def source_connection_parameters() -> dict[str, str]:
    """Return psycopg2 keyword arguments for the discovered source.

    Host-side wrappers set CATALOGUE_SOURCE_HOST after Docker discovery. In that
    path the password is resolved from the established secret-source contract
    and remains a value in this process only. Direct legacy callers without a
    discovered host retain the old DSN environment/file behavior.
    """
    host = os.environ.get("CATALOGUE_SOURCE_HOST", "").strip()
    if not host:
        dsn = read_secret("CATALOGUE_SOURCE_DSN")
        try:
            legacy_sslmode = psycopg2.extensions.parse_dsn(dsn).get("sslmode", "")
        except Exception as exc:
            raise RuntimeError("CATALOGUE_SOURCE_DSN is invalid") from exc
        if legacy_sslmode == "disable" or os.environ.get("PGSSLMODE", "").strip() == "disable":
            raise RuntimeError("sslmode=disable requires the verified VM1124 Trevarn Docker bridge")
        return {"dsn": dsn}
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError as exc:
        raise RuntimeError("CATALOGUE_SOURCE_HOST must be a discovered IP address") from exc

    dbname = _required_setting("CATALOGUE_SOURCE_DB")
    user = _required_setting("CATALOGUE_SOURCE_USER")
    port_value = _required_setting("CATALOGUE_SOURCE_PORT")
    try:
        port = int(port_value)
    except ValueError as exc:
        raise RuntimeError("CATALOGUE_SOURCE_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("CATALOGUE_SOURCE_PORT must be between 1 and 65535")

    params = {
        "host": host,
        "dbname": dbname,
        "user": user,
        "password": read_secret("CATALOGUE_SOURCE_PASSWORD"),
        "port": str(port),
    }
    sslmode = os.environ.get("CATALOGUE_SOURCE_SSLMODE", "").strip()
    if not sslmode:
        sslmode = os.environ.get("PGSSLMODE", "").strip()
    if sslmode:
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise RuntimeError("CATALOGUE_SOURCE_SSLMODE is not a supported libpq mode")
        if sslmode == "disable" and not plaintext_bridge_allowed(
            os.environ.get("CATALOGUE_ENVIRONMENT", ""),
            os.environ.get("CATALOGUE_SOURCE_IDENTITY", ""),
            os.environ.get("CATALOGUE_SOURCE_DOCKER_NETWORK_VERIFIED", ""),
        ):
            raise RuntimeError(
                "CATALOGUE_SOURCE_SSLMODE=disable is restricted to an allowlisted Trevarn Docker bridge"
            )
        params["sslmode"] = sslmode
    return params


# Plaintext (sslmode=disable) is allowed only on a wrapper-verified, unpublished Docker
# bridge whose (environment, source identity) pair is listed here. The wrapper verifies
# the network name against the live container and exports it; this table must stay in
# step with plaintext_bridge_network() in both wrappers. Both Trevarn PostgreSQL
# containers run without server TLS and publish no port; the bridge hop is host-local.
_PLAINTEXT_BRIDGES = (
    ("development", "VM1124/trevarn", "trevarn-net"),
    ("production", "VPS3/trevarn", "trevarn-net"),
)


def plaintext_bridge_network(environment: str, identity: str) -> str | None:
    """The Docker network a plaintext source must sit on, or None when not allowlisted."""
    for allowed_environment, allowed_identity, network in _PLAINTEXT_BRIDGES:
        if environment.strip() == allowed_environment and identity.strip() == allowed_identity:
            return network
    return None


def plaintext_bridge_allowed(environment: str, identity: str, verified_network: str) -> bool:
    expected = plaintext_bridge_network(environment, identity)
    return expected is not None and verified_network.strip() == expected


def _required_setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for the discovered source")
    return value
