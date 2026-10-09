import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { PurchaseOrder } from '../../api/types';
import { fail, ok, type Reply } from '../../testing/backendStub';
import {
  DRAFT, EMPTY_TIMELINE, PLACED, createAttempt, mountPurchase, purchaseRoutes, type BackendState,
} from '../../testing/purchaseBackend';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

const fresh = (po: PurchaseOrder = DRAFT): BackendState => ({ po, timeline: EMPTY_TIMELINE, queue: [] });

const placeBtn = () => screen.findByRole('button', { name: '⛔ 下单' });
async function openDialog() {
  await userEvent.click(await placeBtn());
  return screen.getByRole('dialog');
}
const confirmIn = (dlg: HTMLElement) => within(dlg).getByRole('button', { name: '确认下单' });

describe('⛔ 下单按钮', () => {
  it('右对齐、与其他按钮分开；闸全过时可点', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(fresh()));
    const btn = await placeBtn();
    expect(btn).toHaveClass('btn--danger');
    expect(btn.closest('.btn-row__end')).not.toBeNull();
    expect(within(btn.closest('.btn-row__end') as HTMLElement).queryByRole('button', { name: '保存草稿' })).toBeNull();
    await vi.waitFor(() => expect(btn).toBeEnabled());
  });

  it('有闸未过：禁用并写「{闸} 未过」', async () => {
    const po = { ...DRAFT, preflight: [{ gate: 'supplier', ok: true, detail: {} },
      { gate: 'sources_orderable', ok: false, detail: { offenders: [{ line_id: 101, state: '已撤销' }] } }] };
    await mountPurchase('/purchase/1', purchaseRoutes(fresh(po)));
    const btn = await placeBtn();
    expect(await screen.findByTestId('place-blocked')).toHaveTextContent('来源记录仍可组单 未过');
    expect(btn).toBeDisabled();
  });

  it('有未保存的改动：禁用并写「先保存草稿」', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(fresh()));
    const line = await screen.findByTestId('po-line-1');
    await userEvent.clear(within(line).getByLabelText('单价'));
    await userEvent.type(within(line).getByLabelText('单价'), '13');
    expect(screen.getByTestId('place-blocked')).toHaveTextContent('先保存草稿');
    expect(await placeBtn()).toBeDisabled();
  });

  it('新建（还没落库）：禁用并写「先保存草稿」', async () => {
    await mountPurchase('/purchase/new', purchaseRoutes(fresh()));
    expect(await placeBtn()).toBeDisabled();
    expect(screen.getByTestId('place-blocked')).toHaveTextContent('先保存草稿');
  });
});

describe('⛔ 下单确认弹窗', () => {
  it('正文就是将要发出去的内容', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(fresh()));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    const dlg = await openDialog();
    expect(dlg).toHaveTextContent('⛔ 下单到领星');
    expect(dlg).toHaveTextContent('这一步会在领星建出真实采购单，不可撤销');
    const head = within(dlg).getByTestId('place-header');
    expect(head).toHaveTextContent('东莞宏达（S-1001）');
    expect(head).toHaveTextContent('111');
    expect(head).toHaveTextContent('10925905 陈彩凤');
    expect(head).toHaveTextContent('1 预检');
    expect(head).toHaveTextContent('SCM-PO-1');
    const rows = within(dlg).getAllByTestId('place-line');
    expect(rows).toHaveLength(2);
    expect(rows[1]).toHaveTextContent('SKU-B');
    expect(rows[1]).toHaveTextContent('深圳本地仓（101）');
    expect(rows[1]).toHaveTextContent('0.3333');
    expect(within(rows[1]!).getByTestId('unplanned')).toHaveTextContent('2');
    // 金额精确求和，不经浮点：30×12.5 + 12×0.3333
    expect(within(dlg).getByTestId('place-total')).toHaveTextContent('合计 42 件，金额 378.9996');
    expect(confirmIn(dlg)).toHaveClass('btn--danger');
  });

  it('placed → toast「已下单」+ 领星单号，进详情；请求带的是 confirm_token', async () => {
    const st = fresh();
    const { calls } = await mountPurchase('/purchase/1', purchaseRoutes(st, {
      'POST /v1/purchase-orders/1/place': () => { st.po = PLACED; return ok({ outcome: 'placed', po_id: 1, order_sn: 'PO261009001', erp_call_id: 7 }); },
    }));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    await userEvent.click(confirmIn(await openDialog()));
    expect(await screen.findByRole('status')).toHaveTextContent('已下单，领星单号 PO261009001');
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ confirm_token: 'tok-v4' });
    expect(await screen.findByTestId('po-axes')).toHaveTextContent('已下单');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
    // 写之后刷了红条
    expect(calls.filter((c) => c.path === '/v1/attention').length).toBeGreaterThanOrEqual(2);
  });

  it('rejected → 弹窗里原样显示领星 code / msg，单仍是草稿', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(fresh(), {
      'POST /v1/purchase-orders/1/place': () => ok({ outcome: 'rejected', po_id: 1, lx_code: '1001',
        lx_msg: '供应商不存在 supplier_id=S-1001', erp_call_id: 8 }),
    }));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    const dlg = await openDialog();
    await userEvent.click(confirmIn(dlg));
    const res = await within(dlg).findByTestId('place-result');
    expect(res).toHaveTextContent('1001');
    expect(res).toHaveTextContent('供应商不存在 supplier_id=S-1001');
    expect(res).toHaveTextContent('仍是草稿');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeInTheDocument();
  });

  it('not_sent → 「没有发到领星，可以再次下单」，弹窗不关', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(fresh(), {
      'POST /v1/purchase-orders/1/place': () => ok({ outcome: 'not_sent', po_id: 1, erp_call_id: 9,
        cause: 'ConnectionRefusedError [Errno 111] @ proxy:3128', hint: '请求没有发到领星，可以直接重试' }),
    }));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    const dlg = await openDialog();
    await userEvent.click(confirmIn(dlg));
    const res = await within(dlg).findByTestId('place-result');
    expect(res).toHaveTextContent('没有发到领星，可以再次下单');
    expect(res).toHaveTextContent('Errno 111');
    expect(confirmIn(dlg)).toBeEnabled();
  });

  it('needs_verify → 关弹窗，进详情且红框展开', async () => {
    const st = fresh();
    await mountPurchase('/purchase/1', purchaseRoutes(st, {
      'POST /v1/purchase-orders/1/place': () => {
        st.po = { ...DRAFT, attention: [{ attention_id: 3, kind: 'needs_verify', opened_at: '2026-10-09T10:01:33+08:00' }] };
        return ok({ outcome: 'needs_verify', po_id: 1, erp_call_id: 10, lookup_call_id: 11, lookup_outcome: 'ok',
          truncated: false, timeline: '/v1/purchase-orders/1/timeline' });
      },
    }));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    await userEvent.click(confirmIn(await openDialog()));
    const panel = await screen.findByTestId('attention-panel');
    expect(panel).toHaveClass('panel--alert');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
  });

  it('409 stale_confirm_token → 「草稿已变，请重新打开下单」，不能再点确认', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(fresh(), {
      'POST /v1/purchase-orders/1/place': () => fail(409, 'stale_confirm_token', '采购单内容已变，请刷新后重新确认', { po_id: 1 }),
    }));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    const dlg = await openDialog();
    await userEvent.click(confirmIn(dlg));
    expect(await within(dlg).findByTestId('place-result')).toHaveTextContent('草稿已变，请重新打开下单');
    expect(confirmIn(dlg)).toBeDisabled();
  });

  it('★ 双击确认下单只发一次', async () => {
    let release: (r: Reply) => void = () => {};
    const { calls } = await mountPurchase('/purchase/1', purchaseRoutes(fresh(), {
      'POST /v1/purchase-orders/1/place': () => new Promise<Reply>((r) => { release = r; }),
    }));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    const dlg = await openDialog();
    const btn = confirmIn(dlg);
    await act(async () => { btn.click(); btn.click(); });
    expect(btn).toBeDisabled();
    expect(within(dlg).getByRole('button', { name: '取消' })).toBeDisabled();
    await act(async () => { release(ok({ outcome: 'not_sent', po_id: 1, erp_call_id: 9, cause: 'x', hint: 'h' })); });
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  it('前端等不到回音（status 0）：不当成失败，叫人别再点；只给「重新载入」', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(fresh(), {
      'POST /v1/purchase-orders/1/place': () => { throw new TypeError('fetch failed', { cause: { code: 'ECONNRESET' } }); },
    }));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    const dlg = await openDialog();
    await userEvent.click(confirmIn(dlg));
    const res = await within(dlg).findByTestId('place-result');
    expect(res).toHaveTextContent('不要再点');
    expect(res).toHaveTextContent('ECONNRESET');
    expect(confirmIn(dlg)).toBeDisabled();
    expect(within(dlg).getByRole('button', { name: '重新载入' })).toBeInTheDocument();
    expect(within(dlg).queryByRole('button', { name: '取消' })).toBeNull();
    await userEvent.keyboard('{Escape}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();   // 结果未知时关不掉
  });

  it('调用记录取不到：不让下（下单后核对不了结果）', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(fresh()));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    vi.restoreAllMocks();
    const { stubBackend } = await import('../../testing/backendStub');
    stubBackend(purchaseRoutes(fresh(), { 'GET /v1/purchase-orders/1/timeline': () => fail(500, 'internal', '服务端错误') }));
    const dlg = await openDialog();
    expect(await within(dlg).findByText('调用记录取不到，下单后核对不了结果，先不下单')).toBeInTheDocument();
    expect(confirmIn(dlg)).toBeDisabled();
  });
});

describe('结果未知：每 3 秒查调用记录，等真实结果', () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); });
  const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(3000); });

  async function toUnknown(st: BackendState, place: () => Reply | Promise<Reply>) {
    const r = await mountPurchase('/purchase/1', purchaseRoutes(st, { 'POST /v1/purchase-orders/1/place': place }));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    const dlg = await openDialog();
    await vi.waitFor(() => expect(confirmIn(dlg)).toBeEnabled());
    await userEvent.click(confirmIn(dlg));
    await within(dlg).findByText(/结果未知/);
    return { ...r, dlg };
  }

  it('504 → 确认关掉；在途时说「下单进行中」；落成 rejected 后原样显示', async () => {
    const st = fresh();
    const { dlg } = await toUnknown(st, () => fail(504, 'gateway_timeout', '网关超时'));
    expect(confirmIn(dlg)).toBeDisabled();
    st.timeline = { ...EMPTY_TIMELINE, attempts: [createAttempt()] };
    await tick();
    expect(within(dlg).getByTestId('place-poll')).toHaveTextContent('下单进行中，开始于 2026-10-09 10:01:03');
    st.timeline = { ...EMPTY_TIMELINE, attempts: [createAttempt({ outcome: 'rejected', http_status: 200, lx_code: '1001', lx_msg: '供应商停用' })] };
    await tick();
    const res = within(dlg).getByTestId('place-result');
    expect(res).toHaveTextContent('领星拒绝了这张单');
    expect(res).toHaveTextContent('供应商停用');
  });

  it('回的不是 JSON（代理错误页）→ 轮询到 ok → toast「已下单」并进详情', async () => {
    const st = fresh();
    await toUnknown(st, () => ({ status: 502, json: undefined, raw: '<html>502 Bad Gateway</html>' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('bad_response');
    st.timeline = { ...EMPTY_TIMELINE, attempts: [createAttempt({ outcome: 'ok', http_status: 200, lx_code: '0' })] };
    st.po = PLACED;
    await tick();
    expect(await screen.findByRole('status')).toHaveTextContent('已下单，领星单号 PO261009001');
    expect(await screen.findByTestId('po-axes')).toHaveTextContent('已下单');
  });

  it('409 po_placing（别的页签的下单在途）→ 不是普通错误：交回路由，详情「下单进行中」', async () => {
    const st = fresh();
    await mountPurchase('/purchase/1', purchaseRoutes(st, {
      'POST /v1/purchase-orders/1/place': () => {
        st.timeline = { ...EMPTY_TIMELINE, attempts: [createAttempt({ call_id: 3 })] };
        return fail(409, 'po_placing', '下单请求进行中', { po_id: 1 });
      },
    }));
    await vi.waitFor(async () => expect(await placeBtn()).toBeEnabled());
    const dlg = await openDialog();
    await vi.waitFor(() => expect(confirmIn(dlg)).toBeEnabled());
    await userEvent.click(confirmIn(dlg));
    expect(await screen.findByTestId('attn-placing')).toHaveTextContent('下单进行中');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('button', { name: '⛔ 下单' })).toBeNull();
  });

  it('只认打开之后的那次下单：上一轮的 not_sent 不冒充这一轮的结果', async () => {
    const st = fresh();
    st.timeline = { ...EMPTY_TIMELINE, attempts: [createAttempt({ call_id: 5, outcome: 'not_sent', cause: 'old' })] };
    const { dlg } = await toUnknown(st, () => fail(504, 'gateway_timeout', '网关超时'));
    await tick();
    expect(within(dlg).getByTestId('place-poll')).toHaveTextContent('后端还没有这次下单的记录');
  });

  it('卸载即停止轮询', async () => {
    const { calls, unmount } = await toUnknown(fresh(), () => fail(504, 'gateway_timeout', '网关超时'));
    await tick();
    unmount();
    const n = calls.filter((c) => c.path.endsWith('/timeline')).length;
    await tick(); await tick();
    expect(calls.filter((c) => c.path.endsWith('/timeline')).length).toBe(n);
  });
});
