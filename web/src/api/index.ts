import { createHttpApi } from './http';
import type { SupplyChainApi } from './client';

// ★ PB-D17：没有假数据源。旧 .env 里还留着这个变量的人会以为自己在看假数据 —— 启动即拒，不静默忽略
const retired: unknown = import.meta.env.VITE_DATA_SOURCE;
if (retired !== undefined) {
  throw new Error(`VITE_DATA_SOURCE 已废除（PB-D17：前端只打真后端），收到 ${JSON.stringify(retired)} —— 从 .env 里删掉这一行`);
}

const base = import.meta.env.VITE_API_BASE ?? '/v1';
const timeoutMs = Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 8000);
// ★ 配置类错误往启动钩子放，别等第一个请求才炸
if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
  throw new Error(`VITE_API_TIMEOUT_MS 必须是正数（毫秒），收到 ${JSON.stringify(import.meta.env.VITE_API_TIMEOUT_MS)}`);
}
console.info('[api]', JSON.stringify({ base, timeout_ms: timeoutMs }));

export const api: SupplyChainApi = createHttpApi({ base, timeoutMs });

export { ApiError } from './client';
export type { SupplyChainApi } from './client';
