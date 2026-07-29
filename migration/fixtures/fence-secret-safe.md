# Fenced Confirmed-Secret-Shaped Value (must be redacted)

This synthetic fixture puts secret-SHAPED tokens (not real secrets) inside code
fences in syntactically SAFE positions: a shell assignment RHS and a quoted
YAML scalar. The revised policy redacts them with a syntax-preserving
replacement, so the surrounding shell / YAML stays valid.

```bash
export API_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789
export ACCESS_KEY=AKIAABCDEFGHIJKLMNOP
```

```yaml
credentials:
  token: "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
```

Every shaped token above must be replaced by a well-formed
`<REDACTED:CLASS:LABEL>` marker; none may survive just because it sits in a
fence.
