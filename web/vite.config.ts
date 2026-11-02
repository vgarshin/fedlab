import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the browser talks to the Vite server (port 5173); every /api request is
// forwarded to the FastAPI container. In production nginx does the same job, so the React
// code always uses plain relative URLs like "/api/runs" and never needs to know the host.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target: process.env.API_PROXY_TARGET ?? "http://localhost:8100", changeOrigin: true } },
  },
});
