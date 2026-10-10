import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the app on :5173 and forwards /api to `python app_server.py --insecure-cookie`.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
  build: { outDir: "dist", emptyOutDir: true },
});
