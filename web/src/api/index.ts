import { createHttpApi } from './http';
import { createMockApi } from './mock';
import type { SupplyChainApi } from './client';

const source = import.meta.env.VITE_DATA_SOURCE ?? 'mock';

// ★ 配置类错误往启动钩子放，别等第一个请求才炸
if (source !== 'mock' && source !== 'api') {
  throw new Error(`VITE_DATA_SOURCE 只能是 mock | api，收到 ${JSON.stringify(source)}`);
}
console.info('[api]', JSON.stringify({ data_source: source, base: import.meta.env.VITE_API_BASE ?? '/v1' }));

export const api: SupplyChainApi = source === 'mock'
  ? createMockApi()
  : createHttpApi({
      base: import.meta.env.VITE_API_BASE ?? '/v1',
      timeoutMs: Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 8000),
    });

export { ApiError } from './client';
export type { SupplyChainApi } from './client';
