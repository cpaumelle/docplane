# Example Configuration

This synthetic page documents configuration placeholders that must survive
redaction untouched.

- Password field uses the placeholder `{{password}}`.
- A generic variable placeholder `<VAR>`.
- An environment reference `$DATABASE_URL` and `${SERVICE_ENDPOINT}`.
- The literal `changeme` default.

## Service names

The `password-service` and `token-broker` are ordinary service identifiers and
must not be rewritten just because their names resemble credential words.

```yaml
# Executable example — must stay byte-for-byte intact.
auth:
  password: {{password}}
  api_key: ${SERVICE_ENDPOINT}
```
