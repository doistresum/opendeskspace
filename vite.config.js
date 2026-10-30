import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:9090",
      "/stream": { target: "ws://127.0.0.1:9090", ws: true },
      "/audio": { target: "ws://127.0.0.1:9090", ws: true },
    },
  },
});
