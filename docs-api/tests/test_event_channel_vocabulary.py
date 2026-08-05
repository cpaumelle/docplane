"""Every event channel the API emits must be in the closed vocabulary.

The channel vocabulary is enforced in two places that can drift apart from
the call sites: the genesis CHECK constraint on docplane.events and the
EventIngest.channel Literal. Neither is exercised by a unit test that never
reaches PostgreSQL, so a call site naming an unlisted channel type-checks,
imports, passes CI, and then fails at runtime with CheckViolation inside the
transaction it was supposed to audit — losing the whole operation.

That is exactly how MODEL_ARTIFACT_CUSTODY_REASSIGNED shipped with
channel="BOOTSTRAP" and rolled back on first production use. This test reads
the vocabulary from both authorities and asserts every literal call site
agrees with them.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "docs-api" / "app"
GENESIS = ROOT / "db" / "migrations" / "000_docplane_genesis.sql"
EVENT_MODELS = APP / "event_models.py"


def _genesis_channels() -> set[str]:
    match = re.search(
        r"channel text NOT NULL CHECK \(channel IN \(([^)]*)\)\)",
        GENESIS.read_text(encoding="utf-8"),
    )
    assert match, "the genesis channel CHECK constraint moved or changed shape"
    return set(re.findall(r"'([A-Z_]+)'", match.group(1)))


def _model_channels() -> set[str]:
    match = re.search(r"channel: Literal\[([^\]]*)\]", EVENT_MODELS.read_text(encoding="utf-8"))
    assert match, "EventIngest.channel moved or changed shape"
    return set(re.findall(r'"([A-Z_]+)"', match.group(1)))


def test_the_two_channel_authorities_agree():
    assert _genesis_channels() == _model_channels()


def test_every_emitted_channel_is_in_the_vocabulary():
    allowed = _genesis_channels()
    offenders: list[str] = []
    for source in sorted(APP.glob("*.py")):
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            for channel in re.findall(r'channel\s*=\s*"([A-Z_]+)"', line):
                if channel not in allowed:
                    offenders.append(f"{source.name}:{line_number} channel={channel}")
    assert not offenders, (
        "these call sites would fail events_channel_check at runtime: "
        + ", ".join(offenders)
        + f" (allowed: {sorted(allowed)})"
    )
