import { defineConfig } from "vite";

// Loader build: a tiny dependency-free IIFE served as dist/helpdeck.js.
export default defineConfig({
  build: {
    lib: {
      entry: "src/loader.ts",
      name: "HelpDeck",
      formats: ["iife"],
      fileName: () => "helpdeck.js",
    },
    outDir: "dist",
    emptyOutDir: true,
  },
});
