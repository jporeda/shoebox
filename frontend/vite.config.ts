import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { homeScreenIcons } from "./icons";

const api = { "/api": "http://localhost:8000" };

// Two apps from one source tree: the desktop UI (port 8000) and, with `--mode touch`, the phone and
// iPad app in touch/ (port 8001), which imports the shared API client and hooks from src/.
export default defineConfig(({ mode }) =>
  mode === "touch"
    ? {
        root: "touch",
        plugins: [react(), homeScreenIcons()],
        build: { outDir: "../dist-touch", emptyOutDir: true },
        server: { port: 5174, proxy: api },
      }
    : {
        plugins: [react(), homeScreenIcons()],
        server: { proxy: api },
      },
);
