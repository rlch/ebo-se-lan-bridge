import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// `pnpm dev` proxies the bridge API to a running ebo_server (EBO_DEV_TARGET, default :8010).
const target = process.env.EBO_DEV_TARGET ?? 'http://127.0.0.1:8010'
const api = ['/info', '/drive', '/stop', '/move', '/action', '/connection', '/whep', '/hls', '/classic', '/sleep.svg']

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: false,
      includeAssets: ['favicon.ico', 'apple-touch-icon-180x180.png', 'icon.svg', 'robot.svg'],
      manifest: {
        id: '/',
        name: 'EBO',
        short_name: 'EBO',
        description: 'Live view, drive and talk to the EBO robot over Tailscale.',
        start_url: '/',
        scope: '/',
        display: 'fullscreen',
        display_override: ['fullscreen', 'standalone'],
        orientation: 'any',
        background_color: '#0b0d12',
        theme_color: '#0b0d12',
        categories: ['utilities'],
        icons: [
          { src: 'pwa-64x64.png', sizes: '64x64', type: 'image/png' },
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          { src: 'maskable-icon-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Only the app shell is cached; every robot endpoint is live and must hit the network.
        globPatterns: ['**/*.{js,css,html,svg,png,ico,webmanifest}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/(info|drive|stop|move|action|connection|whep|hls|ws|classic)/],
        runtimeCaching: [],
      },
    }),
  ],
  // hls.js is a lazy chunk, loaded only when WebRTC can't play the stream.
  build: { chunkSizeWarningLimit: 700 },
  server: {
    host: true,
    proxy: {
      ...Object.fromEntries(api.map((p) => [p, { target, changeOrigin: true }])),
      '/ws': { target, ws: true, changeOrigin: true },
    },
  },
})
