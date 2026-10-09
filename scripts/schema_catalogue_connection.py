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
        if sslmode == "disable" and not (
            os.environ.get("CATALOGUE_ENVIRONMENT", "").strip() == "development"
            and os.environ.get("CATALOGUE_SOURCE_IDENTITY", "").strip() == "VM1124/trevarn"
            and os.environ.get("CATALOGUE_SOURCE_DOCKER_NETWORK_VERIFIED", "").strip() == "trevarn-net"
        ):
            raise RuntimeError(
                "CATALOGUE_SOURCE_SSLMODE=disable is restricted to the VM1124 Trevarn Docker bridge"
            )
        params["sslmode"] = sslmode
    return params


def _required_setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for the discovered source")
    return value
