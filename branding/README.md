# Local brand overlay

DocPlane ships brand-neutral. To apply your own branding without editing tracked
files (and without committing your brand to this repo), drop files here. They are
gitignored and mounted into the docs-web container over the neutral defaults:

- brand.css   -- redefine the --brand-* / role tokens (see mkdocs/overrides/assets/theme.css).
                 Example: :root{ --brand-500:#1E7D6E; --brand-700:#15564B; --action:var(--brand-500); }

                 The same file rebrands the CATEGORY IDENTITY palette -- the
                 four-domain wayfinding colours (work / know / model / observe)
                 behind page badges and dashboard chips on every surface. The
                 canonical definition is the --cat-* triplets in theme.css
                 (hue, wash, ink per domain); override them together, keeping
                 badge ink at >=4.5:1 contrast on its wash. Example:
                 :root{
                   --cat-observe:#1E7D6E; --cat-observe-wash:#E6F2EF; --cat-observe-ink:#15564B;
                   --cat-work:#C9A227;    --cat-work-wash:#F8F1DA;    --cat-work-ink:#79611A;
                 }
                 These are identity colours, never status: --state-* stays
                 reserved for signal, and category colours must not be used
                 to mean healthy/broken.

                 The sidebar's four domain group headings render as solid
                 colour bands with white type. A band defaults to the domain
                 hue; if your hue is too light for white type (bright golds
                 especially), override just the band fill:
                 :root{ --cat-work-band:#8A6F14; }  /* darker Aour, badges keep #C9A227 */
- logo.svg    -- header mark (single-colour currentColor reads best; the header renders it light).
- favicon.svg -- browser favicon.

Then set the product name via the environment:

- DOCPLANE_SITE_NAME="Your Product"   (defaults to DocPlane)
- DOCPLANE_SITE_URL=...                (optional; unset keeps the header title linking Home)

Re-render after changes (POST /api/v1/publication/retry). If a file is absent, the
neutral default is served. Nothing here except this README and .gitkeep is tracked.
