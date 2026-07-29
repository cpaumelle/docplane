# Example With Synthetic Secrets

This synthetic page contains secret-SHAPED tokens (not real secrets) that the
redaction transform must rewrite into well-formed markers, while leaving the
approved placeholders below intact.

- Keep this placeholder: `{{password}}`.
- Keep this variable: `<VAR>`.
- Keep this env ref: `$DATABASE_URL`.

Synthetic access-key-shaped token: AKIAABCDEFGHIJKLMNOP

Synthetic personal-token-shaped string: ghp_abcdefghijklmnopqrstuvwxyz0123456789

Synthetic bearer header: Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345

```bash
# REVISED policy: the shaped token below IS redacted (safe assignment-RHS
# position), while the approved placeholder is preserved.
export AKIAZZZZZZZZZZZZZZZZ
echo "{{password}}"
```
