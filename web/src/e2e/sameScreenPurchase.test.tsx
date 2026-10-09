import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import purchaseFixture from '../testing/fixtures/purchase.json';
import plansFixture from '../testing/fixtures/plans.json';
import sellersFixture from '../testing/fixtures/sellers.json';

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

type Dbs = Record<string, { units: number | null; basis?: string } | null>;
/** 照 rules/purchase.py::seller_caps：basis unknown 或 units 非整数 → 点名这些店；期望合计 0 → 全部点名 */
function sellerCaps(total: number, dbs: Dbs): { caps: Record<string, number> } | { bad: string[] } {
  const bad = Object.keys(dbs).filter((s) => dbs[s]?.basis === 'unknown' || !Number.isInteger(dbs[s]?.units)).sort();
  if (bad.length) return { bad };
  const ex = Object.fromEntries(Object.entries(dbs).map(([s, v]) => [s, v!.units!]));
  const sum = Object.values(ex).reduce((a, b) => a + b, 0);
  if (sum === 0) return { bad: Object.keys(ex).sort() };
  const caps = Object.fromEntries(Object.entries(ex).map(([s, e]) => [s, Math.floor((total * e) / sum)]));
  const extra = total - Object.values(caps).reduce((a, b) => a + b, 0);
  const order = Object.keys(ex).sort((a, b) => (total * ex[b]!) % sum - (total * ex[a]!) % sum || a.localeCompare(b));
  for (const s of order.slice(0, extra)) caps[s]! += 1;
  return { caps };
}

/** 照 api/ui/lines.py::list_lines（orderable=1）：JOIN plan 取标题；used 数未作废单的来源 */
function planLines(): unknown {
  type Ln = { line_id: number; plan_id: number; total_units: number; confirmed_units: number; state: string; demand_by_seller: Dbs };
  const used = new Map<string, number>();
  for (const p of POS) if (p.discarded_at === null) {
    for (const l of p.lines as unknown as { sources: { line_id: number; seller_id: string; units: number }[] }[]) {
      for (const s of l.sources) used.set(`${s.line_id}|${s.seller_id}`, (used.get(`${s.line_id}|${s.seller_id}`) ?? 0) + s.units);
    }
  }
  const all = (purchaseFixture.lines as unknown as Ln[]).map((l) => {
    const r = sellerCaps(l.total_units, l.demand_by_seller);
    const caps = 'caps' in r ? r.caps : {};
    return {
      ...l, plan_title: plansFixture.plans.find((p) => p.plan_id === l.plan_id)!.title,
      remaining: l.total_units - l.confirmed_units,
      blocked: 'bad' in r ? { reason: 'no_seller_basis', sellers: r.bad } : null,
      sellers: Object.fromEntries(Object.entries(l.demand_by_seller).map(([sid, v]) => {
        const u = used.get(`${l.line_id}|${sid}`) ?? 0;
        return [sid, { expected: v?.units ?? null, cap: caps[sid] ?? null, used: u,
                       remaining: sid in caps ? caps[sid]! - u : null }];
      })),
    };
  });
  const st = all.filter((l) => ['已提交', '已确认'].includes(l.state));
  const lines = st.filter((l) => l.remaining > 0);
  return { lines, groups: {}, excluded: { state: all.length - st.length, sku: 0, category: 0, period: 0,
                                          fully_confirmed: st.length - lines.length } };
}

function backendBody(url: string): unknown {
  if (url.includes('/attention')) return { count: 0, items: [] };
  if (url.includes('/checks/purchase')) return purchaseFixture.checks;
  if (url.includes('/plan-lines')) return planLines();
  if (url.includes('/sellers')) return sellersFixture;
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
  await screen.findByTestId('todo-list');
  await screen.findByText(/A4Pet-US 期望/);
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
