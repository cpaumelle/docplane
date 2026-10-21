"""Read-only reconciliation pass: governed reads -> ledger candidates -> view.

Record text is data. Nothing here parses, matches or executes the body of a
capture or activity: obligations come from a reviewed manifest, coverage comes
from structured references, and mechanical checks use state fields only.
"""
from __future__ import annotations

import re
from uuid import UUID, uuid5

from .ledger import KINDS, projection_version, reconcile, record_id
from .sources import ReadFailure

NAMESPACE = UUID("6f0a1d3e-52c4-4b8e-9a77-0d2c3b1e8a41")  # fixed: obligation ids never depend on wording
TERMINAL = frozenset({"COMPLETE", "ABANDONED"})
MANIFEST_KEYS = {"subject_id", "obligation_id", "kind", "label"}
MANIFEST_OPTIONAL = {"evidence_ids", "subject_activity_ids", "candidate_cover_ids"}
SECRET_SHAPES = re.compile(r"(?i)bearer\s|token\s*[=:]|password|secret\s*[=:]|[A-Za-z0-9+/_-]{32,}")


def derived_obligation(subject_id, discriminator):
    return str(uuid5(NAMESPACE, f"{subject_id}:{discriminator}"))


def check_manifest(manifest):
    if set(manifest) != {"scope_initiatives", "obligations"}:
        raise ValueError("manifest takes scope_initiatives and obligations only")
    scope = [record_id(x) for x in manifest["scope_initiatives"]]
    if len(set(scope)) != len(scope):
        raise ValueError("duplicate scope initiative")
    seen, rows = set(), []
    for row in manifest["obligations"]:
        keys = set(row)
        if not MANIFEST_KEYS <= keys <= MANIFEST_KEYS | MANIFEST_OPTIONAL:
            raise ValueError("unexpected manifest fields")
        if row["kind"] not in KINDS:
            raise ValueError("unknown discrepancy kind")
        label = row["label"]
        if (not isinstance(label, str) or not 1 <= len(label) <= 160 or not label.isprintable()
                or SECRET_SHAPES.search(label)):
            raise ValueError("label must be a short reviewer-written plain phrase")
        out = {"subject_id": record_id(row["subject_id"]), "obligation_id": record_id(row["obligation_id"]),
               "kind": row["kind"], "label": label}
        for name in MANIFEST_OPTIONAL:
            out[name] = sorted({record_id(x) for x in row.get(name, [])})
        if (out["subject_id"], out["obligation_id"]) in seen or out["obligation_id"] in {r["obligation_id"] for r in rows}:
            raise ValueError("obligation ids are assigned once and must be unique")
        seen.add((out["subject_id"], out["obligation_id"]))
        rows.append(out)
    return scope, rows


def _capture_version(cap):
    return projection_version(record="capture", created_at=cap.get("created_at"),
                              capture_kind=cap.get("kind"), author=cap.get("author_principal_id"))


def _read_capture(reads, capture_id):
    """Return (version, None) or (None, reason). A withheld record is not a clean read."""
    try:
        cap = reads.capture(capture_id)
    except ReadFailure as exc:
        return None, exc.code
    if cap.get("capture_id") != capture_id:
        return None, "IDENTITY_MISMATCH"
    if cap.get("body_withheld") or cap.get("origin_text_withheld"):
        return None, "WITHHELD"
    if not cap.get("created_at"):
        return None, "MALFORMED_RESULT"
    return _capture_version(cap), None


def _relationships(initiatives):
    """Index structured relationships only: (initiative, state, activity_id, link ids).

    Link ids are an activity's ``source_capture_id``, its ``ACTIVITY`` references and
    the initiative's founding captures. No reference type names an *obligation*, so
    a relationship shows related work, never that a particular obligation is covered.
    Returns (entries, blind_ids); blind ids hold a withheld reference.
    """
    entries, blind = [], set()
    for iid, ini in initiatives.items():
        founding = {x for x in ini.get("founding_capture_ids") or [] if isinstance(x, str)}
        for act in ini["activities"]:
            links = set(founding)
            if isinstance(act.get("source_capture_id"), str):
                links.add(act["source_capture_id"])
            for ref in act.get("references") or []:
                if ref.get("reference_id_withheld"):
                    blind.add(iid)
                elif ref.get("reference_type") == "ACTIVITY":
                    links.add(ref.get("reference_id"))
            if links:
                entries.append((iid, ini.get("work_state"), act.get("activity_id"), links))
    return entries, blind


def _after_resolution(activities, resolution_ids):
    ordered = sorted(activities, key=lambda a: (a.get("created_at") or "", a["activity_id"]))
    last = max(i for i, a in enumerate(ordered) if a["activity_id"] in resolution_ids)
    later = len(ordered) - 1 - last
    return ("The newest RESOLUTION is the latest activity." if not later else
            "%d activit%s follow the newest RESOLUTION, so work may have continued; a RESOLUTION can mark a partial outcome." % (later, "y" if later == 1 else "ies"))


def run(reads, manifest, previous):
    scope_ids, obligations = check_manifest(manifest)
    initiatives, failed = {}, {}
    for iid in scope_ids:
        try:
            initiatives[iid] = reads.initiative(iid)
        except ReadFailure as exc:
            failed[iid] = exc.code
    entries, blind = _relationships(initiatives)
    scope_complete = not failed and not blind

    candidates, meta, excluded = [], {}, {}
    by_subject = {}
    for row in obligations:
        by_subject.setdefault(row["subject_id"], []).append(row)

    evaluated = set()
    for subject, rows in by_subject.items():
        versions, reason = {}, None
        wanted = {subject} | {e for r in rows for e in r["evidence_ids"]}
        for cid in sorted(wanted):
            versions[cid], why = _read_capture(reads, cid)
            reason = reason or why
        if reason is None and not scope_complete:
            reason = "SCOPE_INCOMPLETE"       # the related-work list would be partial
        if reason:
            excluded[subject] = reason
            continue
        evaluated.add(subject)
        for row in rows:
            own = set(row["subject_activity_ids"])
            link_ids = {subject} | own
            related = sorted({(e[0], e[1]) for e in entries if e[3] & link_ids and e[2] not in own})
            evidence = [{"record_id": c, "version": versions[c]} for c in sorted(wanted)]
            uncertainty = ["Coverage is unconfirmed, not disproved: the governed reads have no relationship that names an obligation, "
                           "and only the %d scoped initiatives were searched." % len(scope_ids)]
            if related:
                uncertainty.append("Related work found by relationship only; it may cover this obligation, a sibling one, or neither.")
            candidates.append({"subject_id": subject, "kind": row["kind"], "obligation_id": row["obligation_id"], "evidence": evidence})
            meta[f"{subject}:{row['kind']}:{row['obligation_id']}"] = {
                "label": row["label"], "uncertainty": uncertainty, "cover_hint": row["candidate_cover_ids"],
                "related": [{"initiative_id": i, "work_state": st, "initiative_key": initiatives[i].get("initiative_key")} for i, st in related],
                "origin": "manifest"}

    # Mechanical checks on state fields; each initiative stands or falls on its own read.
    for iid, ini in initiatives.items():
        if "owner" not in ini:
            excluded[iid] = "OWNER_NOT_EXPOSED"     # absent field is not "no owner"
            continue
        evaluated.add(iid)
        state = ini.get("work_state")
        resolutions = sorted(a["activity_id"] for a in ini["activities"] if a.get("activity_type") == "RESOLUTION")
        if resolutions and state not in TERMINAL:
            ev = [{"record_id": iid, "version": projection_version(record="initiative-state", work_state=state, resolutions=",".join(resolutions))}]
            ev += [{"record_id": a["activity_id"], "version": projection_version(record="activity", activity_type="RESOLUTION", created_at=a.get("created_at"))}
                   for a in ini["activities"] if a["activity_id"] in resolutions]
            ob = derived_obligation(iid, "resolution-not-complete")
            candidates.append({"subject_id": iid, "kind": "STATE_CONTRADICTS_EVIDENCE", "obligation_id": ob, "evidence": ev})
            meta[f"{iid}:STATE_CONTRADICTS_EVIDENCE:{ob}"] = {
                "label": ini.get("initiative_key", ""), "origin": "state",
                "uncertainty": ["A RESOLUTION activity exists; whether it meets the acceptance criteria is a judgment this worker does not make.",
                                _after_resolution(ini["activities"], resolutions)]}
        owner = ini["owner"] or {}
        if not owner or owner.get("principal_kind") == "AUTOMATION":
            ev = [{"record_id": iid, "version": projection_version(record="owner", principal=owner.get("principal_id"), principal_kind=owner.get("principal_kind"))}]
            ob = derived_obligation(iid, "no-accountable-owner")
            candidates.append({"subject_id": iid, "kind": "ACCOUNTABILITY_UNCLEAR", "obligation_id": ob, "evidence": ev})
            meta[f"{iid}:ACCOUNTABILITY_UNCLEAR:{ob}"] = {
                "label": ini.get("initiative_key", ""), "origin": "owner",
                "uncertainty": ["The owner field records who promoted the item, not who is accountable; an assignment may exist outside this read."]}
    ledger = reconcile(previous, candidates, evaluated_subjects=sorted(evaluated))
    names = {iid: ini.get("initiative_key") for iid, ini in initiatives.items()}
    return ledger, {"source": reads.describe(), "meta": meta, "excluded": excluded, "failed_initiatives": failed,
                    "scope_size": len(scope_ids), "scope_complete": scope_complete,
                    "initiative_keys": names, "evaluated": sorted(evaluated)}
