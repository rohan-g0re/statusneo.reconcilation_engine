import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs on 8000 and the dev server on 5173.  Proxying /api means the front-end code
// never carries a base URL, so the same build works behind a single origin in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
