import { build } from "esbuild";

await build({
  entryPoints: {
    "editor.bundle": "editor-src/editor.js",
    "inline-editor": "editor-src/inline-editor.js"
  },
  bundle: true,
  outdir: "static",
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  sourcemap: true,
  minify: true,
  legalComments: "none",
  logLevel: "info"
});
