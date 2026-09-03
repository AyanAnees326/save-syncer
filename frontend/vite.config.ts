import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The production build lands inside the Python package, which serves it as the
// desktop UI. In development, Vite proxies the API to a plain uvicorn on 8765.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../backend/savesync/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8765", changeOrigin: true, ws: true },
    },
  },
});
