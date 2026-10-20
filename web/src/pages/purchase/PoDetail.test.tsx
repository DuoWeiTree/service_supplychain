import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fail, ok } from '../../testing/backendStub';
import { DRAFT, EMPTY_TIMELINE, PLACED, createAttempt, mountPurchase, purchaseRoutes, type BackendState } from '../../testing/purchaseBackend';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

const placed = (): BackendState => ({ po: PLACED, timeline: EMPTY_TIMELINE, queue: [] });

const LX = (over: Record<string, unknown> = {}) => ({ status: 2, status_shipped: 1, quantity_real: null, quantity_receive: null,
  quantity_entry: null, source: 'api', captured_at: '2026-10-10T14:50:00+08:00', call_id: 9, ...over });
const RB = (over: Record<string, unknown> = {}) => ({ checked: true, read_outcome: 'ok', read_cause: null, read_back_call: 9,
  problems: [], lx: LX(), ...over });

describe('★ 领星状态：打开详情即现查', () => {
  it('已下单：打开就调 read-back，显示领星状态 + 到货状态 + 读回时间', async () => {
    const { calls } = await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'POST /v1/purchase-orders/1/read-back': () => ok(RB({ lx: LX({ status: 2, status_shipped: 2 }) })),
    }));
    const s = await screen.findByTestId('lx-status');
    await vi.waitFor(() => expect(s).toHaveTextContent('待到货 部分到货'));
    expect(s).toHaveTextContent('读回于 2026-10-10 14:50');
    expect(s).not.toHaveTextContent('未读回');
    expect(calls.filter((c) => c.path === '/v1/purchase-orders/1/read-back')).toHaveLength(1);
  });

  it('读回失败：仍显示上次读回的状态与时间，写明这次失败，原因进折叠区', async () => {
    const st = placed();
    st.po = { ...PLACED, lx: LX({ status: 2, status_shipped: null, captured_at: '2026-10-09T10:00:00+08:00' }) as never };
    await mountPurchase('/purchase/1', purchaseRoutes(st, {
      'POST /v1/purchase-orders/1/read-back': () => ok(RB({ checked: false, read_outcome: 'not_sent',
        read_cause: 'ConnectionRefusedError [Errno 111] @ proxy:3128', lx: st.po.lx })),
    }));
    const s = await screen.findByTestId('lx-status');
    await vi.waitFor(() => expect(screen.getByTestId('po-axes')).toHaveTextContent('这次读回失败（not_sent），显示的是上次读回的状态'));
    expect(s).toHaveTextContent('待到货');
    expect(s).toHaveTextContent('读回于 2026-10-09 10:00');
    expect(screen.getByTestId('lx-fail-cause')).toHaveTextContent('ConnectionRefusedError');
  });

  it('「重新读回」再查一次', async () => {
    let n = 0;
    const { calls } = await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'POST /v1/purchase-orders/1/read-back': () => { n += 1; return ok(RB({ lx: LX({ status: n === 1 ? 2 : 9, status_shipped: n === 1 ? 1 : 3 }) })); },
    }));
    const s = await screen.findByTestId('lx-status');
    await vi.waitFor(() => expect(s).toHaveTextContent('待到货 未到货'));
    await userEvent.click(screen.getByRole('button', { name: '重新读回' }));
    await vi.waitFor(() => expect(s).toHaveTextContent('完成 全部到货'));
    expect(calls.filter((c) => c.path === '/v1/purchase-orders/1/read-back')).toHaveLength(2);
  });

  it('认不出的状态码原样写码，不猜', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'POST /v1/purchase-orders/1/read-back': () => ok(RB({ lx: LX({ status: 777, status_shipped: null }) })),
    }));
    await vi.waitFor(async () => expect(await screen.findByTestId('lx-status')).toHaveTextContent('状态码 777'));
  });

  it('作废的待处理草稿不调 read-back', async () => {
    const { calls } = await mountPurchase('/purchase/1', purchaseRoutes({ po: { ...DRAFT, discarded_at: '2026-10-09T10:00:00+08:00' },
      timeline: EMPTY_TIMELINE, queue: [] }));
    expect(await screen.findByTestId('lx-status')).toHaveTextContent('未下单');
    expect(calls.some((c) => c.path.endsWith('/read-back'))).toBe(false);
  });
});

describe('采购单详情', () => {
  it('头部两轴并排：本地状态 | 领星状态「未读回」；领星单号', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed()));
    expect(await screen.findByRole('heading', { name: '采购单 SCM-PO-1' })).toBeInTheDocument();
    const axes = screen.getByTestId('po-axes');
    // 两轴并排不合成（StatusPair）
    expect(axes).toHaveTextContent('本地 已下单');
    expect(axes).toHaveTextContent('领星 未读回');
    const head = screen.getByTestId('po-detail-head');
    expect(head).toHaveTextContent('PO261009001');
    // 幂等键就是标题里的单号，不再重复一遍
    expect(head).not.toHaveTextContent('幂等键');
    expect(screen.queryByTestId('attention-panel')).toBeNull();
  });

  it('明细带领星行号（未回填写明）与来源反查的已确认 / 已下单', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed()));
    const rows = await screen.findAllByTestId('detail-line');
    expect(rows[0]).toHaveTextContent('88001');
    expect(rows[1]).toHaveTextContent('未回填');
    expect(rows[1]).toHaveTextContent('2');   // 无计划来源
    const tr = await within(rows[0]!).findByTestId('trace-101');
    expect(tr).toHaveTextContent('已确认 30');
    expect(tr).toHaveTextContent('已下单 0');
  });

  it('★ 来源链：计划 rev 货号 月份 → 店 件数，不等反查', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed(), { 'GET /v1/plan-lines/101/trace': () => new Promise(() => {}) }));
    const rows = await screen.findAllByTestId('detail-line');
    expect(rows[0]).toHaveTextContent('2026 Q4 销售计划 rev2 SKU-A 2026-10 → A4Pet-US 30 件');
  });

  it('★ 与编辑器说同一种话：供应商 / 采购员 / 仓 / 店写名字；单头补采购方 · 质检', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed()));
    const head = await screen.findByTestId('po-detail-head');
    await vi.waitFor(() => expect(head).toHaveTextContent('供应商 东莞宏达'));
    expect(head).toHaveTextContent('采购员 陈彩凤');
    expect(head).toHaveTextContent('采购方 111');
    expect(head).toHaveTextContent('质检 预检');
    expect(head).toHaveTextContent('仓 深圳本地仓');
    expect(screen.getAllByTestId('detail-line')[0]).toHaveTextContent('深圳本地仓');
    expect(head).not.toHaveTextContent('8085');
  });

  it('名称取不到：退回编号并点名', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'GET /v1/purchase-options': () => fail(500, 'boom', '选项取不到'),
    }));
    expect(await screen.findByTestId('names-failed')).toHaveTextContent('名称取不到，按编号显示');
    expect(screen.getByTestId('po-detail-head')).toHaveTextContent('供应商 S-1001');
  });

  it('★ 明细合计：N 行 · N 件 · 金额', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed()));
    // 30 × 12.5 + 12 × 0.3333 = 375 + 3.9996
    expect(await screen.findByTestId('detail-sum')).toHaveTextContent('2 行，42 件，金额 378.9996');
  });

  it('★ UX-D6：面包屑「采购」回列表并定位到来源计划', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed()));
    const crumbs = await screen.findByTestId('po-crumbs');
    expect(within(crumbs).getByRole('link', { name: '采购' })).toHaveAttribute('href', '/purchase?plan=1');
  });

  it('反查失败只落在那一条来源上，明细照常', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'GET /v1/plan-lines/102/trace': () => fail(404, 'line_not_found', '没有这条记录', { line_id: 102 }),
    }));
    const rows = await screen.findAllByTestId('detail-line');
    expect(await within(rows[1]!).findByText('反查取不到')).toBeInTheDocument();
    expect(await within(rows[0]!).findByTestId('trace-101')).toHaveTextContent('已确认 30');
  });

  it('读失败可以就地重新载入', async () => {
    let n = 0;
    await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'GET /v1/purchase-orders/1/timeline': () => (n++ < 2 ? fail(500, 'internal', '服务端错误') : ok(EMPTY_TIMELINE)),
    }));
    expect(await screen.findByText('服务端错误')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '重新载入' }));
    await vi.waitFor(() => expect(screen.queryByText('服务端错误')).toBeNull());
  });

  it('时间线取不到：点名错误，单头明细照常', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'GET /v1/purchase-orders/1/timeline': () => fail(500, 'internal', '服务端错误'),
    }));
    expect(await screen.findByText('服务端错误')).toBeInTheDocument();
    expect(screen.getAllByTestId('detail-line')).toHaveLength(2);
  });
});

describe('★ 失败即关闭：判不清就不给编辑器', () => {
  const draft = (): BackendState => ({ po: DRAFT, timeline: EMPTY_TIMELINE, queue: [] });

  it('调用记录取不到 → 进详情（点名取不到），不进编辑器', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(draft(), {
      'GET /v1/purchase-orders/1/timeline': () => fail(500, 'internal', '服务端错误'),
    }));
    expect(await screen.findByText('服务端错误')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
    expect(screen.queryByRole('button', { name: '⛔ 下单' })).toBeNull();
  });

  it('待处理队列取不到 → 进详情', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(draft(), {
      'GET /v1/attention': () => fail(503, 'unavailable', '队列不可用'),
    }));
    expect(await screen.findByText('待处理队列取不到，卡住的调用可能没显示')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
  });
});

describe('下单在途（未卡住，任何年龄）', () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); });

  it('★ 草稿有在途下单调用 → 详情「下单进行中」，没有 ⛔；落成 rejected → toast 说出来并回编辑器', async () => {
    const st: BackendState = { po: DRAFT, queue: [], timeline: { ...EMPTY_TIMELINE, attempts: [createAttempt()] } };
    await mountPurchase('/purchase/1', purchaseRoutes(st));
    const s = await screen.findByTestId('attn-placing');
    expect(s).toHaveTextContent('下单进行中');
    expect(s).toHaveTextContent('2026-10-09 10:01:03');
    expect(within(s).queryAllByRole('button')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: '⛔ 下单' })).toBeNull();
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull();
    st.timeline = { ...EMPTY_TIMELINE, attempts: [createAttempt({ outcome: 'rejected', lx_code: '1001', lx_msg: '供应商停用' })] };
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(await screen.findByRole('status')).toHaveTextContent('领星拒绝了这张单（code 1001）：供应商停用');
    expect(await screen.findByRole('button', { name: '保存草稿' })).toBeInTheDocument();
  });

  it('★ 在途下单落成授权类 rejected（code 403）→ toast 说改草稿没用、点名接口', async () => {
    const st: BackendState = { po: DRAFT, queue: [], timeline: { ...EMPTY_TIMELINE, attempts: [createAttempt()] } };
    await mountPurchase('/purchase/1', purchaseRoutes(st));
    await screen.findByTestId('attn-placing');
    st.timeline = { ...EMPTY_TIMELINE, attempts: [createAttempt({ outcome: 'rejected', lx_code: '403',
      lx_msg: '授权失效，请更新授权有效期或检查权限', rejected_kind: 'auth' })] };
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    const t = await screen.findByRole('status');
    expect(t).toHaveTextContent('改草稿没用');
    expect(t).toHaveTextContent('/erp/x/createPurchaseOrder');
    expect(t).not.toHaveTextContent('领星拒绝了这张单');
  });
});

describe('状态记录', () => {
  it('po_event 与已处理项（谁 · 何时 · 理由）在折叠区', async () => {
    const st = placed();
    st.timeline = { ...EMPTY_TIMELINE,
      events: [{ from: '草稿', to: '已下单', actor: 'ops.zhang', reason: null, src: 'place:7', at: '2026-10-09T10:01:33+08:00' }],
      attention: [{ attention_id: 3, kind: 'needs_verify', opened_at: '2026-10-09T10:01:00+08:00', resolved_at: '2026-10-09T10:05:00+08:00',
        resolution: 'found_existing', resolved_by: 'buyer.wang', reason: '人工登记领星单 PO261009001', detail: {} }] };
    await mountPurchase('/purchase/1', purchaseRoutes(st));
    const t = await screen.findByTestId('events');
    expect(t).toHaveTextContent('草稿 → 已下单');
    expect(t).toHaveTextContent('buyer.wang');
    expect(t).toHaveTextContent('人工登记领星单 PO261009001');
    expect(t.closest('details')).not.toHaveAttribute('open');
  });
});
