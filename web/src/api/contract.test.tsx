import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type {
  AttentionQueue, CandidateLines, LineTrace, PoList, PoTimeline, PreflightResult, PurchaseOptions, PurchaseOrder,
} from './types';
import poDraft from './fixtures/contract/po-draft.json';
import poNeedsVerify from './fixtures/contract/po-needs-verify.json';
import poPlaced from './fixtures/contract/po-placed.json';
import poList from './fixtures/contract/po-list.json';
import preflight from './fixtures/contract/preflight.json';
import timelineNv from './fixtures/contract/timeline-needs-verify.json';
import attention from './fixtures/contract/attention.json';
import options from './fixtures/contract/purchase-options.json';
import planLines from './fixtures/contract/plan-lines.json';
import lineTrace from './fixtures/contract/line-trace.json';
import { fromPo, toBody, updateLine } from '../pages/purchase/poEditorModel';
import { ok, type Handler } from '../testing/backendStub';
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
    expect((within(line).getByLabelText('仓') as HTMLSelectElement).value).toBe(String(draft.lines[0]!.wid));
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

  it('已下单的单：详情两轴、领星单号', async () => {
    await mountPurchase(`/purchase/${placed.po_id}`, routes());
    expect(await screen.findByTestId('po-axes')).toHaveTextContent('已下单');
    expect(screen.getByTestId('po-detail-head')).toHaveTextContent(placed.order_sn!);
  });
});
