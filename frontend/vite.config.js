import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // recharts v3 pulls in react-redux/@reduxjs/toolkit as transitive deps;
    // without forcing dedup, Vite's optimizer can pre-bundle a second copy
    // of react/react-dom for that subtree, breaking hooks ("Invalid hook
    // call") in components that have nothing to do with recharts.
    dedupe: ["react", "react-dom"],
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})
