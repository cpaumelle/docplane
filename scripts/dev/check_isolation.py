#!/usr/bin/env python3
"""Assert the merged dev Compose model is isolated (stdin: `compose config --format json`)."""
import json, sys
project = sys.argv[1]
cfg = json.load(sys.stdin)
errors = []
if cfg.get("name") != project:
    errors.append(f"project name {cfg.get('name')!r} != {project!r}")
for name, svc in cfg["services"].items():
    for port in svc.get("ports", []) or []:
        if port.get("host_ip") != "127.0.0.1":
            errors.append(f"{name}: published port {port.get('published')} binds {port.get('host_ip') or '0.0.0.0'}")
    if svc.get("container_name"):
        errors.append(f"{name}: fixed container_name {svc['container_name']} defeats project isolation")
    if svc.get("network_mode") == "host":
        errors.append(f"{name}: host networking")
if cfg["services"]["postgres"].get("ports"):
    errors.append("postgres publishes a port")
env = cfg["services"]["docs-api"]["environment"]
if env.get("DOCPLANE_PUBLICATION_WEBHOOKS"):
    errors.append("publication webhooks configured")
if env.get("DOCPLANE_ACCESS_PROFILE") != "managed":
    errors.append("access profile is not managed")
if "DEV" not in (env.get("DOCPLANE_SITE_NAME") or ""):
    errors.append("site identity is not marked DEV")
for vol in cfg.get("volumes", {}).values():
    if not vol.get("name", "").startswith(project + "_"):
        errors.append(f"volume {vol.get('name')} is not project-scoped")
for net in cfg.get("networks", {}).values():
    if not net.get("name", "").startswith(project + "_"):
        errors.append(f"network {net.get('name')} is not project-scoped")
if errors:
    print("ISOLATION CHECK FAILED:\n  " + "\n  ".join(errors)); sys.exit(1)
ports = sorted(f"{n}:{p['host_ip']}:{p['published']}" for n, s in cfg["services"].items() for p in s.get("ports", []) or [])
print("isolation ok: " + ", ".join(ports))
