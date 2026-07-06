import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // relative asset paths: the same build serves at / (uvicorn, pages.dev)
  // and under /seiche-site/ (GitHub Pages) — hash routing makes this safe
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8787" },
  },
});
