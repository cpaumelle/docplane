"""Render the private reconciliation proposal. Templates only: no record text.

The only free text that appears is the reviewer-written manifest label and the
initiative key, both validated before use.
"""
from __future__ import annotations

from .ledger import KINDS

GROUP_MIN = 3     # this many open items of one systemic kind collapse into one scope-level item

DISCREPANCY = {
    "UNRECORDED_RESIDUAL": "A residual obligation named by the reviewed classification has no work item that explicitly covers it.",
    "STATE_CONTRADICTS_EVIDENCE": "A RESOLUTION activity exists but the initiative is not in a terminal state.",
    "UNACKNOWLEDGED_HANDOVER": "A handover has no explicit acknowledgement or accepting owner.",
    "ACCOUNTABILITY_UNCLEAR": "The initiative's owner is the promoting automation or missing, so accountability is not evidenced.",
    "EVIDENCE_EXPIRED": "Time-bound evidence has passed the point at which it may be relied on.",
    "SUPERSEDED_NOT_LINKED": "A record is superseded or corrected without an explicit reference between the records.",
    "WRONG_OWNER_DOMAIN": "The record sits under an owning workstream that does not match its subject.",
}
ACTION = {
    "UNRECORDED_RESIDUAL": "Record this residual as its own work item carrying the coverage reference below, or reject this proposal with evidence.",
    "STATE_CONTRADICTS_EVIDENCE": "Accountable owner: judge whether the RESOLUTION meets the acceptance criteria and state the result on the initiative.",
    "UNACKNOWLEDGED_HANDOVER": "Receiving owner: acknowledge the handover on the owning initiative, or name who holds it.",
    "ACCOUNTABILITY_UNCLEAR": "Coordinator: name the accountable owner for the initiative, or confirm the current one.",
    "EVIDENCE_EXPIRED": "Owner: re-verify the evidence live or record that it is no longer relied on.",
    "SUPERSEDED_NOT_LINKED": "Add an explicit reference from the later record to the earlier one.",
    "WRONG_OWNER_DOMAIN": "Coordinator: confirm or move the owning workstream.",
}
SYSTEMIC = {"ACCOUNTABILITY_UNCLEAR"}
assert set(DISCREPANCY) == set(ACTION) == set(KINDS)


def _item(key, row, view):
    meta = view["meta"].get(key, {})
    action = ACTION[row["kind"]]
    hint = meta.get("cover_hint") or []
    if row["kind"] == "UNRECORDED_RESIDUAL" and hint:
        action = ("Reviewer: if %s already carries this obligation, add the coverage reference below to it; "
                  "otherwise record it as its own work item." % hint[0])
    return {
        "identity": key, "kind": row["kind"], "original_record": row["subject_id"],
        "record_name": view["initiative_keys"].get(row["subject_id"]),
        "label": meta.get("label"), "discrepancy": DISCREPANCY[row["kind"]],
        "evidence": [{"record_id": e["record_id"], "version": e["version"][:20]} for e in row["evidence"]],
        "uncertainty": meta.get("uncertainty", []), "requested_action": action,
        "coverage_reference": meta.get("token"),
        "reopened": bool(row["decisions"]) and row["status"] == "OPEN",
    }


def build(ledger, view):
    """Structure the proposal. Open flags only become proposals when still detected."""
    proposals, groups, gone, unseen, quiet = [], [], [], [], []
    by_kind = {}
    for key in sorted(ledger):
        row = ledger[key]
        if row["status"] != "OPEN":
            quiet.append({"identity": key, "status": row["status"], "detection": row["detection"]})
        elif row["detection"] == "DETECTED":
            by_kind.setdefault(row["kind"], []).append(_item(key, row, view))
        elif row["detection"] == "NOT_DETECTED":
            gone.append({"identity": key, "kind": row["kind"], "original_record": row["subject_id"]})
        else:
            unseen.append({"identity": key, "kind": row["kind"], "original_record": row["subject_id"],
                           "reason": view["excluded"].get(row["subject_id"], "NOT_IN_THIS_RUN")})
    for kind, items in sorted(by_kind.items()):
        if kind in SYSTEMIC and len(items) >= GROUP_MIN:
            groups.append({"kind": kind, "discrepancy": DISCREPANCY[kind], "requested_action": ACTION[kind],
                           "uncertainty": items[0]["uncertainty"], "affected": [
                               {"original_record": i["original_record"], "record_name": i["record_name"],
                                "identity": i["identity"], "evidence": i["evidence"]} for i in items]})
        else:
            proposals.extend(items)
    return {"source": view["source"], "scope": {"initiatives": view["scope_size"], "complete": view["scope_complete"],
                                                "failed_reads": view["failed_initiatives"]},
            "excluded": view["excluded"], "proposals": proposals, "systemic": groups,
            "no_longer_detected": gone, "not_reevaluated": unseen, "unchanged_decisions": quiet,
            "covered_obligations": len(view["covered"])}


def render(proposal):
    src = proposal["source"]
    out = ["# WORK reconciliation proposal (private, read-only)", "",
           "Nothing was written to WORK or DocPlane. Resolving or rejecting a proposal never completes, "
           "transitions or assigns the underlying work.", ""]
    if src.get("live"):
        out.append("Source: **%s**. Findings are as read now; external facts (PRs, hosts, services) are not verified." % src["mode"])
    else:
        out.append("Source: **snapshot as of %s - historical**. Verify live before acting on any finding." % src.get("as_of"))
    sc = proposal["scope"]
    out.append("Scope: %d initiatives, read %s." % (sc["initiatives"], "completely" if sc["complete"] else
               "INCOMPLETE (%d failed) - absences below are not established" % len(sc["failed_reads"])))
    out += ["", "Open proposals: %d | systemic groups: %d | no longer detected: %d | not re-evaluated: %d | "
            "decisions unchanged: %d" % (len(proposal["proposals"]), len(proposal["systemic"]),
            len(proposal["no_longer_detected"]), len(proposal["not_reevaluated"]), len(proposal["unchanged_decisions"])), ""]
    for n, p in enumerate(proposal["proposals"], 1):
        out += ["## P%d %s%s" % (n, p["kind"], " - REOPENED (evidence changed since a prior decision)" if p["reopened"] else ""),
                "- Original record: `%s`%s" % (p["original_record"], " (%s)" % p["record_name"] if p["record_name"] else ""),
                "- Discrepancy: %s%s" % (p["discrepancy"], " Item: %s" % p["label"] if p["label"] and p["kind"] != "STATE_CONTRADICTS_EVIDENCE" else ""),
                "- Evidence: " + ", ".join("`%s`@%s" % (e["record_id"], e["version"]) for e in p["evidence"]),
                "- Uncertainty: " + " ".join(p["uncertainty"]),
                "- Requested action: " + p["requested_action"]]
        if p["coverage_reference"]:
            out.append("- Coverage reference (ARTIFACT): `%s`" % p["coverage_reference"])
        out.append("- Identity: `%s`" % p["identity"])
        out.append("")
    for g in proposal["systemic"]:
        out += ["## SYSTEMIC %s (%d records)" % (g["kind"], len(g["affected"])), "- Discrepancy: " + g["discrepancy"],
                "- Uncertainty: " + " ".join(g["uncertainty"]), "- Requested action (one decision for the scope): " + g["requested_action"],
                "- Affected records:"]
        out += ["  - `%s`%s" % (a["original_record"], " (%s)" % a["record_name"] if a["record_name"] else "") for a in g["affected"]]
        out.append("")
    if proposal["no_longer_detected"]:
        out += ["## No longer detected (still OPEN - a reviewer must confirm; absence is not proof)"]
        out += ["- `%s` %s on `%s`" % (x["identity"], x["kind"], x["original_record"]) for x in proposal["no_longer_detected"]] + [""]
    if proposal["not_reevaluated"] or proposal["excluded"]:
        out += ["## Not re-evaluated (read failed, was withheld, or was incomplete; existing flags unchanged)"]
        out += ["- `%s`: %s" % (k, v) for k, v in sorted(proposal["excluded"].items())] + [""]
    return "\n".join(out).rstrip() + "\n"
