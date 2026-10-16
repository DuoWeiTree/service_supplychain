import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { fail, ok } from '../testing/backendStub';
import { DRAFT, EMPTY_TIMELINE, PLACED, mountPurchase, purchaseRoutes, type BackendState } from '../testing/purchaseBackend';

/** 两条已被裁定过的文案纪律的守卫（终审 I7 + 建议 4）。
 *
 *  ★ 为什么要守：这两条在本分支各被裁定并修过一次（F16 让页头三段各自成句、
 *    Task 5 让 PlanAdd 的动词一名到底），而写在裁定之前的那两屏没有回头统一 ——
 *    OpsHome 与 usePlanGridSaves 就这样活到了终审。没有守卫，下一屏还会长回来。 */

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

async function app(entry: string) {
  const { ApiError } = await import('../api/client');
  const { createMemoryBackend } = await import('../testing/memoryBackend');
  vi.doMock('../api', () => ({ api: createMemoryBackend(), ApiError }));
  const [{ OpsHome }, { PlanGrid }, { PlanAdd }, { PlanRevs }] = await Promise.all([
    import('../pages/OpsHome'), import('../pages/PlanGrid'),
    import('../pages/PlanAdd'), import('../pages/PlanRevs'),
  ]);
  const { PurchaseHome } = await import('../pages/purchase/PurchaseHome');
  const { PoEditor, PoRoute } = await import('../pages/purchase/PoEditor');
  const utils = render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/" element={<OpsHome />} />
        <Route path="/plans/:planId" element={<PlanGrid />} />
        <Route path="/plans/:planId/add" element={<PlanAdd />} />
        <Route path="/plans/:planId/revs" element={<PlanRevs />} />
        <Route path="/purchase" element={<PurchaseHome />} />
        <Route path="/purchase/new" element={<PoEditor key="new" poId={null} />} />
        <Route path="/purchase/:poId" element={<PoRoute />} />
      </Routes>
    </MemoryRouter>,
  );
  // ★ 与 planFlow 的 app() 同法：本函数是 async，调用方 `await` 它的那一下就是一次
  //   微任务跳变，只发一个请求的页面（PlanRevs 的 listRevs）正好在 act() 之外落地。
  //   同一个根因在本轮已经诊断并修过两次，这第三个文件当时没跟上。
  await act(async () => {});
  return utils;
}

describe('文案纪律', () => {
  // ★ 四屏都要扫：终审抓到的 5 处里有 3 处在顶栏的 actor 下拉里（每一屏都有它）
  const screens: [string, string][] = [
    ['我的计划', '/'],
    ['计划编辑', '/plans/1'],
    ['添加货品', '/plans/1/add'],
    ['版本', '/plans/2/revs'],
    ['采购', '/purchase'],
    ['新建采购单', '/purchase/new'],
    ['新建采购单（从待采购带来）', '/purchase/new?lines=101,999'],
    ['采购单（不存在）', '/purchase/7'],
    ['草稿', '/purchase/1'],
    ['采购单详情', '/purchase/2'],
  ];

  for (const [name, entry] of screens) {
    it(`★ ${name}：整屏一个 \` · \` 元串都不许有`, async () => {
      const { unmount } = await app(entry);
      // 等这一屏真的出了内容再扫 —— 空屏上扫不出元串，那是假绿
      await screen.findAllByRole('heading');   // 采购首页有 h1 + 待采购 / 采购单两个 h2
      if (entry === '/purchase') { await screen.findByTestId('po-list'); await screen.findByTestId('todo-list'); }
      if (entry.startsWith('/purchase/new?')) { await screen.findByTestId('source-summary'); await screen.findByText('记录 999 不存在'); }
      if (entry === '/purchase/new' || entry === '/purchase/1') await screen.findByTestId('cand-sku-A4P-TOY-002');
      if (entry === '/purchase/1') await screen.findByTestId('po-line-1');
      if (entry === '/purchase/2') await screen.findAllByTestId('detail-line');
      const text = document.body.textContent ?? '';
      expect(text.length).toBeGreaterThan(50);
      expect(text).not.toContain(' · ');
      unmount();
    });
  }

  // ★ 一个动作一个名：按钮上的动词与 toast 里的动词必须同源。
  //   「删除货号」→「已移出 N 个 msku」是两个词，人会以为是两件事。
  // ★ Task 22：店铺行按钮原叫「移出货号」却只移这一家店 —— 按钮名本身就说错了事。
  //   现在三个按钮三个名，各自的 toast 用各自的名：移出货号 / 移出该店 / 移出 msku。
  const removals: [string, string, string][] = [
    ['移出货号', 'purchase-row-DCC1800264G1', '已移出货号 DCC1800264G1'],
    ['移出该店', 'block-11094-DCC1800264G1', '已移出该店'],
    ['移出 msku', 'block-11072-DCC1800264G1', '已移出 msku'],
  ];
  for (const [verb, testId, said] of removals) {
    it(`★ ${verb}：按钮说「${verb}」，toast 也说「${said}」`, async () => {
      await app('/plans/1');
      const scope = await screen.findByTestId(testId);
      await userEvent.click(within(scope).getAllByRole('button', { name: verb })[0]!);
      const toast = await screen.findByRole('status');
      expect(toast).toHaveTextContent(said);
      expect(toast.textContent).not.toContain('删除');
      expect(toast.textContent).not.toContain(' · ');
    });
  }

  it('★ 添加货品：按钮说「添加」，报告也说「已添加」', async () => {
    await app('/plans/1/add');
    await userEvent.type(screen.getByLabelText('搜货号'), 'MSKU');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    const boxes = await screen.findAllByRole('checkbox');
    await userEvent.click(boxes.find((b) => !(b as HTMLInputElement).disabled)!);
    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    const report = await screen.findByTestId('claim-report');
    expect(report).toHaveTextContent('已添加');
    expect(report.textContent).not.toContain('已认领');
  });

  it('★ 保存草稿：按钮说「保存草稿」，toast 说「已保存草稿」', async () => {
    await app('/purchase/1');
    await screen.findByTestId('po-line-1');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    const toast = await screen.findByRole('status');
    expect(toast).toHaveTextContent('已保存草稿');
    expect(toast.textContent).not.toContain(' · ');
  });

  it('★ 删除草稿：按钮说「删除草稿」，toast 说「已删除草稿」', async () => {
    await app('/purchase/1');
    await userEvent.click(await screen.findByRole('button', { name: '删除草稿' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '删除草稿' }));
    const toast = await screen.findByRole('status');
    expect(toast).toHaveTextContent('已删除草稿');
  });

  it('★ 逐店分配：直接打件数 +「剩余全给」，不再混一个「加入」', async () => {
    await app('/purchase/new');
    const rec = await screen.findByTestId('cand-101');
    expect(within(rec).getAllByRole('button', { name: '剩余全给' }).length).toBeGreaterThan(0);
    expect(within(rec).queryByRole('button', { name: '加入' })).toBeNull();
  });

  it('★ 新建销售计划：按钮说「新建」，表单提交按钮也说「新建」，不混「创建」', async () => {
    await app('/');
    await screen.findByTestId('plan-list');
    await userEvent.click(screen.getByRole('button', { name: '新建销售计划' }));
    expect(screen.getByRole('button', { name: '新建' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '创建' })).toBeNull();
  });
});

// ★ 下单与转人工：内存替身不模拟领星，这几屏与动词只能用 http 打桩看
describe('文案纪律：下单与转人工', () => {
  const NV = { attention_id: 3, kind: 'needs_verify' as const, opened_at: '2026-10-09T10:01:33+08:00' };
  const LM = { attention_id: 5, kind: 'landed_mismatch' as const, opened_at: '2026-10-09T11:00:00+08:00' };
  const pending = (): BackendState => ({
    po: { ...DRAFT, attention: [NV, LM] }, queue: [{ attention_id: null, kind: 'stuck_call', po_id: 1, call_id: 10,
      endpoint: 'create_purchase_order', actor: 'ops.zhang', started_at: '2026-10-09T10:01:03+08:00',
      opened_at: '2026-10-09T10:01:03+08:00', stale_after_minutes: 10, custom_order_sn: 'SCM-PO-1', supplier_id: 'S-1001',
      timeline: '/v1/purchase-orders/1/timeline' }],
    timeline: {
      po_id: 1,
      conclusion: { kind: 'needs_verify', known: '请求已发到领星，未收到可确认的结果', unknown: '领星是否已建单',
        last_lookup: { call_id: 11, at: '2026-10-09T10:01:34+08:00', outcome: 'ok', truncated: true, window: {} },
        how_to_search: { custom_order_sn: 'SCM-PO-1', window: { search_field_time: 'create_time', start_date: '2026-10-08', end_date: '2026-10-10' } } },
      attempts: [{ call_id: 10, at: '2026-10-09T10:01:03+08:00', actor: 'ops.zhang', endpoint: 'create_purchase_order',
        host: 'erp.example', path: '/x', idem_key: 'SCM-PO-1', elapsed_ms: 30012, outcome: 'unknown', http_status: 502,
        lx_code: null, lx_msg: null, rejected_kind: null, cause: 'http_502', retry_of: null, request: {}, response: null }],
      events: [],
      attention: [{ ...LM, resolved_at: null, resolution: null, resolved_by: null, reason: null,
        detail: { order_sns: ['PO1', 'PO2'], known: '领星里有多张单用了本单的幂等键', unknown: '哪一张是本单下出去的' } }],
    },
  });

  it('★ 红框（三类齐开）与下单弹窗：整屏一个 ` · ` 都不许有', async () => {
    const { unmount } = await mountPurchase('/purchase/1', purchaseRoutes(pending()));
    await screen.findByTestId('attn-stuck_call');
    expect(document.body.textContent).not.toContain(' · ');
    unmount();
    vi.restoreAllMocks();
    await mountPurchase('/purchase/1', purchaseRoutes({ po: DRAFT, timeline: EMPTY_TIMELINE, queue: [] }));
    await vi.waitFor(async () => expect(await screen.findByRole('button', { name: '⛔ 下单' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: '⛔ 下单' }));
    expect(screen.getByRole('dialog').textContent).not.toContain(' · ');
  });

  it('★ 确认下单：toast 说「已下单」', async () => {
    const st: BackendState = { po: DRAFT, timeline: EMPTY_TIMELINE, queue: [] };
    await mountPurchase('/purchase/1', purchaseRoutes(st, {
      'POST /v1/purchase-orders/1/place': () => { st.po = PLACED; return ok({ outcome: 'placed', po_id: 1, order_sn: 'PO9', erp_call_id: 1 }); },
    }));
    await vi.waitFor(async () => expect(await screen.findByRole('button', { name: '⛔ 下单' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: '⛔ 下单' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '⛔ 确认下单' }));
    const toast = await screen.findByRole('status');
    expect(toast).toHaveTextContent('已下单');
    expect(toast.textContent).not.toContain(' · ');
  });

  const actions: [string, string, string, string | null][] = [
    ['重新核对', 'recheck', '已重新核对', null],
    ['登记已有单', 'register-existing', '已登记', '领星单号'],
    ['确认领星无此单', 'confirm-absent', '已确认领星无此单', '确认无此单的理由'],
    ['确认知悉', 'acknowledge-mismatch', '已确认知悉', '确认知悉的理由'],
  ];
  const replies: Record<string, unknown> = {
    recheck: { outcome: 'placed', po_id: 1, order_sn: 'PO9', found_by: 'lookup' },
    'register-existing': { outcome: 'placed', po_id: 1, order_sn: 'PO9', found_by: 'manual' },
    'confirm-absent': { po_id: 1, unlocked: ['place', 'update', 'delete'] },
    'acknowledge-mismatch': { po_id: 1, resolution: 'mismatch_acknowledged' },
  };
  for (const [verb, path, said, input] of actions) {
    it(`★ ${verb}：toast 说「${said}」`, async () => {
      const st = pending();
      // ★ PB-D16：确认知悉只在已下单的单上有
      if (path === 'acknowledge-mismatch') st.po = { ...PLACED, attention: [LM] };
      await mountPurchase('/purchase/1', purchaseRoutes(st, {
        [`POST /v1/purchase-orders/1/${path}`]: () => ok(replies[path]),
      }));
      const panel = await screen.findByTestId('attention-panel');
      if (input) await userEvent.type(within(panel).getByLabelText(input), 'PO9');
      await userEvent.click(within(panel).getByRole('button', { name: verb }));
      const toast = await screen.findByRole('status');
      expect(toast).toHaveTextContent(said);
      expect(toast.textContent).not.toContain(' · ');
    });
  }

  it('写动作失败不出 toast（只有成功才用「已」字）', async () => {
    await mountPurchase('/purchase/1', purchaseRoutes(pending(), {
      'POST /v1/purchase-orders/1/recheck': () => fail(409, 'nothing_to_resolve', '这张单没有待处理的项'),
    }));
    const panel = await screen.findByTestId('attention-panel');
    await userEvent.click(within(panel).getByRole('button', { name: '重新核对' }));
    expect(await within(panel).findByText('这张单没有待处理的项')).toBeInTheDocument();
    expect(screen.queryByRole('status')).toBeNull();
  });
});
