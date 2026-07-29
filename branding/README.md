# Local brand overlay

DocPlane ships brand-neutral. To apply your own branding without editing tracked
files (and without committing your brand to this repo), drop files here. They are
gitignored and mounted into the docs-web container over the neutral defaults:

- brand.css   -- redefine the --brand-* / role tokens (see mkdocs/overrides/assets/theme.css).
                 Example: :root{ --brand-500:#1E7D6E; --brand-700:#15564B; --action:var(--brand-500); }
- logo.svg    -- header mark (single-colour currentColor reads best; the header renders it light).
- favicon.svg -- browser favicon.

Then set the product name via the environment:

- DOCPLANE_SITE_NAME="Your Product"   (defaults to DocPlane)
- DOCPLANE_SITE_URL=...                (optional; unset keeps the header title linking Home)

Re-render after changes (POST /api/v1/publication/retry). If a file is absent, the
neutral default is served. Nothing here except this README and .gitkeep is tracked.
