"""Deterministic exact-text patches with hard ambiguity and overlap guards."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class PatchResult:
    content: str
    edits_applied: int
    occurrences_replaced: int
    before_sha256: str
    after_sha256: str
    impacts: tuple[dict, ...]


def apply_text_patch(content: str, edits: list[dict]) -> PatchResult:
    """Apply edits against one immutable input, rejecting ambiguity and overlap."""
    spans: list[tuple[int, int, str, int]] = []
    impacts: list[dict] = []
    for index, edit in enumerate(edits):
        old = str(edit["old_text"])
        new = str(edit["new_text"])
        expected = int(edit.get("expected_occurrences", 1))
        starts: list[int] = []
        cursor = 0
        while True:
            found = content.find(old, cursor)
            if found < 0:
                break
            starts.append(found)
            cursor = found + len(old)
        if len(starts) != expected:
            raise ValueError(f"PATCH_OCCURRENCE_MISMATCH:{index}:{expected}:{len(starts)}")
        for start in starts:
            spans.append((start, start + len(old), new, index))
        impacts.append({
            "edit_index": index,
            "expected_occurrences": expected,
            "matched_occurrences": len(starts),
            "old_sha256": hashlib.sha256(old.encode("utf-8")).hexdigest(),
            "new_sha256": hashlib.sha256(new.encode("utf-8")).hexdigest(),
        })

    ordered = sorted(spans, key=lambda item: (item[0], item[1]))
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise ValueError(f"PATCH_EDITS_OVERLAP:{previous[3]}:{current[3]}")

    result = content
    for start, end, replacement, _index in reversed(ordered):
        result = result[:start] + replacement + result[end:]
    if result == content:
        raise ValueError("PATCH_NO_CHANGE")
    return PatchResult(
        content=result,
        edits_applied=len(edits),
        occurrences_replaced=len(spans),
        before_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        after_sha256=hashlib.sha256(result.encode("utf-8")).hexdigest(),
        impacts=tuple(impacts),
    )
