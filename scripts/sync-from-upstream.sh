#!/usr/bin/env bash
# Refresh the vendored copies in this package from the canonical sources in
# the parent repo. Only meaningful when this directory still lives inside
# the upstream monorepo (charliehub-hub2). No-op-safe: verifies sources exist.
#
# Vendored verbatim:   docs-api/{app,tests,scripts,Dockerfile,requirements.txt}
#                      db/init.sql (from docs-api/init.sql)
#                      mkdocs/nginx.conf (from docs-mkdocs/nginx.conf)
# NOT auto-synced:     mcp/tools/docs.py — the copy here is genericized
#                      (CharlieHub-specific docstrings/paths removed); this
#                      script diffs it against upstream so you can port real
#                      changes by hand.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
UPSTREAM="$(cd "$HERE/.." && pwd)"

[[ -d "$UPSTREAM/docs-api/app" ]] || {
    echo "upstream docs-api not found at $UPSTREAM/docs-api — nothing to sync" >&2
    exit 1
}

echo "== syncing docs-api code from $UPSTREAM/docs-api"
mkdir -p "$HERE/docs-api"
for d in app tests scripts; do
    rm -rf "$HERE/docs-api/$d"
    cp -a "$UPSTREAM/docs-api/$d" "$HERE/docs-api/$d"
    find "$HERE/docs-api/$d" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
done
cp "$UPSTREAM/docs-api/Dockerfile"       "$HERE/docs-api/Dockerfile"
cp "$UPSTREAM/docs-api/requirements.txt" "$HERE/docs-api/requirements.txt"

echo "== syncing db/init.sql"
cp "$UPSTREAM/docs-api/init.sql" "$HERE/db/init.sql.upstream"
if ! diff -q "$HERE/db/init.sql.upstream" "$HERE/db/init.sql" >/dev/null 2>&1; then
    echo "   db/init.sql differs from upstream (local copy carries a different header)."
    echo "   Review: diff db/init.sql.upstream db/init.sql"
else
    rm -f "$HERE/db/init.sql.upstream"
fi

echo "== syncing mkdocs/nginx.conf"
cp "$UPSTREAM/docs-mkdocs/nginx.conf" "$HERE/mkdocs/nginx.conf"

echo "== checking mcp/tools/docs.py against upstream (manual port if it drifted)"
if ! diff -q "$UPSTREAM/mcp/tools/docs.py" "$HERE/mcp/tools/docs.py" >/dev/null; then
    echo "   differs (expected — the local copy is genericized). To review upstream changes:"
    echo "   diff -u $UPSTREAM/mcp/tools/docs.py $HERE/mcp/tools/docs.py | less"
fi

echo "done."
