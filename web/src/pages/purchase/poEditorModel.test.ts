import { describe, expect, it } from 'vitest';
import {
  addSource, available, baselineOf, emptyDraft, fromPo, gateText, groupCandidates, lineUnits,
  ownOnlyRecords, recordRemaining, removeSource, setHeader, setLineWid, toBody, unplannedUnits, updateLine, validate,
  type Draft,
} from './poEditorModel';
import type { CandidateLine, PurchaseOrder } from '../../api/types';

const cand = (over: Partial<CandidateLine> = {}): CandidateLine => ({
  line_id: 101, plan_id: 1, rev: 1, sku: 'SKU-A', period: '2026-10', total_units: 100,
  demand_at_submit: 60, demand_by_seller: {}, state: '已提交', confirmed_units: 0, ordered_units: 0,
  remaining: 100, blocked: null,
  sellers: {
    '11072': { expected: 40, cap: 67, used: 0, remaining: 67 },
    '11094': { expected: 20, cap: 33, used: 0, remaining: 33 },
  },
  ...over,
});
const NONE = baselineOf(null);

const okAdd = (d: Draft, c: CandidateLine, sid: string, n: number, wid?: string): Draft => {
  const r = addSource(d, c, sid, n, NONE, wid);
  if (!r.ok) throw new Error(`addSource refused: ${r.reason}`);
  return r.draft;
};

const filled = (): Draft => {
  let d = setHeader(emptyDraft(), { supplier_id: 'S-1', purchaser_id: '111', opt_uid: '10925905', qc_type: 0 });
  d = okAdd(d, cand(), '11072', 30);
  d = setLineWid(d, 0, 'W-SZ');
  return updateLine(d, 0, { price: '12.5' });
};

describe('来源合并', () => {
  it('加入来源 → 按货号建一行，数量默认 = 来源合计', () => {
    const d = okAdd(emptyDraft(), cand(), '11072', 30);
    expect(d.lines).toHaveLength(1);
    expect(d.lines[0]!.sku).toBe('SKU-A');
    expect(d.lines[0]!.sources).toEqual([{ line_id: 101, seller_id: '11072', units: 30 }]);
    expect(lineUnits(d.lines[0]!)).toBe(30);
    expect(unplannedUnits(d.lines[0]!)).toBe(0);
  });

  it('同货号再加：并进同一行；同记录同店的量相加，不出两条', () => {
    let d = okAdd(emptyDraft(), cand(), '11072', 30);
    d = okAdd(d, cand(), '11094', 10);
    d = okAdd(d, cand(), '11072', 5);
    expect(d.lines).toHaveLength(1);
    expect(d.lines[0]!.sources).toEqual([
      { line_id: 101, seller_id: '11072', units: 35 },
      { line_id: 101, seller_id: '11094', units: 10 },
    ]);
    expect(lineUnits(d.lines[0]!)).toBe(45);
  });

  it('不同货号各一行', () => {
    let d = okAdd(emptyDraft(), cand(), '11072', 30);
    d = okAdd(d, cand({ line_id: 102, sku: 'SKU-B' }), '11072', 5);
    expect(d.lines.map((l) => l.sku)).toEqual(['SKU-A', 'SKU-B']);
  });

  it('同货号两仓：改仓撞上同 (货号, 仓) → 两行合并', () => {
    let d = okAdd(emptyDraft(), cand(), '11072', 30);
    d = setLineWid(d, 0, 'W-SZ');
    d = okAdd(d, cand(), '11094', 10, 'W-NB');
    expect(d.lines.map((l) => l.wid)).toEqual(['W-SZ', 'W-NB']);
    d = setLineWid(d, 1, 'W-SZ');
    expect(d.lines).toHaveLength(1);
    expect(lineUnits(d.lines[0]!)).toBe(40);
  });

  it('超过该店剩余 → 拒绝并给出可加的量；本稿已加的量要算进去', () => {
    let d = okAdd(emptyDraft(), cand(), '11094', 30);
    const r = addSource(d, cand(), '11094', 5, NONE);
    expect(r).toEqual({ ok: false, reason: 'over_remaining', available: 3 });
    d = okAdd(d, cand(), '11094', 3);
    expect(available(cand(), '11094', d, NONE)).toBe(0);
  });

  it('已保存草稿：本单原有来源算回可用（后端 used 已含本单）', () => {
    const c = cand({ sellers: { '11072': { expected: 40, cap: 67, used: 67, remaining: 0 } } });
    const po = { lines: [{ sources: [{ line_id: 101, seller_id: '11072', units: 67 }] }] } as unknown as PurchaseOrder;
    const base = baselineOf(po);
    const d = fromPo({ ...po, supplier_id: 'S', purchaser_id: 'p', opt_uid: 'u', qc_type: 0,
      lines: [{ line_no: 1, sku: 'SKU-A', wid: 'W', units: 67, price: '1', expect_arrive_date: null,
        unplanned_reason: null, lx_item_id: null, sources: [{ line_id: 101, seller_id: '11072', units: 67 }],
        sources_units: 67, unplanned_units: 0 }] } as PurchaseOrder);
    expect(available(c, '11072', d, base)).toBe(0);
    const removed = removeSource(d, 0, 0);
    expect(available(c, '11072', removed, base)).toBe(67);
  });

  it('blocked / 剩余未知 / 非正整数 → 拒绝', () => {
    const blocked = cand({ blocked: { reason: 'no_seller_basis', sellers: ['11072'] },
      sellers: { '11072': { expected: null, cap: null, used: 0, remaining: null } } });
    expect(addSource(emptyDraft(), blocked, '11072', 1, NONE)).toMatchObject({ ok: false, reason: 'blocked' });
    expect(addSource(emptyDraft(), cand(), '99999', 1, NONE)).toMatchObject({ ok: false, reason: 'no_seller' });
    expect(addSource(emptyDraft(), cand(), '11072', 0, NONE)).toMatchObject({ ok: false, reason: 'bad_units' });
    expect(addSource(emptyDraft(), cand(), '11072', 1.5, NONE)).toMatchObject({ ok: false, reason: 'bad_units' });
  });

  it('移除最后一条来源且数量未手改 → 行消失；手改过数量 → 行留着（全是无计划来源）', () => {
    const d = okAdd(emptyDraft(), cand(), '11072', 30);
    expect(removeSource(d, 0, 0).lines).toHaveLength(0);
    const typed = updateLine(d, 0, { units: 50 });
    const left = removeSource(typed, 0, 0);
    expect(left.lines).toHaveLength(1);
    expect(unplannedUnits(left.lines[0]!)).toBe(50);
  });
});

describe('无计划来源', () => {
  it('数量改大 → 无计划来源 = 数量 − 来源合计；改回 null 跟随来源合计', () => {
    let d = okAdd(emptyDraft(), cand(), '11072', 30);
    d = updateLine(d, 0, { units: 50 });
    expect(unplannedUnits(d.lines[0]!)).toBe(20);
    d = updateLine(d, 0, { units: null });
    expect(unplannedUnits(d.lines[0]!)).toBe(0);
  });

  it('fromPo：数量等于来源合计的行回到「跟随」，加来源时数量一起涨', () => {
    const po = {
      po_id: 1, supplier_id: 'S', purchaser_id: 'p', opt_uid: 'u', qc_type: 1,
      lines: [{ line_no: 1, sku: 'SKU-A', wid: 'W', units: 30, price: '12.5000', expect_arrive_date: '2026-11-01',
        unplanned_reason: null, lx_item_id: null, sources: [{ line_id: 101, seller_id: '11072', units: 30 }],
        sources_units: 30, unplanned_units: 0 }],
    } as unknown as PurchaseOrder;
    let d = fromPo(po);
    expect(d.header).toEqual({ supplier_id: 'S', purchaser_id: 'p', opt_uid: 'u', qc_type: 1 });
    expect(d.lines[0]).toMatchObject({ units: null, price: '12.5000', expect_arrive_date: '2026-11-01', unplanned_reason: '' });
    d = okAdd(d, cand(), '11094', 10, 'W');
    expect(lineUnits(d.lines[0]!)).toBe(40);
  });
});

describe('本地校验（闸名同后端 preflight）', () => {
  it('空稿：单头四闸 + lines 全部未过，一次列全', () => {
    const g = validate(emptyDraft());
    expect(g.map((x) => [x.gate, x.ok])).toEqual([
      ['supplier', false], ['purchaser', false], ['opt_uid', false], ['qc_type', false], ['lines', false],
    ]);
  });

  it('每行四闸带 line_no；缺仓 / 缺价 / 数量小于来源 / 无计划来源缺理由', () => {
    let d = okAdd(setHeader(emptyDraft(), { supplier_id: 'S', purchaser_id: 'p', opt_uid: 'u', qc_type: 2 }),
      cand(), '11072', 30);
    let g = validate(d);
    const byGate = (name: string) => g.find((x) => x.gate === name)!;
    expect(byGate('line_wid')).toMatchObject({ ok: false, detail: { line_no: 1 } });
    expect(byGate('line_price').ok).toBe(false);
    expect(byGate('line_units').ok).toBe(true);
    expect(byGate('line_unplanned_reason').ok).toBe(true);

    d = updateLine(setLineWid(d, 0, 'W'), 0, { price: '-1', units: 50 });
    g = validate(d);
    expect(byGate('line_wid').ok).toBe(true);
    expect(byGate('line_price').ok).toBe(false);
    expect(byGate('line_unplanned_reason')).toMatchObject({ ok: false, detail: { line_no: 1, unplanned: 20 } });

    d = updateLine(d, 0, { price: '0', units: 10, unplanned_reason: '' });
    g = validate(d);
    expect(byGate('line_price').ok).toBe(true);
    expect(byGate('line_units')).toMatchObject({ ok: false, detail: { line_no: 1, units: 10, sources: 30 } });
  });

  it('填齐 → 全过', () => {
    expect(validate(filled()).every((x) => x.ok)).toBe(true);
  });

  it('闸名转人话：带行号', () => {
    expect(gateText({ gate: 'supplier', ok: false, detail: {} })).toBe('供应商');
    expect(gateText({ gate: 'line_price', ok: false, detail: { line_no: 2 } })).toBe('第 2 行单价');
    expect(gateText({ gate: 'something_new', ok: false, detail: {} })).toBe('something_new');
  });
});

describe('toBody', () => {
  it('价格为字符串；未填的可选字段不出现；数量未手改 = 来源合计', () => {
    const r = toBody(filled());
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.body).toEqual({
      supplier_id: 'S-1', purchaser_id: '111', opt_uid: '10925905', qc_type: 0,
      lines: [{ sku: 'SKU-A', wid: 'W-SZ', units: 30, price: '12.5',
        sources: [{ line_id: 101, seller_id: '11072', units: 30 }] }],
    });
    expect('unplanned_reason' in r.body.lines[0]!).toBe(false);
    expect('expect_arrive_date' in r.body.lines[0]!).toBe(false);
  });

  it('有无计划来源才带理由；填了预计到货才带日期', () => {
    const d = updateLine(filled(), 0, { units: 40, unplanned_reason: ' 备货 ', expect_arrive_date: '2026-11-20' });
    const r = toBody(d);
    if (!r.ok) throw new Error('expected ok');
    expect(r.body.lines[0]).toMatchObject({ units: 40, unplanned_reason: '备货', expect_arrive_date: '2026-11-20' });
    const back = toBody(updateLine(d, 0, { units: null }));
    if (!back.ok) throw new Error('expected ok');
    expect('unplanned_reason' in back.body.lines[0]!).toBe(false);
  });

  it('单头缺项 / 没有明细 → 不出 body，点名缺什么（同后端 missing_required 的键）', () => {
    expect(toBody(emptyDraft())).toEqual({ ok: false,
      missing: ['supplier_id', 'purchaser_id', 'opt_uid', 'qc_type', 'lines'] });
  });
});

describe('候选分组', () => {
  it('按货号分组，组内按月份', () => {
    const g = groupCandidates([
      cand({ line_id: 3, sku: 'B', period: '2026-11' }),
      cand({ line_id: 1, sku: 'A', period: '2026-12' }),
      cand({ line_id: 2, sku: 'A', period: '2026-10' }),
    ]);
    expect(g.map((x) => [x.sku, x.records.map((r) => r.line_id)])).toEqual([['A', [2, 1]], ['B', [3]]]);
  });
});

describe('本单原有来源（候选已滤掉的记录）', () => {
  const po = {
    supplier_id: 'S', purchaser_id: 'p', opt_uid: 'u', qc_type: 0,
    lines: [{ line_no: 1, sku: 'SKU-Z', wid: 'W', units: 50, price: '1', expect_arrive_date: null, unplanned_reason: null,
      lx_item_id: null, sources: [{ line_id: 200, seller_id: '11072', units: 30 }, { line_id: 200, seller_id: '11094', units: 20 },
        { line_id: 101, seller_id: '11072', units: 0 }], sources_units: 50, unplanned_units: 0 }],
  } as unknown as PurchaseOrder;

  it('只列候选里没有的记录；可加 = 本单原有 − 本稿现有', () => {
    const own = ownOnlyRecords(po, [cand()]);
    expect(own.map((r) => r.target.line_id)).toEqual([200]);
    expect(own[0]!.saved).toEqual({ '11072': 30, '11094': 20 });
    const base = baselineOf(po);
    let d = fromPo(po);
    expect(available(own[0]!.target, '11072', d, base)).toBe(0);
    d = removeSource(d, 0, 0);
    expect(available(own[0]!.target, '11072', d, base)).toBe(30);
  });

  it('移除后加回：不超过原有量；超了拒绝并点名可加量', () => {
    const own = ownOnlyRecords(po, [cand()])[0]!;
    const base = baselineOf(po);
    const d = removeSource(fromPo(po), 0, 0);
    expect(addSource(d, own.target, '11072', 31, base)).toEqual({ ok: false, reason: 'over_remaining', available: 30 });
    const r = addSource(d, own.target, '11072', 30, base, 'W');
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.draft.lines[0]!.sources).toContainEqual({ line_id: 200, seller_id: '11072', units: 30 });
  });

  it('记录级剩余同口径：加回本单已保存、扣本稿', () => {
    const base = baselineOf(po);
    const d = fromPo(po);
    expect(recordRemaining({ line_id: 200, remaining: 0 }, d, base)).toBe(0);
    expect(recordRemaining({ line_id: 200, remaining: 0 }, removeSource(d, 0, 1), base)).toBe(20);
  });
});
