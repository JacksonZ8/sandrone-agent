import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 隔离验证时可指向另一端口的后端：SANDRONE_API_TARGET=http://127.0.0.1:8001 npm run dev
const apiTarget = process.env.SANDRONE_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: {
    cssMinify: "esbuild",
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    headers: {
      "Cache-Control": "no-store",
    },
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
      "/character": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/live2d": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/audio": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/health": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
