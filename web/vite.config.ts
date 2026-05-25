import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // During `vite dev`, the React dev server proxies /api/* to the FastAPI
      // backend on :8765 so hot reload works while talking to the real
      // backend. `vite build` outputs to web/dist/, which FastAPI serves
      // directly in production mode.
      "/api": {
        target: "http://localhost:8765",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
