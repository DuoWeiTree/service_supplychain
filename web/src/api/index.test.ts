import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => { vi.unstubAllEnvs(); vi.resetModules(); });

describe('api 装配（PB-D17）', () => {
  it('没有 VITE_DATA_SOURCE → 只装真 http 客户端，打 VITE_API_BASE', async () => {
    vi.stubEnv('VITE_API_BASE', '/v1');
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ plans: [], excluded: { archived: 0 } }),
        { status: 200, headers: { 'content-type': 'application/json' } }));
    const { api } = await import('./index');
    await api.listPlans({});
    expect(String(fetchSpy.mock.calls[0]![0])).toMatch(/^\/v1\/plans/);
    fetchSpy.mockRestore();
  });

  it.each(['mock', 'api', ''])('VITE_DATA_SOURCE 还设着（%j）→ 启动即拒并点名 PB-D17', async (v) => {
    vi.stubEnv('VITE_DATA_SOURCE', v);
    await expect(import('./index')).rejects.toThrow(/VITE_DATA_SOURCE 已废除（PB-D17/);
  });

  it('VITE_API_TIMEOUT_MS 不是正数 → 启动即拒', async () => {
    vi.stubEnv('VITE_API_TIMEOUT_MS', 'abc');
    await expect(import('./index')).rejects.toThrow(/VITE_API_TIMEOUT_MS/);
  });
});
