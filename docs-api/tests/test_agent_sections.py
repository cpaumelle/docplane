from app import agent_sections


CONTENT = """# Service Guide

Intro paragraph for agents.

## Overview {#overview}

Current architecture.

### Detail

More detail.

```markdown
## Not a heading {#ignored}
```

## Rollback {#rollback}

Rollback procedure.
"""


def test_outline_is_deterministic_and_code_aware():
    result = agent_sections.outline(CONTENT)
    assert [item["heading_id"] for item in result] == [
        "service-guide",
        "overview",
        "detail",
        "rollback",
    ]
    assert all(item["heading_id"] != "ignored" for item in result)
    overview = next(item for item in result if item["heading_id"] == "overview")
    assert overview["explicit_id"] is True
    assert len(overview["content_hash"]) == 64


def test_section_hash_changes_with_section_content_only():
    before = agent_sections.find_section(CONTENT, "overview")
    changed = agent_sections.find_section(
        CONTENT.replace("Current architecture.", "Corrected architecture."), "overview"
    )
    rollback = agent_sections.find_section(CONTENT, "rollback")
    rollback_after = agent_sections.find_section(
        CONTENT.replace("Current architecture.", "Corrected architecture."), "rollback"
    )
    assert before is not None and changed is not None
    assert before.content_hash != changed.content_hash
    assert rollback is not None and rollback_after is not None
    assert rollback.content_hash == rollback_after.content_hash


def test_summary_ignores_lifecycle_header_and_returns_first_paragraph():
    content = """# Page

**Lifecycle:** REFERENCE
<!-- lifecycle: REFERENCE -->

Useful first paragraph.

Second paragraph.
"""
    assert agent_sections.summary(content) == "Useful first paragraph."
