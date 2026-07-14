import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/s3': 'http://localhost:8000',
    },
  },
});
