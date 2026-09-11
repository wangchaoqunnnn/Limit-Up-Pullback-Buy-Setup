import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * 开发态跨域统一通过 Vite proxy 处理，源码内不出现任何绝对地址。
 * 代理目标来自 .env 中的 VITE_API_PROXY_TARGET（见 .env.example）。
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxyTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000';

  return {
    // 必须使用根路径 base。
    // 后端把前端产物挂在站点根目录（/assets/...），并让所有非 /api 路径回退到
    // index.html。若使用相对 base（'./'），在 /stock/001069 这类多级路由下
    // 资源会被解析为 /stock/assets/... → 命中 SPA 回退返回 HTML，
    // 浏览器会因 MIME 类型不符而拒绝执行，页面直接白屏。
    base: '/',
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: false,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
          ws: false,
        },
      },
    },
    preview: {
      port: 4173,
    },
    build: {
      outDir: 'dist',
      assetsDir: 'assets',
      sourcemap: false,
      cssCodeSplit: false,
      chunkSizeWarningLimit: 1200,
      reportCompressedSize: true,
    },
  };
});
