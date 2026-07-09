import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/main.ts",
      name: "HelpDeck",
      formats: ["iife"],
      fileName: () => "helpdeck.js",
    },
    outDir: "dist",
    emptyOutDir: true,
  },
});
