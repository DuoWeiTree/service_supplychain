import { describe, expect, it } from 'vitest';
import type { SheetDetail, SheetGrid } from '../../api/types';
import grid from '../../testing/fixtures/contract/sheet-grid.json';
import detail from '../../testing/fixtures/contract/sheet-detail.json';
import gridOs from '../../testing/fixtures/contract/sheet-grid-oversea.json';
import detailOs from '../../testing/fixtures/contract/sheet-detail-oversea.json';
import {
  addOptions, availableFor, fmtLocal, blockingGates, cellsFor, filterRecords, fillFor, missingPacking, planTotals, stockFor, destLabel, destOptions, demandRows, identityHolds, parseUnits, unplannedRows,
} from './dispatchGridModel';

const g = grid as unknown as SheetGrid;
const d = detail as unknown as SheetDetail;

describe('dispatchGridModel', () => {
  it('需求行展开到 记录 × 店 × msku，并带上本表已有的格子', () => {
    const rows = demandRows(g);
    expect(rows.map((r) => [r.line?.line_id, r.store.sid, r.msku.seller_sku])).toEqual([
      [1, '11072', 'MSKU-A'], [1, '11072', 'MSKU-B']]);
    expect(rows[0]!.cells.map((c) => c.units)).toEqual([30]);
    expect(rows[1]!.cells).toEqual([]);
  });

  it('无计划来源区按 货号 × 店 × msku 展开，只带 line_id 为空的格子', () => {
    const rows = unplannedRows(gridOs as unknown as SheetGrid);     // ★ CA-D13：无计划来源的 MSKU-E 排在海外仓表
    const e = rows.find((r) => r.msku.seller_sku === 'MSKU-E')!;
    expect(e.cells.map((c) => [c.line_id, c.units, c.channel])).toEqual([[null, 3, 'oversea']]);
    expect(rows.find((r) => r.msku.seller_sku === 'MSKU-A')!.cells).toEqual([]);
  });

  it('可安排：库存读不到是 null（不是 0）；读到了但没这行是 0', () => {
    expect(availableFor(g, 'DCC1800264G1', 1, '11072')).toBe(g.stock!.find((s) => s.sku === 'DCC1800264G1')!.available['11072']);
    expect(availableFor(g, 'NOPE', 1, '11072')).toBe(0);
    expect(availableFor({ ...g, stock: null }, 'DCC1800264G1', 1, '11072')).toBeNull();
  });

  it('本行可发：只列真有这个货号的仓，按可安排从多到少；读不到 = null', () => {
    const many = { ...g, warehouses: { ...g.warehouses, local: [{ wid: 1, name: '测试仓', market: null, kind: 'local' },
      { wid: 5, name: '空仓', market: null, kind: 'local' }, { wid: 6, name: '大仓', market: null, kind: 'local' }] },
      stock: [...g.stock!, { sku: 'DCC1800264G1', wid: 6, buckets: { shared: 500 }, committed: {}, available: { '11072': 500 }, source: 'ch' as const, as_of: null }] };
    expect(stockFor(many, 'DCC1800264G1', '11072')!.map((s) => [s.name, s.available, s.own, s.shared]))
      .toEqual([['大仓', 500, 0, 500], ['测试仓', 55, 5, 80]]);
    expect(stockFor(many, 'NOPE', '11072')).toEqual([]);
    expect(stockFor({ ...g, stock: null }, 'DCC1800264G1', '11072')).toBeNull();
  });

  it('★ CA-D13 目的地只列本表去向：FBA 表只给本店；海外仓表按店铺市场过滤（市场未知的仓也列）；旧版混合表一个都不给', () => {
    const store = g.demand[0]!.stores[0]!;
    const os = [{ wid: 2, name: '30-LA自有海外仓', market: 'US', kind: 'oversea_self' },
      { wid: 9, name: '英国仓', market: 'UK', kind: 'oversea_3pl' }, { wid: 10, name: '无市场仓', market: null, kind: 'oversea_3pl' }];
    expect(destOptions(store, os, 'fba').map((o) => o.label)).toEqual(['FBA 本店']);
    expect(destOptions({ ...store, has_fba: false }, os, 'fba')).toEqual([]);
    expect(destOptions(store, os, 'oversea').map((o) => o.label)).toEqual(['30-LA自有海外仓', '无市场仓']);
    expect(destOptions(store, os, null)).toEqual([]);
  });

  it('件数：空 = 撤回（0），负数 / 小数 / 非数字拒绝', () => {
    expect(parseUnits('')).toEqual({ ok: true, units: 0 });
    expect(parseUnits(' 12 ')).toEqual({ ok: true, units: 12 });
    expect(parseUnits('-1').ok).toBe(false);
    expect(parseUnits('1.5').ok).toBe(false);
    expect(parseUnits('abc').ok).toBe(false);
  });

  it('恒等式：Σ 格子 == Σ 排货行（计划 + 无计划来源）', () => {
    expect(identityHolds(g, d)).toBe(true);
    expect(identityHolds({ ...g, cells: g.cells.slice(1) }, d)).toBe(false);
  });

  it('已取消的表不报恒等式告警（单都已取消，格子留作记录）', () => {
    const cancelled = { ...d, state: '已取消' as const,
      consignments: d.consignments.map((c) => ({ ...c, our_stage: '已取消' as const })) };
    expect(identityHolds(g, cancelled)).toBe(true);
  });

  it('格子按 记录 × msku × 店 取', () => {
    expect(cellsFor(g, 1, 'MSKU-A', '11072')).toHaveLength(1);
    expect(cellsFor(g, null, 'MSKU-A', '11072')).toHaveLength(0);
  });

  it('目的地文案', () => {
    expect(destLabel(g, { channel: 'fba', dest_sid: '11072', dest_wid: null })).toBe('FBA A4Pet-US');
    expect(destLabel(g, { channel: 'oversea', dest_sid: null, dest_wid: 2 })).toBe('海外仓 30-LA自有海外仓');
    expect(destLabel(g, { channel: 'oversea', dest_sid: null, dest_wid: 77 })).toBe('海外仓 #77');
  });

  it('计划合计：全取服务端的量（需求 / 本表 / 别表 / 已投送 / 还差 / 条数）', () => {
    const two = { ...g, demand: [g.demand[0]!, { ...g.demand[0]!, line_id: 9, total: 50, staged_here: 0, staged_other: 0, pending: 0 }] };
    expect(planTotals(two)).toEqual({ total: 150, here: 30, other: 0, sent: 0, left: 70, linesLeft: 1, linesDone: 1 });
  });

  it('记录筛选：默认还差 > 0；本地有货；全部；搜货号或品名', () => {
    const two = { ...g, demand: [g.demand[0]!, { ...g.demand[0]!, line_id: 9, sku: 'NOSTOCK', sku_name: '没货', pending: 0 }] };
    expect(filterRecords(two, 'pending', '').map((l) => l.line_id)).toEqual([1]);
    expect(filterRecords(two, 'stock', '').map((l) => l.line_id)).toEqual([1]);
    expect(filterRecords(two, 'all', '').map((l) => l.line_id)).toEqual([1, 9]);
    expect(filterRecords(two, 'all', '没货').map((l) => l.line_id)).toEqual([9]);
  });

  it('加一格的来源仓：只列可安排 > 0 的仓，多的在前；「排 N」= min(还差, 可安排)', () => {
    const many = { ...g, stock: [...g.stock!, { sku: 'DCC1800264G1', wid: 6, buckets: { shared: 3 }, committed: {}, available: { '11072': 3 }, source: 'ch' as const, as_of: null },
      { sku: 'DCC1800264G1', wid: 7, buckets: { shared: 0 }, committed: {}, available: { '11072': 0 }, source: 'ch' as const, as_of: null }] };
    expect(addOptions(many, 'DCC1800264G1', '11072').map((o) => [o.wid, o.available])).toEqual([[1, 55], [6, 3]]);
    expect(fillFor(70, 55)).toBe(55);
    expect(fillFor(0, 55)).toBeNull();
    expect(fillFor(null, 55)).toBeNull();
  });

  it('缺装箱：FBA 单里六个字段有空的行数；海外仓单恒 0', () => {
    const fba = d.consignments.find((c) => c.channel === 'fba')!;
    expect(missingPacking(fba, d.packing)).toBe(0);
    expect(missingPacking(fba, [])).toBe(1);
    expect(missingPacking((detailOs as unknown as SheetDetail).consignments.find((c) => c.channel === 'oversea')!, [])).toBe(0);
  });

  it('挡确认的闸：未过的，加上无法判定的（G5 除外 —— 它是待裁定、不拦）', () => {
    const gs = [{ gate: 'G1', ok: false }, { gate: 'G3', ok: null }, { gate: 'G5', ok: null }, { gate: 'G6', ok: true }] as never;
    expect(blockingGates(gs).map((x: { gate: string }) => x.gate)).toEqual(['G1', 'G3']);
  });

  it('来源时刻换成本地时区再显示；只有日期 / 认不出的原样给', () => {
    const utc = '2026-10-12T09:30:00+00:00';
    const d = new Date(utc);
    const p = (n: number) => String(n).padStart(2, '0');
    expect(fmtLocal(utc)).toBe(`${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`);
    expect(fmtLocal('2026-10-10')).toBe('2026-10-10');
    expect(fmtLocal(null)).toBe('—');
    expect(fmtLocal('垃圾')).toBe('垃圾');
  });
});
