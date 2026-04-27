// vitest.config.ts
import { defineConfig } from "file:///sessions/eloquent-beautiful-hopper/mnt/AgenticOE_v2/skeleton/frontend/node_modules/vitest/dist/config.js";
import { fileURLToPath, URL } from "node:url";
var __vite_injected_original_import_meta_url = "file:///sessions/eloquent-beautiful-hopper/mnt/AgenticOE_v2/skeleton/frontend/vitest.config.ts";
var vitest_config_default = defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", __vite_injected_original_import_meta_url))
    }
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      thresholds: {
        lines: 70,
        functions: 70
      }
    }
  }
});
export {
  vitest_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZXN0LmNvbmZpZy50cyJdLAogICJzb3VyY2VzQ29udGVudCI6IFsiY29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2Rpcm5hbWUgPSBcIi9zZXNzaW9ucy9lbG9xdWVudC1iZWF1dGlmdWwtaG9wcGVyL21udC9BZ2VudGljT0VfdjIvc2tlbGV0b24vZnJvbnRlbmRcIjtjb25zdCBfX3ZpdGVfaW5qZWN0ZWRfb3JpZ2luYWxfZmlsZW5hbWUgPSBcIi9zZXNzaW9ucy9lbG9xdWVudC1iZWF1dGlmdWwtaG9wcGVyL21udC9BZ2VudGljT0VfdjIvc2tlbGV0b24vZnJvbnRlbmQvdml0ZXN0LmNvbmZpZy50c1wiO2NvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9pbXBvcnRfbWV0YV91cmwgPSBcImZpbGU6Ly8vc2Vzc2lvbnMvZWxvcXVlbnQtYmVhdXRpZnVsLWhvcHBlci9tbnQvQWdlbnRpY09FX3YyL3NrZWxldG9uL2Zyb250ZW5kL3ZpdGVzdC5jb25maWcudHNcIjtpbXBvcnQgeyBkZWZpbmVDb25maWcgfSBmcm9tIFwidml0ZXN0L2NvbmZpZ1wiO1xuaW1wb3J0IHsgZmlsZVVSTFRvUGF0aCwgVVJMIH0gZnJvbSBcIm5vZGU6dXJsXCI7XG5cbmV4cG9ydCBkZWZhdWx0IGRlZmluZUNvbmZpZyh7XG4gIHJlc29sdmU6IHtcbiAgICBhbGlhczoge1xuICAgICAgXCJAXCI6IGZpbGVVUkxUb1BhdGgobmV3IFVSTChcIi4vc3JjXCIsIGltcG9ydC5tZXRhLnVybCkpLFxuICAgIH0sXG4gIH0sXG4gIHRlc3Q6IHtcbiAgICBlbnZpcm9ubWVudDogXCJub2RlXCIsXG4gICAgaW5jbHVkZTogW1wic3JjLyoqLyoudGVzdC50c1wiLCBcInNyYy8qKi8qLnRlc3QudHN4XCJdLFxuICAgIGNvdmVyYWdlOiB7XG4gICAgICBwcm92aWRlcjogXCJ2OFwiLFxuICAgICAgcmVwb3J0ZXI6IFtcInRleHRcIiwgXCJqc29uLXN1bW1hcnlcIl0sXG4gICAgICB0aHJlc2hvbGRzOiB7XG4gICAgICAgIGxpbmVzOiA3MCxcbiAgICAgICAgZnVuY3Rpb25zOiA3MCxcbiAgICAgIH0sXG4gICAgfSxcbiAgfSxcbn0pO1xuIl0sCiAgIm1hcHBpbmdzIjogIjtBQUF3WSxTQUFTLG9CQUFvQjtBQUNyYSxTQUFTLGVBQWUsV0FBVztBQURtTixJQUFNLDJDQUEyQztBQUd2UyxJQUFPLHdCQUFRLGFBQWE7QUFBQSxFQUMxQixTQUFTO0FBQUEsSUFDUCxPQUFPO0FBQUEsTUFDTCxLQUFLLGNBQWMsSUFBSSxJQUFJLFNBQVMsd0NBQWUsQ0FBQztBQUFBLElBQ3REO0FBQUEsRUFDRjtBQUFBLEVBQ0EsTUFBTTtBQUFBLElBQ0osYUFBYTtBQUFBLElBQ2IsU0FBUyxDQUFDLG9CQUFvQixtQkFBbUI7QUFBQSxJQUNqRCxVQUFVO0FBQUEsTUFDUixVQUFVO0FBQUEsTUFDVixVQUFVLENBQUMsUUFBUSxjQUFjO0FBQUEsTUFDakMsWUFBWTtBQUFBLFFBQ1YsT0FBTztBQUFBLFFBQ1AsV0FBVztBQUFBLE1BQ2I7QUFBQSxJQUNGO0FBQUEsRUFDRjtBQUNGLENBQUM7IiwKICAibmFtZXMiOiBbXQp9Cg==
