import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev: 把 /api 反代到本地 FastAPI（8000）
// prod build 后由 nginx 反代（同源）
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/healthz": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
