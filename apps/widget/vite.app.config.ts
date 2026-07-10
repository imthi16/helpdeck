import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

// Iframe app build: the Preact chat app served under dist/app/.
export default defineConfig({
  plugins: [preact()],
  base: "./",
  build: {
    outDir: "dist/app",
    emptyOutDir: true,
  },
});
