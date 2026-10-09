import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import purchaseFixture from '../testing/fixtures/purchase.json';

/** 判据⑥ 的采购屏：mock 与 http 打桩渲染同一屏。
 *
 *  ★ 桩按**后端**的规则答（repo/purchase.py 的列表：不含 discarded_at 的单；
 *    供应商名来自供应商表；open_attention 数的是未处理项），不是照 mock 抄：
 *    mock 与后端不一致，红的就是这一条。/attention 与 /checks/purchase 也要桩，
 *    否则外壳红条与核对条在 api 侧落成错误块，两侧永远不同。
 *
 *  ⚠ 循环的一段：fixture 是两侧共用的字节，所以这条门禁的是 http.ts 拆包与页面渲染，
 *    不证明 fixture 本身像真后端。 */

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

type Fx = { po_id: number; custom_order_sn: string; supplier_id: string; state: string; order_sn: string | null; is_void: boolean;
  discarded_at: string | null; lines: { units: number }[]; created_by: string; created_at: string };
const POS = purchaseFixture.purchase_orders as unknown as Fx[];

function backendBody(url: string): unknown {
  if (url.includes('/attention')) return { count: 0, items: [] };
  if (url.includes('/checks/purchase')) return purchaseFixture.checks;
  if (url.includes('/purchase-orders')) {
    const state = new URL(url, 'http://x').searchParams.get('state');
    return { purchase_orders: POS
      .filter((p) => p.discarded_at === null && (state === null || p.state === state))
      .sort((a, b) => b.po_id - a.po_id)
      .map((p) => ({
        po_id: p.po_id, custom_order_sn: p.custom_order_sn, supplier_id: p.supplier_id,
        supplier_name: purchaseFixture.options.suppliers.find((s) => s.supplier_id === p.supplier_id)?.name ?? p.supplier_id,
        state: p.state, order_sn: p.order_sn, is_void: p.is_void, lines: p.lines.length,
        units: p.lines.reduce((a, l) => a + l.units, 0), open_attention: 0,
        created_by: p.created_by, created_at: p.created_at,
      })) };
  }
  throw new Error(`桩没有覆盖这个调用：${url}`);
}

async function screenText(source: 'mock' | 'api'): Promise<string> {
  vi.resetModules();
  const { ApiError } = await import('../api/client');
  if (source === 'api') {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => new Response(
      JSON.stringify(backendBody(String(input))), { status: 200, headers: { 'content-type': 'application/json' } }));
    const { createHttpApi } = await import('../api/http');
    vi.doMock('../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  } else {
    const { createMemoryBackend } = await import('../testing/memoryBackend');
    vi.doMock('../api', () => ({ api: createMemoryBackend(), ApiError }));
  }
  const { PurchaseHome } = await import('../pages/purchase/PurchaseHome');
  const { unmount } = render(<MemoryRouter><PurchaseHome /></MemoryRouter>);
  await screen.findByTestId('po-list');
  await screen.findByText('核对全部通过');
  const text = (document.body.textContent ?? '').replace(/\s+/g, ' ').trim();
  unmount();
  return text;
}

describe('判据⑥ · 采购首页两种数据源跑出同一屏', () => {
  it('★ 整屏文字逐字相同，且确实有内容', async () => {
    const a = await screenText('mock');
    const b = await screenText('api');
    expect(b).toEqual(a);
    expect(a).toContain('东莞宏达');
    expect(a).toContain('PO260930001');
    expect(a).toContain('核对全部通过');
  });
});
