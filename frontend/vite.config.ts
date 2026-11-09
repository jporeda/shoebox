import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { homeScreenIcons } from "./icons";

export default defineConfig({
  plugins: [react(), homeScreenIcons()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
