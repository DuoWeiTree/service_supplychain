import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import type {
  AttentionQueue, CandidateLines, LineInPurchase, LineTrace, PoList, PurchasePlans, PoTimeline, PreflightResult, PurchaseOptions,
  PurchaseOrder, SubmitResult, CellResult, DispatchGate, DispatchPlans, SheetDetail, SheetGrid, SheetList, SheetPreflight,
} from './types';
import submitResubmit from '../testing/fixtures/contract/submit-resubmit.json';
import submitBlocked from '../testing/fixtures/contract/submit-lines-in-purchase.json';
import { ApiError } from './client';
import { SubmitPanel } from '../pages/SubmitPanel';
import poDraft from '../testing/fixtures/contract/po-draft.json';
import poNeedsVerify from '../testing/fixtures/contract/po-needs-verify.json';
import poPlaced from '../testing/fixtures/contract/po-placed.json';
import poList from '../testing/fixtures/contract/po-list.json';
import preflight from '../testing/fixtures/contract/preflight.json';
import timelineNv from '../testing/fixtures/contract/timeline-needs-verify.json';
import attention from '../testing/fixtures/contract/attention.json';
import options from '../testing/fixtures/contract/purchase-options.json';
import planLines from '../testing/fixtures/contract/plan-lines.json';
import lineTrace from '../testing/fixtures/contract/line-trace.json';
import purchasePlans from '../testing/fixtures/contract/purchase-plans.json';
import sheetList from '../testing/fixtures/contract/sheet-list.json';
import dispatchPlans from '../testing/fixtures/contract/dispatch-plans.json';
import sheetDetail from '../testing/fixtures/contract/sheet-detail.json';
import sheetGrid from '../testing/fixtures/contract/sheet-grid.json';
import sheetCell from '../testing/fixtures/contract/sheet-cell.json';
import sheetPreflight from '../testing/fixtures/contract/sheet-preflight.json';
import sheetBlocked from '../testing/fixtures/contract/sheet-confirm-blocked.json';
import { fromPo, toBody, updateLine } from '../pages/purchase/poEditorModel';
import { ok, stubBackend, type Handler } from '../testing/backendStub';
import { mountPurchase } from '../testing/purchaseBackend';

/** ★ 契约：这些 JSON 是真后端吐出来的（tests/test_web_contract.py 写、并按类型骨架守着）。
 *  手写的桩与夹具会和界面「互相印证」地一起错 —— purchaser_id / opt_uid / wid 后端是整数，
 *  前端曾一直按字符串写，编辑器对一个数 .trim() 就炸（final review C1）。这里拿真形状过类型、过模型、过页面。 */

// 编译期：JSON 推断不出字面量联合（'草稿' / 0|1|2），所以先把串 / 数放宽；类别（数 vs 串 vs null）与缺键必须对得上
type Widen<T> = T extends string ? string : T extends number ? number
  : T extends readonly (infer U)[] ? Widen<U>[]
  : T extends object ? { [K in keyof T]: Widen<T[K]> }
  : T;
const typed = {
  poDraft: poDraft satisfies Widen<PurchaseOrder>,
  poNeedsVerify: poNeedsVerify satisfies Widen<PurchaseOrder>,
  poPlaced: poPlaced satisfies Widen<PurchaseOrder>,
  poList: poList satisfies Widen<PoList>,
  preflight: preflight satisfies Widen<PreflightResult>,
  timelineNv: timelineNv satisfies Widen<PoTimeline>,
  attention: attention satisfies Widen<AttentionQueue>,
  options: options satisfies Widen<PurchaseOptions>,
  planLines: planLines satisfies Widen<CandidateLines>,
  lineTrace: lineTrace satisfies Widen<LineTrace>,
  purchasePlans: purchasePlans satisfies Widen<PurchasePlans>,
  submitResubmit: submitResubmit satisfies Widen<SubmitResult>,
  submitBlocked: submitBlocked satisfies Widen<{ error: string; hint: string; plan_id: number; lines: LineInPurchase[] }>,
  sheetList: sheetList satisfies Widen<SheetList>,
  dispatchPlans: dispatchPlans satisfies Widen<DispatchPlans>,
  sheetDetail: sheetDetail satisfies Widen<SheetDetail>,
  sheetGrid: sheetGrid satisfies Widen<SheetGrid>,
  sheetCell: sheetCell satisfies Widen<CellResult>,
  sheetPreflight: sheetPreflight satisfies Widen<SheetPreflight>,
  sheetBlocked: sheetBlocked satisfies Widen<{ error: string; hint: string; gates: DispatchGate[]; gate_run: number }>,
};

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../api'); vi.restoreAllMocks(); });

const draft = typed.poDraft as unknown as PurchaseOrder;
const nv = typed.poNeedsVerify as unknown as PurchaseOrder;
const placed = typed.poPlaced as unknown as PurchaseOrder;
const emptyTl = (po_id: number): PoTimeline => ({ po_id, conclusion: null, attempts: [], events: [], attention: [] });

function routes(over: Record<string, Handler> = {}): Record<string, Handler> {
  const r: Record<string, Handler> = {
    'GET /v1/attention': () => ok(attention),
    'GET /v1/sellers': () => ok({ sellers: [] }),
    'GET /v1/purchase-options': () => ok(options),
    'GET /v1/plan-lines': () => ok(planLines),
  };
  for (const po of [draft, nv, placed]) {
    r[`GET /v1/purchase-orders/${po.po_id}`] = () => ok(po);
    r[`GET /v1/purchase-orders/${po.po_id}/timeline`] = () => ok(po.po_id === nv.po_id ? timelineNv : emptyTl(po.po_id));
  }
  r[`GET /v1/purchase-orders/${draft.po_id}/preflight`] = () => ok(preflight);
  for (const id of new Set([draft, nv, placed].flatMap((p) => p.lines.flatMap((l) => l.sources.map((s) => s.line_id))))) {
    r[`GET /v1/plan-lines/${id}/trace`] = () => ok({ ...lineTrace, line_id: id });
  }
  return { ...r, ...over };
}

describe('契约样本 · 编辑器模型', () => {
  it('★ 真后端的草稿：载入 → 改 → toBody 不炸，id 原样是整数', () => {
    const r = toBody(updateLine(fromPo(draft), 0, { price: '3.1' }));
    if (!r.ok) throw new Error(JSON.stringify(r));
    expect(r.body.purchaser_id).toBe(draft.purchaser_id);
    expect(r.body.opt_uid).toBe(draft.opt_uid);
    expect(r.body.lines[0]!.wid).toBe(draft.lines[0]!.wid);
    expect(typeof r.body.purchaser_id).toBe('number');
    expect(typeof r.body.lines[0]!.wid).toBe('number');
  });
});

describe('契约样本 · 页面', () => {
  it('★ 已保存草稿改一格再保存：PUT 体的 id 是整数，屏上没有错误', async () => {
    let put: unknown = null;
    await mountPurchase(`/purchase/${draft.po_id}`, routes({
      [`PUT /v1/purchase-orders/${draft.po_id}`]: (b) => { put = b; return ok({ po_id: draft.po_id, draft_version: draft.draft_version + 1 }); },
    }));
    const line = await screen.findByTestId('po-line-1');
    // 下拉回填的就是后端的值（数转成文本后仍是已知选项，不掉进「手填」）
    expect(screen.queryByLabelText('采购方（手填）')).toBeNull();
    // 单头仓（PB-D25）：回填的就是后端明细上的仓
    expect((screen.getByLabelText('仓库') as HTMLSelectElement).value).toBe(String(draft.lines[0]!.wid));
    await userEvent.clear(within(line).getByLabelText('单价'));
    await userEvent.type(within(line).getByLabelText('单价'), '3.1');
    await userEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByRole('status')).toHaveTextContent('已保存草稿');
    expect(put).toMatchObject({ purchaser_id: draft.purchaser_id, opt_uid: draft.opt_uid, draft_version: draft.draft_version,
      lines: [{ wid: draft.lines[0]!.wid, price: '3.1' }] });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('待核实的单：详情 + 红框，结论来自真时间线', async () => {
    await mountPurchase(`/purchase/${nv.po_id}`, routes());
    const p = await screen.findByTestId('attention-panel');
    expect(within(p).getByTestId('conclusion')).toHaveTextContent(typed.timelineNv.conclusion.unknown);
    expect(within(p).getAllByTestId('attempt')).toHaveLength(typed.timelineNv.attempts.length);
  });

  it('★ 真后端的待采购记录带进编辑器：说出来自哪张计划哪一版，来源链带计划名', async () => {
    const l = typed.planLines.lines[0]!;
    await mountPurchase(`/purchase/new?lines=${l.line_id}`, routes());
    expect(await screen.findByTestId('source-summary'))
      .toHaveTextContent(`来自 销售计划「${l.plan_title}」rev${l.rev} 的 1 条记录`);
    expect(screen.getByTestId(`cand-${l.line_id}`)).toHaveTextContent(`${l.plan_title} rev${l.rev} ${l.period}`);
  });

  it('★ 真后端的 purchase/plans：计划抬头出数，草稿 chip 链到草稿，无事可做的计划点名', async () => {
    const pp = typed.purchasePlans as unknown as PurchasePlans;
    stubBackend({ ...routes(), 'GET /v1/purchase/plans': () => ok(pp), 'GET /v1/purchase-orders': () => ok(poList),
      'GET /v1/checks/purchase': () => ok({ ok: true, counts: {}, violations: [] }) });
    const { ApiError: E } = await import('./client');
    const { createHttpApi } = await import('./http');
    vi.doMock('../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError: E }));
    const { PurchaseHome } = await import('../pages/purchase/PurchaseHome');
    render(<MemoryRouter initialEntries={['/purchase']}><PurchaseHome /></MemoryRouter>);
    const p = pp.plans[0]!;
    expect(await screen.findByTestId(`plan-${p.plan_id}`)).toHaveTextContent(`已组单（含草稿）${p.confirmed}`);
    expect(screen.getByTestId(`draft-chip-${p.drafts[0]!.po_id}`)).toHaveAttribute('href', `/purchase/${p.drafts[0]!.po_id}`);
    expect(screen.getByTestId('todo-dropped')).toHaveTextContent(`另有 ${pp.excluded.nothing_to_order.length} 个计划`);
    expect(within(screen.getByTestId('unplanned-drafts')).getAllByRole('link')).toHaveLength(pp.unplanned_drafts.length);
  });

  it('已下单的单：详情两轴、领星单号', async () => {
    await mountPurchase(`/purchase/${placed.po_id}`, routes());
    expect(await screen.findByTestId('po-axes')).toHaveTextContent('已下单');
    expect(screen.getByTestId('po-detail-head')).toHaveTextContent(placed.order_sn!);
  });
});

describe('契约样本 · 提交结果', () => {
  it('★ 真后端的再提交响应：四个数、拆分冻结、已完结留着、空与 0 分开', () => {
    const r = typed.submitResubmit as unknown as SubmitResult;
    render(<MemoryRouter><SubmitPanel planId={1} report={r} blocked={null} sellers={[]} /></MemoryRouter>);
    const report = screen.getByTestId('submit-report');
    expect(within(report).getByTestId('count-kept')).toHaveTextContent(`保留 ${r.kept.count}`);
    expect(within(report).getByTestId('count-removed')).toHaveTextContent(`去掉 ${r.removed.count}`);
    expect(within(report).getByTestId('replaced-detail')).toHaveTextContent(
      `${r.replaced.lines[0]!.total_units.from} → ${r.replaced.lines[0]!.total_units.to}`);
    expect(within(report).getByTestId('split_frozen-detail')).toHaveTextContent(r.split_frozen.lines[0]!.sku);
    expect(within(report).getByTestId('left_completed-detail')).toHaveTextContent(`rev ${r.left_completed.lines[0]!.rev}`);
    expect(within(report).getByTestId('skip-empty_purchase')).toBeInTheDocument();
    expect(within(report).getByTestId('skip-zero_purchase')).toBeInTheDocument();
  });

  it('★ 真后端的 409 lines_in_purchase：采购单号可点', () => {
    const { error, hint, ...fields } = typed.submitBlocked;
    const lines = fields.lines as LineInPurchase[];
    render(<MemoryRouter><SubmitPanel planId={1} report={null} sellers={[]}
      blocked={{ err: new ApiError(409, error, hint, fields), lines }} /></MemoryRouter>);
    const po = lines.find((l) => l.po_ids.length > 0)!.po_ids[0]!;
    expect(within(screen.getByTestId('lines-in-purchase')).getByRole('link', { name: `采购单 ${po}` }))
      .toHaveAttribute('href', `/purchase/${po}`);
  });
});
