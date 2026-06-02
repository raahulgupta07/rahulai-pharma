import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  server: {
    proxy: {
      '/teams': 'http://localhost:8001',
      '/health': 'http://localhost:8001',
      '/api': 'http://localhost:8001'
    }
  }
});
