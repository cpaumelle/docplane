# Fenced Approved Placeholder (must remain)

This synthetic fixture puts an APPROVED placeholder inside a code fence. The
revised fenced-code policy keeps approved placeholders / synthetic examples
inside fences unchanged — only confirmed-secret-shaped values are redacted.

```yaml
auth:
  password: {{password}}
  variable: <VAR>
  endpoint: ${SERVICE_ENDPOINT}
  default: changeme
```

The placeholders above must survive redaction untouched and produce zero
markers.
