import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const apiProxy = process.env.VITE_API_PROXY ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": apiProxy,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/testSetup.ts",
  },
});
