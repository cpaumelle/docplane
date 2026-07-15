"""Unit tests for the docs lint rules — especially the 2026-07 reality-drift rules.

Pure — imports app/lint_rules.py directly (stdlib-only). Confirms each reality-drift rule
MATCHES its drift and does NOT fire on benign text (no crying wolf), and that exclusions work.
Run:  python3 -m pytest docs-api/tests/test_lint_rules.py -q
   or: python3 docs-api/tests/test_lint_rules.py
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "lint_rules", os.path.join(os.path.dirname(__file__), "..", "app", "lint_rules.py"))
lint_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint_rules)


def _rules_hit(path, content):
    e, w = lint_rules.lint_findings([(path, content)])
    return {f["rule"] for f in e + w}


# --- reality-drift rules FIRE on their drift -------------------------------------------
def test_traefik_8091_fires():
    assert "stale-traefik-api-port" in _rules_hit(
        "operations/x.md", "curl http://localhost:8091/api/http/routers")

def test_nav_manager_fires():
    assert "retired-nav-manager" in _rules_hit("services/x.md", "open the nav-manager SPA")

def test_hsts_global_fires():
    assert "stale-hsts-global" in _rules_hit(
        "network/x.md", "HSTS is applied at the entrypoint for every route")

def test_agent_config_auth_fires():
    assert "stale-agent-config-auth" in _rules_hit(
        "agent-guides/x.md", "the /api/agent-config endpoint requires an X-API-Key")

def test_router_mismatch_binary_fires():
    assert "stale-router-mismatch" in _rules_hit(
        "control-plane/x.md", "router_mismatch is 1 if mismatch, 0 if synchronized")

def test_lifecycle_planned_fires():
    assert "stale-lifecycle-planned" in _rules_hit(
        "agent-guides/x.md", "the lifecycle validator is planned but not yet deployed")


# --- and DO NOT fire on benign / correct text (no crying wolf) -------------------------
def test_benign_traefik_edge_agent_8091_ok():
    # :8091 for the edge-agent (not the Traefik router API) must NOT fire.
    assert "stale-traefik-api-port" not in _rules_hit(
        "network/x.md", "the edge-agent listens on :8091 for control")

def test_benign_hsts_per_domain_ok():
    assert "stale-hsts-global" not in _rules_hit(
        "network/x.md", "HSTS is applied per-domain to public-cert routes only")

def test_benign_agent_config_correct_ok():
    assert "stale-agent-config-auth" not in _rules_hit(
        "agent-guides/x.md", "/api/agent-config is anonymous (fabric-internal); other endpoints need a key")

def test_benign_router_mismatch_count_ok():
    assert "stale-router-mismatch" not in _rules_hit(
        "control-plane/x.md", "router_mismatch is a count of uncovered+orphan hosts")


# --- exclusions honoured ---------------------------------------------------------------
def test_change_log_is_excluded():
    # change-log records history verbatim — reality-drift rules must skip it.
    hits = _rules_hit("control-plane/change-log/2026.md", "we removed the nav-manager SPA")
    assert "retired-nav-manager" not in hits


# --- existing rules still work (extraction preserved behavior) -------------------------
def test_existing_stale_cidr_still_fires():
    assert "stale-cidr-24" in _rules_hit("network/x.md", "the site uses 10.44.0.0/24")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("PASS", fn.__name__)
    print(f"--- ALL {len(fns)} PASS ---")
