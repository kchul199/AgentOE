// vite.config.ts
import { defineConfig } from "file:///sessions/practical-gallant-galileo/mnt/AgenticOE_v2/services/ops-portal/node_modules/vite/dist/node/index.js";
import react from "file:///sessions/practical-gallant-galileo/mnt/AgenticOE_v2/services/ops-portal/node_modules/@vitejs/plugin-react/dist/index.js";
var vite_config_default = defineConfig({
  plugins: [react()],
  cacheDir: "/tmp/vite-ops-portal-cache",
  server: {
    port: 5174,
    proxy: {
      "/ops-api": {
        target: "http://localhost:8001",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/ops-api/, "")
      }
    }
  },
  build: { outDir: "dist", sourcemap: true, target: "es2022" }
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcudHMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCIvc2Vzc2lvbnMvcHJhY3RpY2FsLWdhbGxhbnQtZ2FsaWxlby9tbnQvQWdlbnRpY09FX3YyL3NlcnZpY2VzL29wcy1wb3J0YWxcIjtjb25zdCBfX3ZpdGVfaW5qZWN0ZWRfb3JpZ2luYWxfZmlsZW5hbWUgPSBcIi9zZXNzaW9ucy9wcmFjdGljYWwtZ2FsbGFudC1nYWxpbGVvL21udC9BZ2VudGljT0VfdjIvc2VydmljZXMvb3BzLXBvcnRhbC92aXRlLmNvbmZpZy50c1wiO2NvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9pbXBvcnRfbWV0YV91cmwgPSBcImZpbGU6Ly8vc2Vzc2lvbnMvcHJhY3RpY2FsLWdhbGxhbnQtZ2FsaWxlby9tbnQvQWdlbnRpY09FX3YyL3NlcnZpY2VzL29wcy1wb3J0YWwvdml0ZS5jb25maWcudHNcIjtpbXBvcnQgeyBkZWZpbmVDb25maWcgfSBmcm9tIFwidml0ZVwiO1xuaW1wb3J0IHJlYWN0IGZyb20gXCJAdml0ZWpzL3BsdWdpbi1yZWFjdFwiO1xuXG5leHBvcnQgZGVmYXVsdCBkZWZpbmVDb25maWcoe1xuICBwbHVnaW5zOiBbcmVhY3QoKV0sXG4gIGNhY2hlRGlyOiBcIi90bXAvdml0ZS1vcHMtcG9ydGFsLWNhY2hlXCIsXG4gIHNlcnZlcjoge1xuICAgIHBvcnQ6IDUxNzQsXG4gICAgcHJveHk6IHtcbiAgICAgIFwiL29wcy1hcGlcIjoge1xuICAgICAgICB0YXJnZXQ6IFwiaHR0cDovL2xvY2FsaG9zdDo4MDAxXCIsXG4gICAgICAgIGNoYW5nZU9yaWdpbjogdHJ1ZSxcbiAgICAgICAgcmV3cml0ZTogKHApID0+IHAucmVwbGFjZSgvXlxcL29wcy1hcGkvLCBcIlwiKSxcbiAgICAgIH0sXG4gICAgfSxcbiAgfSxcbiAgYnVpbGQ6IHsgb3V0RGlyOiBcImRpc3RcIiwgc291cmNlbWFwOiB0cnVlLCB0YXJnZXQ6IFwiZXMyMDIyXCIgfSxcbn0pO1xuIl0sCiAgIm1hcHBpbmdzIjogIjtBQUEwWSxTQUFTLG9CQUFvQjtBQUN2YSxPQUFPLFdBQVc7QUFFbEIsSUFBTyxzQkFBUSxhQUFhO0FBQUEsRUFDMUIsU0FBUyxDQUFDLE1BQU0sQ0FBQztBQUFBLEVBQ2pCLFVBQVU7QUFBQSxFQUNWLFFBQVE7QUFBQSxJQUNOLE1BQU07QUFBQSxJQUNOLE9BQU87QUFBQSxNQUNMLFlBQVk7QUFBQSxRQUNWLFFBQVE7QUFBQSxRQUNSLGNBQWM7QUFBQSxRQUNkLFNBQVMsQ0FBQyxNQUFNLEVBQUUsUUFBUSxjQUFjLEVBQUU7QUFBQSxNQUM1QztBQUFBLElBQ0Y7QUFBQSxFQUNGO0FBQUEsRUFDQSxPQUFPLEVBQUUsUUFBUSxRQUFRLFdBQVcsTUFBTSxRQUFRLFNBQVM7QUFDN0QsQ0FBQzsiLAogICJuYW1lcyI6IFtdCn0K
