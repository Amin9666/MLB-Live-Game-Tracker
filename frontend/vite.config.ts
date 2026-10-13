import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": api,
      "/sse": api,
      "/healthz": api,
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
});
