import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: Number(process.env.AGENT_WORLD_WEB_PORT ?? 5173),
    strictPort: true,
    proxy: {
      "/api/accounting": `http://127.0.0.1:${process.env.ACCOUNTING_API_PORT ?? "8020"}`,
      "/api": `http://127.0.0.1:${process.env.AGENT_WORLD_API_PORT ?? "8000"}`,
    },
  },
  build: {
    rollupOptions: { output: { manualChunks: { phaser: ["phaser"] } } },
  },
});
