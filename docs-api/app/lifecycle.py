"""Docs lifecycle validation (F7.2).

Pure, dependency-light (stdlib `re` only) so it is trivially unit-testable and the
`/api/docs/lifecycle/check` endpoint in main.py stays a thin DB wrapper around it.

Every page declares its lifecycle twice: a human-readable `**Lifecycle:** <STATE>` field
and a machine `<!-- lifecycle: <STATE> -->` marker comment. The validator checks presence,
field/comment agreement, and (advisory) membership in the known state set.
"""
import re

# The canonical docs lifecycle model — exactly the six states of I-DOCS-LIFECYCLE-1
# (agent-guides/docs-api.md, model v1.0 frozen 2026-06-30). Unknown states are only a
# WARNING (never an error), so this set can never false-FAIL a page — but it must match
# the real model or it false-WARNs every legitimately-classified page (the earlier
# {DRAFT,CURRENT,DEPRECATED,RETIRED} guess did exactly that: 142 spurious warnings).
VALID_LIFECYCLE_STATES = {"OPERATION", "ACTIVE", "PAUSED", "BACKLOG",
                          "REFERENCE", "ARCHIVED"}

_FIELD_RE = re.compile(r"\*\*Lifecycle:\*\*\s*([A-Za-z][A-Za-z-]*)")
_COMMENT_RE = re.compile(r"<!--\s*lifecycle:\s*([A-Za-z][A-Za-z-]*)\s*-->")


def lifecycle_findings(pages, valid_states=VALID_LIFECYCLE_STATES):
    """Pure: given [(path, content), ...] return (errors, warnings).

    ERROR   lifecycle-missing      — neither the field nor the marker comment is present.
    ERROR   lifecycle-inconsistent — field and comment both present but disagree.
    WARNING lifecycle-unknown      — declared state not in valid_states (advisory).
    """
    errors, warnings = [], []
    for path, content in pages:
        fm = _FIELD_RE.search(content or "")
        cm = _COMMENT_RE.search(content or "")
        field = fm.group(1).strip().upper() if fm else None
        comment = cm.group(1).strip().upper() if cm else None
        if not field and not comment:
            errors.append({"path": path, "rule": "lifecycle-missing",
                           "message": "no **Lifecycle:** field or <!-- lifecycle: --> marker"})
            continue
        if field and comment and field != comment:
            errors.append({"path": path, "rule": "lifecycle-inconsistent",
                           "message": f"field '{field}' != marker '{comment}'"})
        state = field or comment
        if state not in valid_states:
            warnings.append({"path": path, "rule": "lifecycle-unknown",
                             "message": f"unrecognized lifecycle state '{state}'"})
    return errors, warnings
