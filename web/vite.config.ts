import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const BACKEND = 'http://127.0.0.1:8091';

export default defineConfig(({ mode }) => {
  // ★ PB-D17：没有假数据源。在起服务的终端第一屏就拒（CLAUDE.md 判据 4），不等打开浏览器看控制台
  const retired = loadEnv(mode, process.cwd(), 'VITE_')['VITE_DATA_SOURCE'];
  if (retired !== undefined) {
    throw new Error(`VITE_DATA_SOURCE 已废除（PB-D17：前端只打真后端），收到 ${JSON.stringify(retired)} —— 从 .env / 环境变量里删掉`);
  }
  return {
  plugins: [react()],
  build: { outDir: 'dist' },
  // ★ I5 裁定：VITE_API_BASE 默认 `/v1` 是相对路径，dev server 起在 5173，
  //   没有这段转发时 fetch('/v1/plans') 打的是 Vite 自己 —— SPA 回退返回 index.html，
  //   前端报 bad_response「返回的不是 JSON」。日志说得清是怎么坏的，但开发形态下开箱不可用。
  //   端口抄 config.example.toml 的 [api] port（根 README 的 uvicorn 也是它），
  //   不另写第二份。
  server: {
    proxy: {
      '/v1': {
        target: BACKEND,
        changeOrigin: true,
        // ★ 后端没起时代理默认回一个空 500，页面会显示「返回的不是 JSON」去冤枉接口。
        //   这里回成 502 + JSON，带 cause code（ECONNREFUSED 等），终端同时留一行三问日志
        configure: (proxy) => {
          proxy.on('error', (err, req, res) => {
            const code = (err as NodeJS.ErrnoException).code ?? 'unknown';
            console.error(`[proxy] ${req.method} ${req.url} → ${BACKEND} failed cause_code=${code} message=${err.message}`);
            if ('writeHead' in res && !res.headersSent) {
              res.writeHead(502, { 'content-type': 'application/json' });
              res.end(JSON.stringify({ error: 'backend_unreachable', hint: `连不上后端 ${BACKEND}（${code}）`, cause_code: code, target: BACKEND }));
            }
          });
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    css: true,
  },
  };
});
