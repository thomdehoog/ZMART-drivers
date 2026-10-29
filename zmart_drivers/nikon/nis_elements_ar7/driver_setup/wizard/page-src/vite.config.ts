import { defineConfig } from "vite";

/* `npm run dev` serves the page with live reload and sends the wizard's calls
   on to a running wizard (start it with `python -m ...driver_setup.wizard --no-window
   --port 8765`). `npm run build` writes plain files to ../page, which are
   committed, so that running the wizard needs no Node at all. */
export default defineConfig({
  base: "./",
  build: { outDir: "../page", emptyOutDir: true, target: "es2022" },
  server: { proxy: { "/api": "http://127.0.0.1:8765", "/files": "http://127.0.0.1:8765" } },
});
