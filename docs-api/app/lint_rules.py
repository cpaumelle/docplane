"""Docs lint rules + pure matcher (extracted from main.py so it is unit-testable).

Each rule: (id, severity, regex, exclude_path_prefixes, message).
exclude_path_prefixes: None, a str, or a tuple of strs — pages whose path starts with any
are skipped (history pages: change-log, archive, doctrine counter-examples).
`lint_findings` is pure (stdlib `re` only) so `GET /api/docs/lint` stays a thin DB wrapper.
"""
import re

_LINT_RULES = [
    # Shell commands that would directly modify generated files (not prose warnings).
    # Exclude doctrine/standards/troubleshooting pages that intentionally show counter-examples.
    ("forbidden-edit-routes",  "error",
     r"(vi|vim|nano|emacs|sed -i)\s+\S*routes\.yml",
     ("control-plane/doctrine", "standards/03-common-patterns", "agent-guides/troubleshooting"),
     "Shell command editing generated routes.yml directly"),
    ("forbidden-edit-md",      "error",
     r"(vi|vim|nano|emacs|sed -i)\s+\S*/docs/\S+\.md",
     ("control-plane/doctrine", "standards/03-common-patterns", "agent-guides/troubleshooting"),
     "Shell command editing generated .md files directly"),

    # Wrong CIDR — skip the change-log which legitimately records history
    ("stale-cidr-24",          "error",
     r"10\.(44|35)\.0\.0/24",
     "control-plane/change-log", "Stale /24 CIDR — should be /16"),

    # Lifecycle contradiction
    ("lifecycle-terminal",     "error",
     r"[Dd]isable is terminal",
     None, "Contradicts lifecycle invariant — disabled→active is valid"),

    # CT1112/CT1113 in active ops pages only (not archive, not legacy service docs)
    ("stale-ct-ops",           "warning",
     r"CT1112|CT1113",
     "archive/",  "Decommissioned container reference — confirm DECOMMISSIONED banner present"),

    # Placeholders
    ("todo-placeholder",       "warning",
     r"TODO|FIXME|\bTBD\b|Add more.*as needed",
     None, "Placeholder content — replace or remove"),

    # --- Reality-drift rules (2026-07 audit) — mechanical docs-vs-reality checks. Narrow/
    # high-signal on purpose; the corpus-wide semantic fan-out catches the rest (see
    # DOCS-REALITY-AUDIT-HANDOFF.md). change-log/archive excluded (they record history). ---
    # Traefik router API is NOT on :8091 (edge-agent port). See INGRESS-DRIFT-BLIND-1.
    ("stale-traefik-api-port", "error",
     r"8091/api/http|localhost:8091/api",
     "control-plane/change-log",
     "Traefik router API on :8091 is wrong (edge-agent port) — use the internal API entrypoint"),
    # nav-manager SPA was retired (dead code, deleted).
    ("retired-nav-manager",    "warning",
     r"nav-manager",
     ("services/docs-api/nav-manager", "archive/", "control-plane/change-log"),
     "nav-manager was retired — remove/redirect the reference"),
    # HSTS is per-domain (public-cert-only), NOT a global/entrypoint floor.
    ("stale-hsts-global",      "warning",
     r"(?:global|globally|entrypoint|every route|all (?:HTTPS )?routes)[^\n]{0,40}(?:HSTS|Strict-Transport-Security)"
     r"|(?:HSTS|Strict-Transport-Security)[^\n]{0,40}(?:global|globally|entrypoint|every route|all (?:HTTPS )?routes)",
     "control-plane/change-log",
     "HSTS is per-domain (public-cert-only), not a global/entrypoint floor"),
    # /api/agent-config is anonymous by design (fabric-internal); not app-auth'd.
    ("stale-agent-config-auth", "warning",
     r"agent-config[^\n]{0,40}requires?[^\n]{0,20}(?:auth|X-API-Key|key)",
     "control-plane/change-log",
     "/api/agent-config is anonymous by design (fabric-internal) — do not claim it requires a key"),
    # router_mismatch is a COUNT (host-set coverage), not a 0/1 boolean.
    ("stale-router-mismatch",  "warning",
     r"router_mismatch[^\n]{0,40}(?:1 if|0 if|boolean|0 or 1)",
     "control-plane/change-log",
     "router_mismatch is a count (host-set coverage), not a 0/1 boolean"),
    # The lifecycle validator is LIVE (GET /api/docs/lifecycle/check), not planned.
    ("stale-lifecycle-planned", "warning",
     r"lifecycle.{0,15}(?:validator|check)[^\n]{0,30}(?:planned|not (?:yet )?(?:implemented|deployed|live|available))",
     "control-plane/change-log",
     "The lifecycle validator is live (GET /api/docs/lifecycle/check) — no longer planned"),
]


def lint_findings(pages, rules=_LINT_RULES):
    """Pure: given [(path, content), ...] return (errors, warnings)."""
    errors, warnings = [], []
    for path, content in pages:
        for rule_id, severity, pattern, exclude_prefixes, message in rules:
            if exclude_prefixes:
                prefixes = (exclude_prefixes,) if isinstance(exclude_prefixes, str) else exclude_prefixes
                if any(path.startswith(p) for p in prefixes):
                    continue
            matches = re.findall(pattern, content or "", re.IGNORECASE)
            if matches:
                item = {"path": path, "rule": rule_id, "message": message, "occurrences": len(matches)}
                (errors if severity == "error" else warnings).append(item)
    return errors, warnings
