import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import gridFixture from '../testing/fixtures/grid-1.json';
import plansFixture from '../testing/fixtures/plans.json';
import sellersFixture from '../testing/fixtures/sellers.json';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

/** ★ 桩只认这三条路由，其余一律抛 —— 防止「桩把没实现的调用悄悄喂成空数组」 */
function stubFetch() {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    // ★ Task 20：PlanGrid 挂载即调 getLeadTime（LeadTimeBar），不覆盖会在两边跑出
    //   不同的屏（mock 正常显示、api 因未覆盖的桩报错）——靠时序侥幸通过不是真的相同
    const body = url.includes('/lead-time')
        ? { route: 'oversea_sea', make_days: 30, ship_days: 60, anchor_day: 15,
            valid_from: { making: '2026-01-01', shipping: '2026-01-01' } }
      : url.includes('/attention') ? { count: 0, items: [] }
      : url.includes('/grid') ? gridFixture
      : url.includes('/sellers') ? sellersFixture
      : url.includes('/plans') ? plansFixture
      : null;
    if (body === null) throw new Error(`桩没有覆盖这个调用：${url}`);
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
  });
}

async function screenText(source: 'mock' | 'api'): Promise<string> {
  vi.resetModules();
  const { ApiError } = await import('../api/client');
  if (source === 'api') {
    stubFetch();
    const { createHttpApi } = await import('../api/http');
    vi.doMock('../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  } else {
    const { createMemoryBackend } = await import('../testing/memoryBackend');
    vi.doMock('../api', () => ({ api: createMemoryBackend(), ApiError }));
  }
  const { PlanGrid } = await import('../pages/PlanGrid');
  const { unmount } = render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
  // ★ 多 msku 的店铺默认展开 —— 比的是整屏，不是折叠后的两行字
  await screen.findAllByTestId(/^block-/);
  // ★ Task 20：LeadTimeBar 的 getLeadTime 是独立于 grid/sellers 的另一次异步 —— 不等它
  //   落地就读 body.textContent，两边会因为谁先回来而偶然「相同」，测不出真的一致
  await screen.findByTestId('lt-bar-meta');
  const text = (document.body.textContent ?? '').replace(/\s+/g, ' ').trim();
  unmount();
  return text;
}

describe('判据⑥ · 两种数据源跑出同一屏', () => {
  it('mock 与 api 渲染出的整屏文本逐字相同', async () => {
    const a = await screenText('mock');
    const b = await screenText('api');
    expect(b).toBe(a);
    // ★ 顺带守住「这一屏确实有内容」—— 两边都空也会相等，那是假绿
    expect(a).toContain('库存含计划采购到货（生产 30 天 + 货运 60 天）');
    expect(a).not.toContain('未计本计划采购');
    expect(a).toContain('在途未分摊到店铺');
    expect(a).toContain('计划采购');            // ★ 货号级采购与在途那一行也画出来了
    expect(a.length).toBeGreaterThan(200);
  });
});
