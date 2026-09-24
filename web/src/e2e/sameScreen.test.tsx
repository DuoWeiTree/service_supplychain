import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import gridFixture from '../api/fixtures/grid-1.json';
import plansFixture from '../api/fixtures/plans.json';
import sellersFixture from '../api/fixtures/sellers.json';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

/** ★ 桩只认这三条路由，其余一律抛 —— 防止「桩把没实现的调用悄悄喂成空数组」 */
function stubFetch() {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const body = url.includes('/grid') ? gridFixture
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
    const { createMockApi } = await import('../api/mock');
    vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
  }
  const { PlanGrid } = await import('../pages/PlanGrid');
  const { unmount } = render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
  // ★ 多 msku 的店铺默认展开 —— 比的是整屏，不是折叠后的两行字
  await screen.findAllByTestId(/^block-/);
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
    expect(a).toContain('未计本计划采购');
    expect(a).toContain('在途未分摊到店铺');
    expect(a).toContain('计划采购');            // ★ 货号级采购与在途那一行也画出来了
    expect(a.length).toBeGreaterThan(200);
  });
});
