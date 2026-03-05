import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // Listen on all local IPs
    port: 3000,
    strictPort: true, // Fail if port 3000 is taken
    proxy: {
      '/api': {
        target: 'http://localhost:8002',
        changeOrigin: true,
        secure: false,
      },
      '/ws': {
        target: 'ws://localhost:8002',
        ws: true,
        changeOrigin: true,
      }
    }
  }
})
