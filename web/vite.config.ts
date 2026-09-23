import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist' },
  // ★ I5 裁定：VITE_API_BASE 默认 `/v1` 是相对路径，dev server 起在 5173，
  //   没有这段转发时 fetch('/v1/plans') 打的是 Vite 自己 —— SPA 回退返回 index.html，
  //   前端报 bad_response「返回的不是 JSON」。日志说得清是怎么坏的，但
  //   VITE_DATA_SOURCE=api 这半边在开发形态下开箱不可用。
  //   端口抄 config.example.toml 的 [api] port（根 README 的 uvicorn 也是它），
  //   不另写第二份。
  server: { proxy: { '/v1': { target: 'http://127.0.0.1:8090', changeOrigin: true } } },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    css: true,
  },
});
