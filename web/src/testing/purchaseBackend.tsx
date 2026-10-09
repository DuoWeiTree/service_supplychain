import { vi } from 'vitest';
import { act, render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { PoTimeline, PurchaseOptions, PurchaseOrder } from '../api/types';
import { ok, stubBackend, type Handler } from './backendStub';

/** 采购单页的 http 打桩：形状照后端（usecase/draft_po.py::load_po、usecase/attention.py::timeline）。 */
export const OPTIONS: PurchaseOptions = {
  suppliers: [{ supplier_id: 'S-1001', name: '东莞宏达' }],
  warehouses: [{ wid: 'W-SZ', name: '深圳本地仓' }],
  purchasers: [{ purchaser_id: '111', source: '领星历史', count: 135 }],
  opt_uids: [{ opt_uid: '10925905', name: '陈彩凤', source: '领星历史', count: 143 }],
  history: { available: true, cause: null, as_of: '2026-10-08', dropped: 0 },
};

export const DRAFT: PurchaseOrder = {
  po_id: 1, supplier_id: 'S-1001', purchaser_id: '111', opt_uid: '10925905', qc_type: 1, wid: 'W-SZ',
  custom_order_sn: 'SCM-PO-1', order_sn: null, state: '草稿', placed_at: null, draft_version: 4,
  created_by: 'ops.zhang', is_void: false, discarded_at: null,
  lines: [
    { line_no: 1, sku: 'SKU-A', wid: 'W-SZ', units: 30, price: '12.5000', expect_arrive_date: null,
      unplanned_reason: null, lx_item_id: null, sources: [{ line_id: 101, seller_id: '11072', units: 30 }],
      sources_units: 30, unplanned_units: 0 },
    { line_no: 2, sku: 'SKU-B', wid: 'W-SZ', units: 12, price: '0.3333', expect_arrive_date: null,
      unplanned_reason: '补样品', lx_item_id: null, sources: [{ line_id: 102, seller_id: '11072', units: 10 }],
      sources_units: 10, unplanned_units: 2 },
  ],
  source_line_states: [{ line_id: 101, state: '已确认' }, { line_id: 102, state: '已确认' }],
  attention: [],
  preflight: [{ gate: 'supplier', ok: true, detail: {} }, { gate: 'sources_orderable', ok: true, detail: { offenders: [] } }],
  confirm_token: 'tok-v4',
};

export const PLACED: PurchaseOrder = {
  ...DRAFT, state: '已下单', order_sn: 'PO261009001', placed_at: '2026-10-09T10:01:33+08:00',
  lines: DRAFT.lines.map((l, i) => ({ ...l, lx_item_id: i === 0 ? '88001' : null })),
};

export const EMPTY_TIMELINE: PoTimeline = { po_id: 1, conclusion: null, attempts: [], events: [], attention: [] };

const trace = (line_id: number) => ({
  line_id, sku: line_id === 101 ? 'SKU-A' : 'SKU-B', period: '2026-10', total_units: 500, state: '已确认',
  confirmed_units: 30, ordered_units: 0, purchase_orders: [],
});

/** 一个可变的后端：handler 可改 `state.po` 等，之后的 GET 就读到新值 */
export interface BackendState { po: PurchaseOrder; timeline: PoTimeline; queue: unknown[] }

export function purchaseRoutes(state: BackendState, over: Record<string, Handler> = {}): Record<string, Handler> {
  return {
    'GET /v1/attention': () => ok({ count: state.queue.length, items: state.queue }),
    'GET /v1/sellers': () => ok({ sellers: [{ seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' }] }),
    'GET /v1/purchase-options': () => ok(OPTIONS),
    'GET /v1/plan-lines': () => ok({ lines: [], groups: {}, excluded: { state: 0, sku: 0, category: 0, period: 0, fully_confirmed: 0 } }),
    'GET /v1/purchase-orders/1': () => ok(state.po),
    'GET /v1/purchase-orders/1/preflight': () =>
      ok({ po_id: 1, ok: state.po.preflight.every((g) => g.ok), gates: state.po.preflight }),
    'GET /v1/purchase-orders/1/timeline': () => ok(state.timeline),
    'GET /v1/plan-lines/101/trace': () => ok(trace(101)),
    'GET /v1/plan-lines/102/trace': () => ok(trace(102)),
    ...over,
  };
}

export async function mountPurchase(entry: string, routes: Record<string, Handler>) {
  const calls = stubBackend(routes);
  const { ApiError } = await import('../api/client');
  const { createHttpApi } = await import('../api/http');
  vi.doMock('../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  const { PoEditor, PoRoute } = await import('../pages/purchase/PoEditor');
  const utils = render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/purchase" element={<div>HOME-PAGE</div>} />
        <Route path="/purchase/new" element={<PoEditor poId={null} />} />
        <Route path="/purchase/:poId" element={<PoRoute />} />
      </Routes>
    </MemoryRouter>,
  );
  await act(async () => {});
  return { calls, ...utils };
}
