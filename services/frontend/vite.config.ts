import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

/**
 * AgentOE 시나리오 빌더 프런트엔드 Vite 설정.
 *
 * - dev 서버: 5173 포트, /api 프록시 → 백엔드(8000).
 * - build: ES2022 / single-bundle, tsc -b 이후 실행 (package.json build 스크립트).
 * - 경로 alias "@" → src/ 로 tsconfig 의 paths 와 일치.
 */
export default defineConfig({
  plugins: [react()],
  cacheDir: "/tmp/vite-agentoe-cache",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    target: "es2022",
  },
});
