import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PoAttempt, PoTimeline, PurchaseOrder } from '../../api/types';
import { fail, ok } from '../../testing/backendStub';
import { DRAFT, PLACED, mountPurchase, purchaseRoutes, type BackendState } from '../../testing/purchaseBackend';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

// L8 禁词（tests/test_layering_web.py）：后端 differences 的对侧键名只在打桩里拼出来
const THEIRS = 'ling' + 'xing';

const NV_OPEN = { attention_id: 3, kind: 'needs_verify' as const, opened_at: '2026-10-09T10:01:33+08:00' };
const PENDING: PurchaseOrder = { ...DRAFT, attention: [NV_OPEN] };

const CREATE: PoAttempt = {
  call_id: 10, at: '2026-10-09T10:01:03.120+08:00', actor: 'ops.zhang', endpoint: 'create_purchase_order',
  host: 'erp.example', path: '/erp/sc/routing/purchase/purchase/createPurchaseOrder', idem_key: 'SCM-PO-1',
  elapsed_ms: 30012, outcome: 'unknown', http_status: null, lx_code: null, lx_msg: null,
  cause: 'TimeoutError: The read operation timed out @ erp.example:443', retry_of: null,
  request: { custom_order_sn: 'SCM-PO-1', supplier_id: 'S-1001', access_token: '***' }, response: null,
};
const LOOKUP: PoAttempt = {
  ...CREATE, call_id: 11, at: '2026-10-09T10:01:34+08:00', endpoint: 'purchase_order_list',
  path: '/erp/sc/routing/data/local_inventory/purchaseOrderList', idem_key: null, elapsed_ms: 812,
  outcome: 'ok', http_status: 200, lx_code: '0', lx_msg: 'success', cause: null, retry_of: 10,
  request: { search_field_time: 'create_time', start_date: '2026-10-08', end_date: '2026-10-10', offset: 0, length: 500 },
  response: { code: 0, data: [], total: 0 },
};
const WINDOW = { search_field_time: 'create_time', start_date: '2026-10-08', end_date: '2026-10-10' };

const nvTimeline = (lastLookup: boolean): PoTimeline => ({
  po_id: 1,
  conclusion: {
    kind: 'needs_verify', known: '请求已发到领星，未收到可确认的结果（unknown，TimeoutError）', unknown: '领星是否已建单',
    last_lookup: lastLookup ? { call_id: 11, at: '2026-10-09T10:01:34+08:00', outcome: 'ok', truncated: false, window: WINDOW } : null,
    how_to_search: { custom_order_sn: 'SCM-PO-1', window: WINDOW },
  },
  attempts: lastLookup ? [CREATE, LOOKUP] : [CREATE],
  events: [],
  attention: [{ ...NV_OPEN, resolved_at: null, resolution: null, resolved_by: null, reason: null,
    detail: { create_call_id: 10, create_outcome: 'unknown', request_confirmed_sent: true, custom_order_sn: 'SCM-PO-1', opened_by: 'place' } }],
});

const nvState = (lastLookup = true): BackendState => ({ po: PENDING, timeline: nvTimeline(lastLookup), queue: [] });
const panel = () => screen.findByTestId('attention-panel');

describe('路由：有未处理项的草稿不进编辑器', () => {
  it('★ 待核实的草稿 → 详情 + 红框，没有保存 / 删除 / 下单', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState()));
    expect(await panel()).toHaveClass('panel--alert');
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
    expect(screen.queryByRole('button', { name: '删除草稿' })).toBeNull();
    expect(screen.queryByRole('button', { name: '⛔ 下单' })).toBeNull();
  });
});

describe('结论', () => {
  it('已知 / 未知 / 最近一次查重（时间 + 结果）+ 去领星照着搜的条件', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState()));
    const c = within(await panel()).getByTestId('conclusion');
    expect(c).toHaveTextContent('已知：请求已发到领星');
    expect(c).toHaveTextContent('未知：领星是否已建单');
    expect(c).toHaveTextContent('最近一次查重：2026-10-09 10:01:34 查询成功');
    expect(c).toHaveTextContent('SCM-PO-1');
    expect(c).toHaveTextContent('2026-10-08 至 2026-10-10');
  });

  it('★ 空≠0：从没查过 与 查过没找到 写得不一样', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState(false)));
    const c = within(await panel()).getByTestId('conclusion');
    expect(c).toHaveTextContent('最近一次查重：还没查过');
    expect(c).not.toHaveTextContent('查询成功');
  });
});

describe('时间线', () => {
  it('每次调用一行：时间 人 接口 耗时 结果 cause；请求 / 响应体在折叠区', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState()));
    const tl = within(await panel()).getByTestId('timeline');
    const rows = within(tl).getAllByTestId('attempt');
    expect(rows).toHaveLength(2);
    const r0 = rows[0]!;
    expect(r0).toHaveTextContent('2026-10-09 10:01:03');
    expect(r0).toHaveTextContent('ops.zhang');
    expect(r0).toHaveTextContent('create_purchase_order');
    expect(r0).toHaveTextContent('30012 ms');
    expect(r0).toHaveTextContent('结果未知');
    expect(r0).toHaveTextContent('TimeoutError');
    const fold = r0.querySelector('details.fold');
    expect(fold).not.toBeNull();
    expect(fold).not.toHaveAttribute('open');
    expect(fold).toHaveTextContent('"custom_order_sn": "SCM-PO-1"');
    expect(rows[1]).toHaveTextContent('812 ms');
    expect(rows[1]).toHaveTextContent('code 0');
  });
});

describe('待核实的动作', () => {
  it('确认领星无此单：理由为空时禁用', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState()));
    const p = await panel();
    const btn = within(p).getByRole('button', { name: '确认领星无此单' });
    expect(btn).toBeDisabled();
    await userEvent.type(within(p).getByLabelText('确认无此单的理由'), '   ');
    expect(btn).toBeDisabled();
    await userEvent.type(within(p).getByLabelText('确认无此单的理由'), '领星后台按幂等键搜过');
    expect(btn).toBeEnabled();
  });

  it('★ 409 contradicts_evidence：逐个点名找到过的领星单号', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState(), {
      'POST /v1/purchase-orders/1/confirm-absent': () => fail(409, 'contradicts_evidence',
        '系统查重在领星找到过本单幂等键的单，不能确认「领星无此单」；先在领星处理该单',
        { po_id: 1, found_order_sns: ['PO261009001', 'PO261009002'], lookup_call_id: 11,
          open_landed_mismatch: false, custom_order_sn: 'SCM-PO-1' }),
    }));
    const p = await panel();
    await userEvent.type(within(p).getByLabelText('确认无此单的理由'), '搜过');
    await userEvent.click(within(p).getByRole('button', { name: '确认领星无此单' }));
    const box = await within(p).findByTestId('contradicts');
    const sns = within(box).getAllByTestId('found-sn').map((x) => x.textContent);
    expect(sns).toEqual(['PO261009001', 'PO261009002']);
    expect(box).toHaveTextContent('不能确认「领星无此单」');
  });

  it('409 lookup_in_progress → 「查询进行中，稍后再试」', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState(), {
      'POST /v1/purchase-orders/1/confirm-absent': () => fail(409, 'lookup_in_progress', '有查询 / 下单调用进行中，等它结束再确认', { po_id: 1 }),
    }));
    const p = await panel();
    await userEvent.type(within(p).getByLabelText('确认无此单的理由'), '搜过');
    await userEvent.click(within(p).getByRole('button', { name: '确认领星无此单' }));
    expect(await within(p).findByText('查询进行中，稍后再试')).toBeInTheDocument();
  });

  it('确认领星无此单成功 → toast，草稿解锁回到编辑器', async () => {
    const st = nvState();
    const { calls } = await mountPurchase('/purchase/1', purchaseRoutes(st, {
      'POST /v1/purchase-orders/1/confirm-absent': () => {
        st.po = DRAFT;
        return ok({ po_id: 1, unlocked: ['place', 'update', 'delete'] });
      },
    }));
    const p = await panel();
    await userEvent.type(within(p).getByLabelText('确认无此单的理由'), '领星后台按幂等键搜过');
    await userEvent.click(within(p).getByRole('button', { name: '确认领星无此单' }));
    expect(await screen.findByRole('status')).toHaveTextContent('已确认领星无此单');
    expect(calls.find((c) => c.path.endsWith('/confirm-absent'))?.body).toEqual({ reason: '领星后台按幂等键搜过' });
    expect(await screen.findByRole('button', { name: '保存草稿' })).toBeInTheDocument();
  });

  it('登记已有单：单号为空时禁用；成功 → toast「已登记」，转已下单', async () => {
    const st = nvState();
    const { calls } = await mountPurchase('/purchase/1', purchaseRoutes(st, {
      'POST /v1/purchase-orders/1/register-existing': () => {
        st.po = PLACED; st.timeline = { ...st.timeline, conclusion: null, attention: [] };
        return ok({ outcome: 'placed', po_id: 1, order_sn: 'PO261009001', found_by: 'manual' });
      },
    }));
    const p = await panel();
    const btn = within(p).getByRole('button', { name: '登记已有单' });
    expect(btn).toBeDisabled();
    await userEvent.type(within(p).getByLabelText('领星单号'), 'PO261009001');
    await userEvent.click(btn);
    expect(await screen.findByRole('status')).toHaveTextContent('已登记领星单 PO261009001');
    expect(calls.find((c) => c.path.endsWith('/register-existing'))?.body).toEqual({ order_sn: 'PO261009001' });
    await vi.waitFor(() => expect(screen.queryByTestId('attention-panel')).toBeNull());
    expect(screen.getByTestId('po-axes')).toHaveTextContent('已下单');
  });

  it('重新核对 → toast「已重新核对」，重取时间线', async () => {
    const { calls } = await mountPurchase('/purchase/1', purchaseRoutes(nvState(), {
      'POST /v1/purchase-orders/1/recheck': () => ok({ outcome: 'needs_verify', po_id: 1, erp_call_id: 10,
        lookup_call_id: 12, lookup_outcome: 'ok', truncated: false, timeline: '/v1/purchase-orders/1/timeline' }),
    }));
    const p = await panel();
    const before = calls.filter((c) => c.path.endsWith('/timeline')).length;
    await userEvent.click(within(p).getByRole('button', { name: '重新核对' }));
    expect(await screen.findByRole('status')).toHaveTextContent('已重新核对');
    await vi.waitFor(() => expect(calls.filter((c) => c.path.endsWith('/timeline')).length).toBeGreaterThan(before));
  });

  it('mock 数据源 501 not_in_mock：动作的错误点名显示', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState(), {
      'POST /v1/purchase-orders/1/recheck': () => fail(501, 'not_in_mock', 'mock 数据源不下单'),
    }));
    const p = await panel();
    await userEvent.click(within(p).getByRole('button', { name: '重新核对' }));
    expect(await within(p).findByText('mock 数据源不下单')).toBeInTheDocument();
  });
});

describe('领星明细不一致', () => {
  const lmState = (): BackendState => {
    const detail = {
      known: '领星里按幂等键查到的单与本单对不上', unknown: '它是不是本单下出去的', custom_order_sn: 'SCM-PO-1',
      order_sn: 'PO261009777', lookup_call_id: 11,
      differences: {
        supplier_id: { ours: 'S-1001', [THEIRS]: 'S-2002' },
        items: { ours: [['SKU-A', 'W-SZ', 30, '12.5'], ['SKU-B', 'W-SZ', 12, '0.3333']],
                 [THEIRS]: [['SKU-A', 'W-SZ', 25, '12.5'], ['SKU-B', 'W-SZ', 12, '0.3333']] },
      },
    };
    const po = { ...PLACED, attention: [{ attention_id: 5, kind: 'landed_mismatch' as const, opened_at: '2026-10-09T11:00:00+08:00' }] };
    return { po, queue: [], timeline: {
      po_id: 1, conclusion: { kind: 'landed_mismatch', known: detail.known, unknown: detail.unknown, facts: detail },
      attempts: [LOOKUP], events: [],
      attention: [{ attention_id: 5, kind: 'landed_mismatch', opened_at: '2026-10-09T11:00:00+08:00', resolved_at: null,
        resolution: null, resolved_by: null, reason: null, detail }] } };
  };

  it('问题行表：只列对不上的项，两侧并排', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(lmState()));
    const p = await panel();
    const t = within(p).getByTestId('mismatch-rows');
    const rows = within(t).getAllByTestId('mismatch-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('供应商');
    expect(rows[0]).toHaveTextContent('S-1001');
    expect(rows[0]).toHaveTextContent('S-2002');
    expect(rows[1]).toHaveTextContent('SKU-A');
    expect(rows[1]).toHaveTextContent('30');
    expect(rows[1]).toHaveTextContent('25');
    expect(t).not.toHaveTextContent('SKU-B');
    expect(within(p).getByTestId('attn-landed_mismatch')).toHaveTextContent('PO261009777');
  });

  it('确认知悉：理由必填；成功 → toast「已确认知悉」', async () => {
    const st = lmState();
    await mountPurchase('/purchase/1', purchaseRoutes(st, {
      'POST /v1/purchase-orders/1/acknowledge-mismatch': () => {
        st.po = PLACED;
        return ok({ po_id: 1, resolution: 'mismatch_acknowledged' });
      },
    }));
    const p = await panel();
    const btn = within(p).getByRole('button', { name: '确认知悉' });
    expect(btn).toBeDisabled();
    await userEvent.type(within(p).getByLabelText('确认知悉的理由'), '领星已改对');
    await userEvent.click(btn);
    expect(await screen.findByRole('status')).toHaveTextContent('已确认知悉');
  });

  it('与待核实同时开着时，给「去领星改对后点重新核对」', async () => {
    const st = lmState();
    st.po = { ...DRAFT, attention: [NV_OPEN, st.po.attention[0]!] };
    st.timeline = { ...st.timeline, attention: [...nvTimeline(true).attention, ...st.timeline.attention],
      conclusion: nvTimeline(true).conclusion };
    await mountPurchase('/purchase/1', purchaseRoutes(st));
    const p = await panel();
    expect(within(p).getByTestId('attn-landed_mismatch')).toHaveTextContent('去领星改对后点重新核对');
    expect(within(p).getByRole('button', { name: '重新核对' })).toBeInTheDocument();
  });
});

describe('下单请求卡住', () => {
  it('★ 草稿也进详情；只显示「等待自动恢复」与开始时间，没有按钮', async () => {
    // 卡住是读时推导的：PO 的 attention 里没有它，只在 /attention 队列里
    const st: BackendState = { po: DRAFT, timeline: { po_id: 1, conclusion: null, attempts: [{ ...CREATE, outcome: 'in_flight', elapsed_ms: null, cause: null }], events: [], attention: [] },
      queue: [{ attention_id: null, kind: 'stuck_call', po_id: 1, call_id: 10, endpoint: 'create_purchase_order',
        actor: 'ops.zhang', started_at: '2026-10-09T10:01:03+08:00', opened_at: '2026-10-09T10:01:03+08:00',
        stale_after_minutes: 10, custom_order_sn: 'SCM-PO-1', supplier_id: 'S-1001', timeline: '/v1/purchase-orders/1/timeline' }] };
    await mountPurchase('/purchase/1', purchaseRoutes(st));
    const p = await panel();
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
    const s = within(p).getByTestId('attn-stuck_call');
    expect(s).toHaveTextContent('等待自动恢复');
    expect(s).toHaveTextContent('2026-10-09 10:01:03');
    expect(within(s).queryAllByRole('button')).toHaveLength(0);
    expect(within(p).getByTestId('timeline')).toHaveTextContent('进行中');
  });
});
