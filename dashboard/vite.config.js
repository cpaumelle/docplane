import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  build: {
    lib: {
      entry: resolve(process.cwd(), "editor-src/editor.js"),
      name: "DocPlaneEditorBundle",
      formats: ["iife"],
      fileName: () => "editor.bundle.js"
    },
    outDir: "static",
    emptyOutDir: false,
    sourcemap: true,
    minify: "esbuild"
  }
});
