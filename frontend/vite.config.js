import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico', 'favicon.svg', 'apple-touch-icon-180x180.png'],
      manifest: {
        name: 'Trading Bot Dashboard',
        short_name: 'Trading Bot',
        description: 'MT5 trading bot dashboard - signals, positions, risk, backtesting.',
        theme_color: '#0b0f17',
        background_color: '#0b0f17',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        icons: [
          { src: 'pwa-64x64.png', sizes: '64x64', type: 'image/png' },
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          { src: 'maskable-icon-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // This is a live trading dashboard - account balance, positions,
        // signals, risk state must never be served stale from a cache.
        // Only the built app shell (JS/CSS/HTML/icons) is precached;
        // /api and /ws are deliberately excluded from every fallback/cache
        // path so they always hit the network.
        navigateFallbackDenylist: [/^\/api/, /^\/ws/],
        runtimeCaching: [],
      },
    }),
  ],
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
