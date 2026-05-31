import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_API_TARGET: 로컬 개발 시 backend URL (기본값 http://localhost:8000)
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  cacheDir: "/tmp/vite-ops-portal-cache",
  server: {
    port: 5174,
    proxy: {
      // Phase N — api.ts BASE = "/api/v1"  →  실 backend (port 8000)
      "/api/v1": {
        target: API_TARGET,
        changeOrigin: true,
        // SSE: 청크 스트리밍 유지
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => {
            proxyReq.setHeader("accept-encoding", "identity");
          });
        },
      },
    },
  },
  build: { outDir: "dist", sourcemap: true, target: "es2022" },
});
