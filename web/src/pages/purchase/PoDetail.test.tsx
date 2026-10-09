import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen, within } from '@testing-library/react';
import { fail } from '../../testing/backendStub';
import { DRAFT, EMPTY_TIMELINE, PLACED, createAttempt, mountPurchase, purchaseRoutes, type BackendState } from '../../testing/purchaseBackend';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

const placed = (): BackendState => ({ po: PLACED, timeline: EMPTY_TIMELINE, queue: [] });

describe('采购单详情', () => {
  it('头部两轴并排：本地状态 | 领星状态「未读回」；领星单号、幂等键', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed()));
    expect(await screen.findByRole('heading', { name: '采购单 SCM-PO-1' })).toBeInTheDocument();
    const axes = screen.getByTestId('po-axes');
    expect(axes).toHaveTextContent('本地状态已下单');
    expect(axes).toHaveTextContent('领星状态未读回');
    const head = screen.getByTestId('po-detail-head');
    expect(head).toHaveTextContent('PO261009001');
    expect(head).toHaveTextContent('SCM-PO-1');
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

  it('反查失败只落在那一条来源上，明细照常', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(placed(), {
      'GET /v1/plan-lines/102/trace': () => fail(404, 'line_not_found', '没有这条记录', { line_id: 102 }),
    }));
    const rows = await screen.findAllByTestId('detail-line');
    expect(await within(rows[1]!).findByText('反查取不到')).toBeInTheDocument();
    expect(await within(rows[0]!).findByTestId('trace-101')).toHaveTextContent('已确认 30');
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
