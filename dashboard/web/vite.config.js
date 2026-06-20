import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Read-only SPA: it fetches the static data.json produced by `python -m dashboard.export`.
export default defineConfig({
  plugins: [react()],
  server: { open: true },
});
