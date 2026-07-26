# Human authoring contract

## Decision

Humans and agents are both first-class DocPlane authors.

The prohibited operation is not human editing. The prohibited operation is bypassing DocPlane authority by editing generated release files, container filesystems, MkDocs output, or PostgreSQL directly.

Every supported authoring surface must call the same versioned control-plane APIs and produce the same identity, concurrency, validation, review, audit, deployment, and certification evidence.

## Authority model

```text
Human dashboard editor ─┐
                        ├─> scoped principal -> change proposal -> validation -> review -> WP8 publication
Agent / MCP / SDK ──────┘
```

A human correction may be as small as a typo or factual inaccuracy. It should not require SSH, a Git checkout, or an agent intermediary.

## Required behaviour

A human authoring surface must:

- authenticate a named HUMAN principal;
- discover and resolve the canonical page before creating content;
- read by stable resource ID rather than relying only on a mutable path;
- support bounded section-level edits and whole-page source editing;
- bind changes to the exact page revision and, where applicable, section hash;
- preserve idempotency for retries;
- show the proposed diff and rendered preview;
- run the same validators used for agent proposals;
- create a durable change proposal and audit trail;
- obey workspace roles, review policy, ownership, and criticality;
- return publication and certification receipts;
- never write directly into a promoted release.

## Editor strategy

DocPlane should eventually provide two synchronized modes:

1. **Markdown source mode** for exact control over MkDocs extensions, attributes, directives, code blocks, comments, and front matter.
2. **Visual mode** for ordinary prose, headings, lists, links, tables, and images.

Source mode is the lossless authority. Visual mode must preserve unsupported or product-specific syntax as protected nodes rather than silently rewriting or deleting it.

The first implementation should prioritize source editing, preview, validation, diff, and proposal submission. Visual editing and collaborative presence can follow after round-trip fidelity tests cover the DocPlane Markdown dialect.

## Candidate components

The implementation spike should compare:

- **CodeMirror 6** for the authoritative Markdown source editor, diff/merge integration, syntax awareness, and extensibility;
- **Milkdown** for a headless, plugin-driven visual Markdown mode based on ProseMirror and Remark;
- **TOAST UI Editor** as a simpler dual Markdown/WYSIWYG alternative.

Selection criteria:

- exact Markdown round-trip fidelity;
- support for MkDocs Material syntax and `attr_list`;
- explicit heading IDs;
- admonitions, tabs, diagrams, code blocks, HTML, comments, and front matter;
- section-level addressing and hashes;
- diff and conflict presentation;
- accessibility and keyboard use;
- bundle size and long-term maintainability;
- collaboration support without bypassing DocPlane revisions.

## Non-goals for the initial sprint

- editing generated HTML or generated Markdown exports;
- direct filesystem save;
- silent auto-merge;
- automatic publication outside WP8;
- forcing all users into WYSIWYG;
- hiding syntax that cannot be round-tripped safely.

## Product invariant

> Human convenience may improve the authoring surface, but it may not create a second authority or a weaker publication path.
