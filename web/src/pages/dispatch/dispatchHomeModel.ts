/** /dispatch 的纯模型（spec 2026-10-15）：排货表下一步 · 货号 × 月透视 · 搜索。 */
import type { DispatchPlan, DispatchPlanRecord, DispatchPlanSheet } from '../../api/types';

export type ChipTone = 'neutral' | 'warn' | 'danger' | 'primary';
export interface NextStep { text: string; tone: ChipTone; more: number; unmatched: boolean }

/** 设计系统 sc-chip 的四种 ↔ web 既有类（dispatch.css 头注）；色只给要人动手的 */
export const TONE_CLASS: Record<ChipTone, string> = {
  neutral: 'chip', warn: 'chip chip--warn', danger: 'chip chip--bad', primary: 'chip chip--ours',
};

/** spec §4：文案逐字复用详情页；按表中顺序取第一条，其余计「+N」 */
export function sheetNextStep(s: DispatchPlanSheet): NextStep {
  const hits: { text: string; tone: ChipTone }[] = [];
  if (s.in_flight > 0) hits.push({ text: '正在提交…', tone: 'neutral' });
  if (s.route === null) hits.push({ text: '旧版混合表：只能取消', tone: 'neutral' });
  if (s.attention > 0) hits.push({ text: '提交结果未知，先核实再继续', tone: 'warn' });
  const drift = s.drift[0];
  if (drift !== undefined) hits.push({ text: `⚠ ${drift}`, tone: 'danger' });
  
  // DL-D12：补锁排在提交之前（详情页按钮两者并存时的顺序以此为准）
  if (s.route === 'fba' && s.to_lock > 0) hits.push({ text: `补锁库存（${s.to_lock} 张）`, tone: 'warn' });
  if (s.state === '已确认' && s.to_submit > 0) hits.push({ text: `提交领星（${s.to_submit} 张）`, tone: 'primary' });
  if (s.state === '草稿') {
    if (s.packing_missing > 0) hits.push({ text: `缺装箱 ${s.packing_missing}`, tone: 'warn' });
    if (s.logistics_missing > 0) hits.push({ text: '还没选物流方式', tone: 'warn' });
    if (s.consignments === 0) hits.push({ text: '还没有排货单', tone: 'neutral' });
    if (hits.length === 0) hits.push({ text: '草稿', tone: 'primary' });
  }
  if (s.state === '已确认' && s.all_submitted) {
    hits.push({ text: s.route === 'oversea' ? '海外仓备货单已全部提交领星' : '已提交领星 FBA 发货计划', tone: 'neutral' });
  }
  const first = hits[0];
  // ★ DL-Q6：不命中时写表状态原文；调用方负责在控制台留痕
  if (first === undefined) return { text: s.state, tone: 'neutral', more: 0, unmatched: true };
  return { ...first, more: hits.length - 1, unmatched: false };
}

export interface PivotCell { pending: number; total: number; confirmed: number; ordered: number; staged: number; dispatched: number; records: number }
export interface PivotRow { sku: string; sku_name: string | null; cells: Map<string, PivotCell>; pending_sum: number }
export interface Pivot { months: string[]; rows: PivotRow[] }

const EMPTY: PivotCell = { pending: 0, total: 0, confirmed: 0, ordered: 0, staged: 0, dispatched: 0, records: 0 };

/** DL-D2 货号 × 月；DL-D10 只列有记录的月。★ 同格多条记录相加并计条数（DL-Q5），不覆盖 */
export function pivotRecords(records: DispatchPlanRecord[]): Pivot {
  const months = [...new Set(records.map((r) => r.period.slice(0, 7)))].sort();
  const bySku = new Map<string, PivotRow>();
  for (const r of records) {
    const row = bySku.get(r.sku) ?? { sku: r.sku, sku_name: null, cells: new Map<string, PivotCell>(), pending_sum: 0 };
    bySku.set(r.sku, row);
    if (row.sku_name === null && r.sku_name) row.sku_name = r.sku_name;
    const m = r.period.slice(0, 7);
    const c = row.cells.get(m) ?? EMPTY;
    row.cells.set(m, {
      pending: c.pending + r.pending, total: c.total + r.total, confirmed: c.confirmed + r.confirmed,
      ordered: c.ordered + r.ordered, staged: c.staged + r.staged, dispatched: c.dispatched + r.dispatched,
      records: c.records + 1,
    });
    row.pending_sum += r.pending;
  }
  const rows = [...bySku.values()].sort((a, b) => b.pending_sum - a.pending_sum || a.sku.localeCompare(b.sku));
  return { months, rows };
}

export function cellTitle(sku: string, month: string, c: PivotCell): string {
  return `${sku} ${month}：还差 ${c.pending} · 需求 ${c.total} · 已组单 ${c.confirmed} · 已下单 ${c.ordered}`
    + ` · 已排未发 ${c.staged} · 已投送 ${c.dispatched}${c.records > 1 ? `（${c.records} 条记录）` : ''}`;
}

const norm = (q: string) => q.trim().toLowerCase();

export function skuHit(sku: string, q: string): boolean {
  const k = norm(q);
  return k !== '' && sku.toLowerCase().includes(k);
}

/** 计划名或货号命中即列出；★ 只有货号命中的才自动展开 */
export function filterPlans(plans: DispatchPlan[], q: string): { shown: DispatchPlan[]; autoOpen: number[] } {
  const k = norm(q);
  if (k === '') return { shown: plans, autoOpen: [] };
  const shown: DispatchPlan[] = [];
  const autoOpen: number[] = [];
  for (const p of plans) {
    const bySku = p.records.some((r) => r.sku.toLowerCase().includes(k));
    if (bySku) autoOpen.push(p.plan_id);
    if (bySku || p.title.toLowerCase().includes(k)) shown.push(p);
  }
  return { shown, autoOpen };
}
