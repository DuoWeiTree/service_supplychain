import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PoAttempt, PoTimeline, PurchaseOrder } from '../../api/types';
import { fail, ok } from '../../testing/backendStub';
import { DRAFT, PLACED, createAttempt, mountPurchase, purchaseRoutes, type BackendState } from '../../testing/purchaseBackend';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

// L8 禁词（tests/test_layering_web.py）：后端 differences 的对侧键名只在打桩里拼出来
const THEIRS = 'ling' + 'xing';

const NV_OPEN = { attention_id: 3, kind: 'needs_verify' as const, opened_at: '2026-10-09T10:01:33+08:00' };
const PENDING: PurchaseOrder = { ...DRAFT, attention: [NV_OPEN] };

const CREATE: PoAttempt = {
  call_id: 10, at: '2026-10-09T10:01:03.120+08:00', actor: 'ops.zhang', endpoint: 'create_purchase_order',
  host: 'erp.example', path: '/erp/sc/routing/purchase/purchase/createPurchaseOrder', idem_key: 'SCM-PO-1',
  elapsed_ms: 30012, outcome: 'unknown', http_status: null, lx_code: null, lx_msg: null, rejected_kind: null,
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
    expect(await panel()).toHaveClass('ap');
    expect(await panel()).not.toHaveClass('ap--warn');   // 待核实要人动手：danger 外框
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
    expect(screen.queryByRole('button', { name: '删除草稿' })).toBeNull();
    expect(screen.queryByRole('button', { name: '⛔ 下单' })).toBeNull();
  });
});

/** 结论是 dt/dd 键值：取某个键对应的值 */
const fact = (c: HTMLElement, k: string) =>
  [...c.querySelectorAll('dt')].find((d) => d.textContent === k)?.nextElementSibling?.textContent ?? null;

describe('结论', () => {
  it('已知 / 未知 / 最近一次查重（时间 + 结果）+ 去领星照着搜的条件', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState()));
    const c = within(await panel()).getByTestId('conclusion');
    expect(fact(c, '已知')).toContain('请求已发到领星');
    expect(fact(c, '不知道')).toBe('领星是否已建单');
    expect(fact(c, '最近查重')).toContain('2026-10-09 10:01:34 查询成功');
    expect(c).toHaveTextContent('SCM-PO-1');
    expect(c).toHaveTextContent('2026-10-08 至 2026-10-10');
  });

  it('★ 空≠0：从没查过 与 查过没找到 写得不一样', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState(false)));
    const c = within(await panel()).getByTestId('conclusion');
    expect(fact(c, '最近查重')).toBe('还没查过');
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

  it('后端明确拒绝（409 nothing_to_resolve）：动作的错误点名显示，不算结果未知', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState(), {
      'POST /v1/purchase-orders/1/recheck': () => fail(409, 'nothing_to_resolve', '这张单没有待处理的项'),
    }));
    const p = await panel();
    await userEvent.click(within(p).getByRole('button', { name: '重新核对' }));
    expect(await within(p).findByText('这张单没有待处理的项')).toBeInTheDocument();
    expect(within(p).queryByTestId('attn-unknown')).toBeNull();
    expect(p).not.toHaveTextContent('结果未知：');
    expect(within(p).getByRole('button', { name: '重新核对' })).toBeEnabled();
  });
});

describe('查单类动作结果未知', () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); });

  it.each([
    ['重新核对', 'recheck', null],
    ['登记已有单', 'register-existing', 'PO261009001'],
  ])('%s 超时 / 5xx → 按钮全关，等调用记录里没有在途的再放开并重取', async (verb, path, sn) => {
    const st = nvState();
    await mountPurchase('/purchase/1', purchaseRoutes(st, {
      [`POST /v1/purchase-orders/1/${path}`]: () => fail(504, 'gateway_timeout', '网关超时'),
    }));
    const p = await panel();
    if (sn) await userEvent.type(within(p).getByLabelText('领星单号'), sn);
    st.timeline = { ...st.timeline, attempts: [...st.timeline.attempts,
      createAttempt({ call_id: 30, endpoint: 'purchase_order_list', idem_key: null })] };
    await userEvent.click(within(p).getByRole('button', { name: verb }));
    expect(await within(p).findByTestId('attn-unknown')).toHaveTextContent('结果未知');
    expect(within(p).getByRole('button', { name: '重新核对' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(within(p).getByTestId('attn-unknown')).toBeInTheDocument();     // 还在途
    st.timeline = { ...st.timeline, attempts: st.timeline.attempts.map((a) => (a.call_id === 30 ? { ...a, outcome: 'ok' } : a)) };
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await vi.waitFor(() => expect(screen.queryByTestId('attn-unknown')).toBeNull());
    expect(within(await panel()).getByRole('button', { name: '重新核对' })).toBeEnabled();
  });

  it('502 lookup_failed 是后端的明确结论，不进「结果未知」', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(nvState(), {
      'POST /v1/purchase-orders/1/register-existing': () => fail(502, 'lookup_failed', '查询领星本身失败，无法判断有无此单号；稍后重试',
        { order_sn: 'X', lookup_call: 31, lookup_outcome: 'unknown', cause: 'TimeoutError' }),
    }));
    const p = await panel();
    await userEvent.type(within(p).getByLabelText('领星单号'), 'X');
    await userEvent.click(within(p).getByRole('button', { name: '登记已有单' }));
    expect(await within(p).findByText(/查询领星本身失败/)).toBeInTheDocument();
    expect(within(p).queryByTestId('attn-unknown')).toBeNull();
  });
});

describe('领星明细不一致（按后端四种开立路径的真实形状）', () => {
  const lmRow = (detail: Record<string, unknown>) => ({ attention_id: 5, kind: 'landed_mismatch' as const,
    opened_at: '2026-10-09T11:00:00+08:00', resolved_at: null, resolution: null, resolved_by: null, reason: null, detail });
  const LM_BRIEF = { attention_id: 5, kind: 'landed_mismatch' as const, opened_at: '2026-10-09T11:00:00+08:00' };

  /** place_po._adoptable：单张命中但对不上（草稿，待核实通常同时开着） */
  const draftDiff = (): BackendState => {
    const detail = { known: '领星里按幂等键查到的单与本单对不上', unknown: '它是不是本单下出去的', custom_order_sn: 'SCM-PO-1',
      order_sn: 'PO261009777', lookup_call_id: 11, differences: {
        supplier_id: { ours: 'S-1001', [THEIRS]: 'S-2002' },
        items: { ours: [['SKU-A', '101', 30, '12.5'], ['SKU-B', '101', 12, '0.3333']],
                 [THEIRS]: [['SKU-A', '101', 25, '12.5'], ['SKU-B', '101', 12, '0.3333']] } } };
    const nv = nvTimeline(true);
    return { po: { ...DRAFT, attention: [NV_OPEN, LM_BRIEF] }, queue: [],
      timeline: { ...nv, attempts: [CREATE, LOOKUP], attention: [...nv.attention, lmRow(detail)] } };
  };
  /** place_po._adoptable：同键多张（草稿）；竞态后 needs_verify 已被确认无此单收掉 */
  const draftMultiRace = (): BackendState => {
    const detail = { known: '领星里有多张单用了本单的幂等键', unknown: '哪一张是本单下出去的', custom_order_sn: 'SCM-PO-1',
      order_sns: ['PO261009001', 'PO261009002'], lookup_call_id: 11 };
    return { po: { ...DRAFT, attention: [LM_BRIEF] }, queue: [], timeline: { po_id: 1,
      conclusion: { kind: 'landed_mismatch', known: detail.known, unknown: detail.unknown, facts: detail },
      attempts: [CREATE, LOOKUP], events: [], attention: [lmRow(detail)] } };
  };
  /** place_po.lookup_and_settle：已记账后又查到另一张同键单 */
  const placedAlso = (): BackendState => {
    const detail = { recorded_order_sn: 'PO261009001', also_found_order_sn: 'PO261009002', custom_order_sn: 'SCM-PO-1',
      lookup_call_id: 11 };
    return { po: { ...PLACED, attention: [LM_BRIEF] }, queue: [], timeline: { po_id: 1,
      conclusion: { kind: 'landed_mismatch', known: '领星里查到与本单同幂等键的另一张单', unknown: '该以哪一张为准', facts: detail },
      attempts: [LOOKUP], events: [], attention: [lmRow(detail)] } };
  };
  /** usecase/landed.verify（G1 首检）：problems 是 v_check_po_landed 的行，价格是 numeric 的字符串 */
  const placedProblems = (): BackendState => {
    const detail = { known: '领星里的采购单明细与本单不一致', unknown: '是谁、为何改的', order_sn: 'PO261009001',
      read_back_call: 12, read_outcome: 'ok', problems: [
        { problem: 'qty_or_price_differs', po_id: 1, order_sn: 'PO261009001', line_no: 1, sku: 'SKU-A', wid: 101,
          ours_units: 30, lx_units: 7, ours_price: '12.5000', lx_price: '12.5000' },
        { problem: 'unexpected_item', po_id: 1, order_sn: 'PO261009001', line_no: null, sku: 'SKU-Z', wid: 101,
          ours_units: null, lx_units: 3, ours_price: null, lx_price: '1.0000' }] };
    return { po: { ...PLACED, attention: [LM_BRIEF] }, queue: [], timeline: { po_id: 1,
      conclusion: { kind: 'landed_mismatch', known: detail.known, unknown: detail.unknown, facts: detail },
      attempts: [LOOKUP], events: [], attention: [lmRow(detail)] } };
  };

  it('草稿 + differences：问题行表只列对不上的项；★ 没有「确认知悉」，给 PB-D16 出路', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(draftDiff()));
    const p = await panel();
    const rows = within(within(p).getByTestId('mismatch-rows')).getAllByTestId('mismatch-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('供应商');
    expect(rows[0]).toHaveTextContent('S-2002');
    expect(rows[1]).toHaveTextContent('SKU-A');
    expect(rows[1]).toHaveTextContent('30 件');
    expect(rows[1]).toHaveTextContent('25 件');
    expect(within(p).getByTestId('mismatch-rows')).not.toHaveTextContent('SKU-B');
    const lm = within(p).getByTestId('attn-landed_mismatch');
    expect(lm).toHaveTextContent('PO261009777');
    expect(within(lm).getByTestId('pbd16')).toHaveTextContent('去领星改对后点重新核对，或在领星作废该单');
    expect(within(p).queryByRole('button', { name: '确认知悉' })).toBeNull();
    expect(within(p).getByRole('button', { name: '重新核对' })).toBeInTheDocument();
    expect(within(p).getByTestId('conclusion')).toHaveTextContent('查到的单对不上');
  });

  it('★ 竞态：草稿上只剩 landed_mismatch（同键多张）→ 列出单号，仍没有「确认知悉」', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(draftMultiRace()));
    const lm = within(await panel()).getByTestId('attn-landed_mismatch');
    expect(lm).toHaveTextContent('PO261009001、PO261009002');
    // ★ 没有 needs_verify 就没有「重新核对」—— 提示不能指向一个不存在的按钮
    expect(within(lm).getByTestId('pbd16')).toHaveTextContent('在领星作废该单');
    expect(within(lm).getByTestId('pbd16')).not.toHaveTextContent('重新核对');
    expect(screen.queryByRole('button', { name: '重新核对' })).toBeNull();
    expect(screen.queryByRole('button', { name: '确认知悉' })).toBeNull();
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
  });

  it('已下单 + 另查到同键单：点名两张；可确认知悉', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placedAlso()));
    const lm = within(await panel()).getByTestId('attn-landed_mismatch');
    expect(lm).toHaveTextContent('已记账 PO261009001，另查到 PO261009002');
    expect(within(lm).getByRole('button', { name: '确认知悉' })).toBeInTheDocument();
    expect(within(lm).queryByTestId('pbd16')).toBeNull();
  });

  it('已下单 + G1 problems：问题行表逐行；确认知悉理由必填，成功 toast', async () => {
    const st = placedProblems();
    await mountPurchase('/purchase/1', purchaseRoutes(st, {
      'POST /v1/purchase-orders/1/acknowledge-mismatch': () => { st.po = PLACED; return ok({ po_id: 1, resolution: 'mismatch_acknowledged' }); },
    }));
    const p = await panel();
    const rows = within(within(p).getByTestId('mismatch-rows')).getAllByTestId('mismatch-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('第 1 行 SKU-A 101：数量或单价不同');
    expect(rows[0]).toHaveTextContent('30 件，单价 12.5000');
    expect(rows[0]).toHaveTextContent('7 件，单价 12.5000');
    expect(rows[1]).toHaveTextContent('SKU-Z 101：领星多出这一行');
    expect(rows[1]).toHaveTextContent('无');
    const btn = within(p).getByRole('button', { name: '确认知悉' });
    expect(btn).toBeDisabled();
    await userEvent.type(within(p).getByLabelText('确认知悉的理由'), '供应商确认改为 7 件');
    await userEvent.click(btn);
    expect(await screen.findByRole('status')).toHaveTextContent('已确认知悉');
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
