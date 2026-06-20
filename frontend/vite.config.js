import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI server (Module 21) serves the built frontend as static files
// from /app/frontend/build at root "/", and exposes the REST + SSE API under
// /api/v1. In production the frontend is same-origin, so base is "/".
// In dev, Vite proxies /api to the backend on :8080.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "build",
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
        // EventSource / SSE needs to stay open; do not buffer.
        ws: false,
      },
    },
  },
});
