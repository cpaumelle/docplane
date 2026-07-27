import { build } from "esbuild";

await build({
  entryPoints: ["editor-src/editor.js"],
  bundle: true,
  outfile: "static/editor.bundle.js",
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  sourcemap: true,
  minify: true,
  legalComments: "none",
  logLevel: "info"
});
