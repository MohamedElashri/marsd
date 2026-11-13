import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../src/marsd/static', emptyOutDir: true },
  server: { strictPort: true },
});
