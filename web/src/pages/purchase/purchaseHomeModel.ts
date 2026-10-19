/** /purchase 的纯模型（spec 2026-10-16 §1）：计划内货号 × 月透视 · 搜索 · 按计划勾选。 */
import type { PurchasePlan, PurchasePlanRecord } from '../../api/types';

export interface PickCell {
  /** 这一格里可组的记录：勾这一格 = 勾它们 */
  ids: number[];
  remaining: number; total: number; confirmed: number; ordered: number; records: number;
  /** 不可组记录的状态原文（组满的不算在内） */
  blockedStates: string[];
}
export interface PickRow { sku: string; sku_name: string | null; cells: Map<string, PickCell>; orderable_sum: number }
export interface PickPivot { months: string[]; rows: PickRow[] }

/** 同 dispatch DL-D2 / DL-Q5：只列有记录的月；同格多条记录相加并计条数 */
export function pivotPlan(records: PurchasePlanRecord[]): PickPivot {
  const months = [...new Set(records.map((r) => r.period.slice(0, 7)))].sort();
  const bySku = new Map<string, PickRow>();
  for (const r of records) {
    const row = bySku.get(r.sku) ?? { sku: r.sku, sku_name: null, cells: new Map<string, PickCell>(), orderable_sum: 0 };
    bySku.set(r.sku, row);
    if (row.sku_name === null && r.sku_name) row.sku_name = r.sku_name;
    const m = r.period.slice(0, 7);
    const c = row.cells.get(m) ?? { ids: [], remaining: 0, total: 0, confirmed: 0, ordered: 0, records: 0, blockedStates: [] };
    row.cells.set(m, {
      ids: r.orderable ? [...c.ids, r.line_id] : c.ids,
      remaining: c.remaining + (r.orderable ? r.remaining : 0),
      total: c.total + r.total, confirmed: c.confirmed + r.confirmed, ordered: c.ordered + r.ordered,
      records: c.records + 1,
      blockedStates: !r.orderable && r.remaining > 0 ? [...c.blockedStates, r.state] : c.blockedStates,
    });
    if (r.orderable) row.orderable_sum += r.remaining;
  }
  const rows = [...bySku.values()].sort((a, b) => b.orderable_sum - a.orderable_sum || a.sku.localeCompare(b.sku));
  return { months, rows };
}

export type CellKind = { kind: 'orderable' } | { kind: 'full' } | { kind: 'state'; state: string };
/** ★ 组满 ≠ 0 ≠ 状态不可组：三种处置不同，不能都写成一个数 */
export function cellKind(c: PickCell): CellKind {
  if (c.ids.length > 0) return { kind: 'orderable' };
  const st = c.blockedStates[0];
  return st === undefined ? { kind: 'full' } : { kind: 'state', state: st };
}

export function cellTitle(sku: string, month: string, c: PickCell): string {
  return `${sku} ${month}：还可组 ${c.remaining}，需求 ${c.total}，已组单（含草稿）${c.confirmed}，已下单 ${c.ordered}`
    + `${c.records > 1 ? `（${c.records} 条记录）` : ''}`;
}

const norm = (q: string) => q.trim().toLowerCase();
export const skuHit = (sku: string, q: string): boolean => norm(q) !== '' && sku.toLowerCase().includes(norm(q));

/** 计划名或货号命中即列出；★ 只有货号命中的才自动展开（同 /dispatch） */
export function filterPlans(plans: PurchasePlan[], q: string): { shown: PurchasePlan[]; autoOpen: number[] } {
  const k = norm(q);
  if (k === '') return { shown: plans, autoOpen: [] };
  const shown: PurchasePlan[] = [];
  const autoOpen: number[] = [];
  for (const p of plans) {
    const bySku = p.records.some((r) => skuHit(r.sku, k));
    if (bySku) autoOpen.push(p.plan_id);
    if (bySku || p.title.toLowerCase().includes(k)) shown.push(p);
  }
  return { shown, autoOpen };
}

export type Pick = 'none' | 'some' | 'all';
export function pickState(ids: readonly number[], picked: readonly number[]): Pick {
  const n = ids.filter((id) => picked.includes(id)).length;
  return n === 0 ? 'none' : n === ids.length ? 'all' : 'some';
}

const planIds = (p: PurchasePlan) => p.records.filter((r) => r.orderable).map((r) => r.line_id);
export const planPick = (p: PurchasePlan, picked: readonly number[]): Pick => {
  const ids = planIds(p);
  return ids.length === 0 ? 'none' : pickState(ids, picked);
};

/** 一组记录的勾：全勾了 → 去掉；否则补齐（部分勾时点一下是「全勾」，不是「全清」） */
export function toggleIds(picked: readonly number[], ids: readonly number[]): number[] {
  if (pickState(ids, picked) === 'all') return picked.filter((id) => !ids.includes(id));
  return [...picked, ...ids.filter((id) => !picked.includes(id))];
}
export const togglePlan = (picked: readonly number[], p: PurchasePlan): number[] => toggleIds(picked, planIds(p));

/** UX-D6：回列表并定位到本单件数最多的来源计划（同件数取先出现的）；没有来源 → 列表首页 */
export function homeHref(sources: readonly { plan_id: number; units: number }[]): string {
  const sum = new Map<number, number>();
  for (const s of sources) sum.set(s.plan_id, (sum.get(s.plan_id) ?? 0) + s.units);
  let best: number | null = null;
  for (const [pid, n] of sum) if (best === null || n > sum.get(best)!) best = pid;
  return best === null ? '/purchase' : `/purchase?plan=${best}`;
}
