import { describe, expect, it } from 'vitest';
import type { DispatchPlan, DispatchPlanRecord, DispatchPlanSheet } from '../../api/types';
import { TONE_CLASS, cellTitle, filterPlans, pivotRecords, sheetNextStep, skuHit, type ChipTone } from './dispatchHomeModel';

const S: DispatchPlanSheet = {
  sheet_id: 11, state: '草稿', route: 'fba', in_flight: 0, attention: 0, drift: [], to_lock: 0, to_submit: 0,
  packing_missing: 0, logistics_missing: 0, consignments: 1, all_submitted: false,
};
const step = (o: Partial<DispatchPlanSheet>) => sheetNextStep({ ...S, ...o });

describe('sheetNextStep（spec §4 · DL-D7 / D8）', () => {
  const CASES: [string, Partial<DispatchPlanSheet>, string, ChipTone, number][] = [
    ['0 在途写调用排最前', { state: '已确认', in_flight: 1, to_submit: 1 }, '正在提交…', 'neutral', 1],
    ['1 旧版混合表', { route: null }, '旧版混合表：只能取消', 'neutral', 0],
    ['2 待核实', { state: '已确认', attention: 1, to_submit: 2 }, '提交结果未知，先核实再继续', 'warn', 1],
    ['3 两轴不一致', { state: '已确认', route: 'oversea', drift: ['领星已撤销'], all_submitted: true }, '⚠ 领星已撤销', 'danger', 1],
    ['4 FBA 已建未锁', { state: '已确认', to_lock: 1 }, '补锁库存（1 张）', 'warn', 0],
    ['5 还有没领星单号的', { state: '已确认', to_submit: 2 }, '提交领星（2 张）', 'primary', 0],
    ['6 草稿 FBA 缺装箱', { packing_missing: 3 }, '缺装箱 3', 'warn', 0],
    ['7 草稿 海外仓没选物流方式', { route: 'oversea', logistics_missing: 1 }, '还没选物流方式', 'warn', 0],
    ['8 草稿 没有排货单', { consignments: 0 }, '还没有排货单', 'neutral', 0],
    ['9 草稿 以上都没有', {}, '草稿', 'primary', 0],
    ['10 海外仓全部已提交', { state: '已确认', route: 'oversea', all_submitted: true }, '海外仓备货单已全部提交领星', 'neutral', 0],
    ['11 FBA 全部已提交', { state: '已确认', all_submitted: true }, '已提交领星 FBA 发货计划', 'neutral', 0],
  ];
  it.each(CASES)('%s', (_name, o, text, tone, more) => {
    expect(step(o)).toEqual({ text, tone, more, unmatched: false });
  });

  it('★ 多条命中：取优先级最高的，其余计成 +N；在途永远最先（Review Focus）', () => {
    expect(step({ state: '已确认', in_flight: 1, attention: 1, drift: ['领星已撤销'], to_submit: 2 }))
      .toEqual({ text: '正在提交…', tone: 'neutral', more: 3, unmatched: false });
  });

  it('★ 一条都不命中：写表状态原文、标 unmatched，不写「草稿」也不空着（Review Focus · DL-Q6）', () => {
    expect(step({ state: '已确认' })).toEqual({ text: '已确认', tone: 'neutral', more: 0, unmatched: true });
  });

  it('「已确认」不再用绿色：四种色都映到既有 chip 类，没有 chip--ok', () => {
    expect(TONE_CLASS).toEqual({ neutral: 'chip', warn: 'chip chip--warn', danger: 'chip chip--bad', primary: 'chip chip--ours' });
  });
});

const R = (o: Partial<DispatchPlanRecord>): DispatchPlanRecord => ({
  line_id: 1, sku: 'A', sku_name: '甲', period: '2026-10-01', state: '已确认',
  total: 200, confirmed: 200, ordered: 150, staged: 5, dispatched: 0, pending: 195, ...o,
});

describe('pivotRecords（DL-D2 · DL-D6 · DL-D10）', () => {
  it('一个货号一行；月份只列有记录的月、按时间排；没有记录的格子不存在（≠ 0）', () => {
    const p = pivotRecords([
      R({ line_id: 1, period: '2027-02-01' }), R({ line_id: 2, period: '2026-11-01' }),
      R({ line_id: 3, sku: 'B', period: '2026-12-01', total: 50, pending: 0 }),
    ]);
    expect(p.months).toEqual(['2026-11', '2026-12', '2027-02']);
    expect(p.rows.map((r) => r.sku)).toEqual(['A', 'B']);
    expect(p.rows[0]!.cells.get('2026-12')).toBeUndefined();
    expect(p.rows[1]!.cells.get('2026-12')!.pending).toBe(0);
  });

  it('货号行按还差合计降序，同额按货号', () => {
    const p = pivotRecords([R({ sku: 'C', pending: 10 }), R({ sku: 'B', pending: 97 }), R({ sku: 'A', pending: 10 })]);
    expect(p.rows.map((r) => [r.sku, r.pending_sum])).toEqual([['B', 97], ['A', 10], ['C', 10]]);
  });

  it('★ 同一货号同一月两条活记录：两条都算进格子，不静默丢（Review Focus · DL-Q5）', () => {
    const p = pivotRecords([R({ line_id: 1, total: 100, pending: 100 }), R({ line_id: 2, total: 40, pending: 30 })]);
    const c = p.rows[0]!.cells.get('2026-10')!;
    expect([c.pending, c.total, c.records]).toEqual([130, 140, 2]);
    expect(p.rows[0]!.pending_sum).toBe(130);
    expect(cellTitle('A', '2026-10', c)).toMatch(/（2 条记录）$/);
  });

  it('品名取第一个非空的', () => {
    expect(pivotRecords([R({ sku_name: null }), R({ line_id: 2, period: '2026-11-01' })]).rows[0]!.sku_name).toBe('甲');
  });

  it('悬停 / 读屏给全量：需求 · 已组单 · 已下单 · 已排未发 · 已投送', () => {
    const c = pivotRecords([R({})]).rows[0]!.cells.get('2026-10')!;
    expect(cellTitle('A', '2026-10', c)).toBe('A 2026-10：还差 195 · 需求 200 · 已组单 200 · 已下单 150 · 已排未发 5 · 已投送 0');
  });
});

const P = (o: Partial<DispatchPlan>): DispatchPlan => ({
  plan_id: 1, title: '测试1', rev: 1, lines: 1, total: 0, confirmed: 0, ordered: 0, staged: 0, dispatched: 0, pending: 0,
  sheets: [], records: [], ...o,
});

describe('filterPlans / skuHit（DL-D10：计划名 / 货号，纯前端）', () => {
  const plans = [P({ plan_id: 1, title: '测试1', records: [R({ sku: 'DCC1045014AI' })] }),
    P({ plan_id: 2, title: '测试销售计划2', records: [R({ sku: 'DCC9' })] })];

  it('空查询：全列、不自动展开', () => {
    expect(filterPlans(plans, '')).toEqual({ shown: plans, autoOpen: [] });
    expect(filterPlans(plans, '   ')).toEqual({ shown: plans, autoOpen: [] });
  });

  it('按计划名命中：列出但不自动展开', () => {
    expect(filterPlans(plans, '销售计划2')).toEqual({ shown: [plans[1]], autoOpen: [] });
  });

  it('★ 按货号命中：自动展开；大小写、前后空格不影响（Review Focus）', () => {
    expect(filterPlans(plans, '  dcc1045 ')).toEqual({ shown: [plans[0]], autoOpen: [1] });
    expect(skuHit('DCC1045014AI', '  dcc1045 ')).toBe(true);
    expect(skuHit('DCC1045014AI', '   ')).toBe(false);
  });

  it('都没命中：空', () => {
    expect(filterPlans(plans, 'zzz')).toEqual({ shown: [], autoOpen: [] });
  });
});
