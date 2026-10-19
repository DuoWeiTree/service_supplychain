import { describe, expect, it } from 'vitest';
import type { PurchasePlan, PurchasePlanRecord } from '../../api/types';
import { cellKind, filterPlans, homeHref, pickState, pivotPlan, planPick, togglePlan } from './purchaseHomeModel';

const rec = (over: Partial<PurchasePlanRecord>): PurchasePlanRecord => ({
  line_id: 1, rev: 1, sku: 'SKU-A', sku_name: null, period: '2026-10', state: '已提交',
  total: 100, confirmed: 0, ordered: 0, remaining: 100, orderable: true, ...over,
});
const plan = (over: Partial<PurchasePlan>): PurchasePlan => ({
  plan_id: 1, title: '10 月计划', rev: 5, lines: 0, total: 0, confirmed: 0, ordered: 0,
  orderable_units: 0, orderable_lines: 0, drafts: [], records: [], ...over,
});

describe('pivotPlan', () => {
  it('货号 × 月；只列有记录的月；可组多的货号在前', () => {
    const p = pivotPlan([
      rec({ line_id: 1, sku: 'B', period: '2026-12', remaining: 10 }),
      rec({ line_id: 2, sku: 'A', period: '2026-10', remaining: 5, sku_name: '猫爬架' }),
      rec({ line_id: 3, sku: 'B', period: '2026-10', remaining: 0, confirmed: 100, orderable: false }),
    ]);
    expect(p.months).toEqual(['2026-10', '2026-12']);
    expect(p.rows.map((r) => r.sku)).toEqual(['B', 'A']);
    expect(p.rows[1]!.sku_name).toBe('猫爬架');
    expect(p.rows[0]!.orderable_sum).toBe(10);
  });

  it('★ 同格多条记录相加，可勾的只是其中可组的那几条', () => {
    const [row] = pivotPlan([
      rec({ line_id: 1, remaining: 30, total: 50 }),
      rec({ line_id: 2, remaining: 0, total: 40, confirmed: 40, orderable: false }),
    ]).rows;
    const c = row!.cells.get('2026-10')!;
    expect(c.ids).toEqual([1]);
    expect(c.remaining).toBe(30);
    expect(c.total).toBe(90);
    expect(c.records).toBe(2);
  });
});

describe('cellKind', () => {
  it('★ 可组 / 组满 / 状态不可组 三态分开，组满不写成 0', () => {
    const one = (r: PurchasePlanRecord) => pivotPlan([r]).rows[0]!.cells.get('2026-10')!;
    expect(cellKind(one(rec({})))).toEqual({ kind: 'orderable' });
    expect(cellKind(one(rec({ remaining: 0, confirmed: 100, orderable: false })))).toEqual({ kind: 'full' });
    expect(cellKind(one(rec({ state: '准备排货', remaining: 20, orderable: false })))).toEqual({ kind: 'state', state: '准备排货' });
  });
});

describe('filterPlans', () => {
  const ps = [plan({ plan_id: 1, title: '春季', records: [rec({ sku: 'DCC1' })] }),
    plan({ plan_id: 2, title: 'DCC 专项', records: [rec({ sku: 'X' })] })];
  it('计划名或货号命中即列；只有货号命中的才自动展开', () => {
    const r = filterPlans(ps, 'dcc');
    expect(r.shown.map((p) => p.plan_id)).toEqual([1, 2]);
    expect(r.autoOpen).toEqual([1]);
  });
  it('空搜索全列、不展开', () => {
    expect(filterPlans(ps, '  ')).toEqual({ shown: ps, autoOpen: [] });
  });
});

describe('计划勾选', () => {
  const p = plan({ records: [rec({ line_id: 1 }), rec({ line_id: 2, orderable: false, remaining: 0 }), rec({ line_id: 3 })] });
  it('抬头勾 = 本计划全部可组记录；组满的不勾', () => {
    expect(togglePlan([], p)).toEqual([1, 3]);
    expect(planPick(p, [1, 3])).toBe('all');
    expect(togglePlan([1, 3, 99], p)).toEqual([99]);
  });
  it('部分勾 → some；再点抬头 = 全勾（不是全清）', () => {
    expect(planPick(p, [3])).toBe('some');
    expect(togglePlan([3], p)).toEqual([3, 1]);
    expect(planPick(plan({ records: [rec({ orderable: false })] }), [])).toBe('none');
  });
  it('格子勾：一格多条可组记录一起勾 / 一起去掉', () => {
    expect(pickState([1, 3], [1])).toBe('some');
    expect(pickState([1, 3], [3, 1])).toBe('all');
  });
});

describe('homeHref', () => {
  it('★ UX-D6：回列表定位到件数最多的来源计划；没有来源回列表首页', () => {
    expect(homeHref([{ plan_id: 4, units: 10 }, { plan_id: 7, units: 30 }, { plan_id: 4, units: 25 }])).toBe('/purchase?plan=4');
    expect(homeHref([])).toBe('/purchase');
  });
});
