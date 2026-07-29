# Fenced Value With No Safe Replacement (must fail closed)

This synthetic fixture puts a secret-SHAPED token (not a real secret) inside a
code fence in a position where NO syntax-preserving replacement can be
guaranteed: it is embedded inside a URL with no surrounding delimiter, so
swapping it for a bare marker could change how the line parses.

The revised policy REFUSES this document (fail closed) and emits a content-free
remediation finding — it never silently passes the token through and never
emits broken output.

```bash
curl https://ghp_abcdefghijklmnopqrstuvwxyz0123456789@example.invalid/path
```

Expected behaviour: `redact()` raises `DocumentRefusedError`; the document is
refused; the finding names only the reason code, marker class, and fence
language.
