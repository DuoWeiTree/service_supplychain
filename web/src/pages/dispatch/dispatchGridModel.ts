/** 排货网格的纯模型（spec 2026-10-10 §7）。★ 需求侧与物理侧并排，不合成「可排量」（`10` 原则三）。 */
import type {
  DispatchRoute,
  ConsignmentView, DemandRow, DispatchCell, DispatchChannel, DispatchGate, DispatchMsku, DispatchWarehouse, PackingRow,
  SheetDetail, SheetGrid,
} from '../../api/types';

export interface GridRow {
  key: string;
  line: DemandRow | null;
  sku: string;
  store: { sid: string; seller_name: string; market?: string; has_fba?: boolean; expected?: number | null };
  msku: DispatchMsku;
  cells: DispatchCell[];
}

export function cellsFor(g: SheetGrid, lineId: number | null, sellerSku: string, sid: string): DispatchCell[] {
  return g.cells.filter((c) => c.line_id === lineId && c.seller_sku === sellerSku && c.sid === sid);
}

export function demandRows(g: SheetGrid): GridRow[] {
  return g.demand.flatMap((line) => line.stores.flatMap((store) => store.mskus.map((msku) => ({
    key: `${line.line_id}/${store.sid}/${msku.seller_sku}`, line, sku: line.sku, store, msku,
    cells: cellsFor(g, line.line_id, msku.seller_sku, store.sid),
  }))));
}

export function unplannedRows(g: SheetGrid): GridRow[] {
  return g.unplanned.flatMap((u) => u.stores.flatMap((store) => store.mskus.map((msku) => ({
    key: `-/${u.sku}/${store.sid}/${msku.seller_sku}`, line: null, sku: u.sku, store, msku,
    cells: cellsFor(g, null, msku.seller_sku, store.sid),
  }))));
}

/** null = 本地库存读不到（不是 0）；读到了但快照里没这行 = 0 */
export function availableFor(g: SheetGrid, sku: string, wid: number, sid: string): number | null {
  if (g.stock === null) return null;
  const row = g.stock.find((s) => s.sku === sku && s.wid === wid);
  return row?.available[sid] ?? 0;
}

export interface WhStock { wid: number; name: string; available: number; own: number; shared: number }

/** 这一行（货号 × 店）在哪些本地仓真有货。★ 只列有快照行的仓 —— 38 个仓里通常只有一两个有货，
 *  全列出来就是一排 0，真数被挤到看不见的地方（10-11 负责人实测「完全没有库存信息」）。
 *  null = 库存读不到（不是「哪儿都没有」） */
export function stockFor(g: SheetGrid, sku: string, sid: string): WhStock[] | null {
  if (g.stock === null) return null;
  return g.stock.filter((s) => s.sku === sku).map((s) => ({
    wid: s.wid, name: whName(g, s.wid), available: s.available[sid] ?? 0,
    own: s.buckets[sid] ?? 0, shared: s.buckets['shared'] ?? 0,
  })).sort((a, b) => b.available - a.available || a.wid - b.wid);
}

export interface DestOption { key: string; label: string; channel: DispatchChannel; dest_wid: number | null; dest_sid: string | null }

/** CA-D5：逐格选目的地。FBA = 本店（店有 FBA 时）；海外仓按店铺市场过滤，市场未知的仓照列（10-10 实测 8/21 为空） */
/** ★ CA-D13：只列本表去向的目的地（route 为空 = 旧版混合表，不给加格子 → 空） */
export const ROUTE_LABEL: Record<DispatchRoute, string> = { fba: '平台仓 · 亚马逊 FBA', oversea: '海外仓' };
export const ROUTE_SHORT: Record<DispatchRoute, string> = { fba: '平台仓 · FBA', oversea: '海外仓' };

export function destOptions(store: { sid: string; has_fba?: boolean | undefined; market?: string | undefined },
  oversea: DispatchWarehouse[], route: DispatchRoute | null): DestOption[] {
  if (route === 'fba') {
    return store.has_fba === false ? []
      : [{ key: `fba/${store.sid}`, label: 'FBA 本店', channel: 'fba', dest_wid: null, dest_sid: store.sid }];
  }
  if (route === 'oversea') {
    return oversea
      .filter((w) => w.market === null || store.market === undefined || w.market === store.market)
      .map((w) => ({ key: `oversea/${w.wid}`, label: w.name, channel: 'oversea' as const, dest_wid: w.wid, dest_sid: null }));
  }
  return [];
}

export const destKey = (c: { channel: DispatchChannel; dest_wid: number | null; dest_sid: string | null }) =>
  c.channel === 'fba' ? `fba/${c.dest_sid}` : `oversea/${c.dest_wid}`;

export function destLabel(g: SheetGrid, c: { channel: DispatchChannel; dest_wid: number | null; dest_sid: string | null }): string {
  if (c.channel === 'fba') {
    const name = g.demand.flatMap((l) => l.stores).find((s) => s.sid === c.dest_sid)?.seller_name
      ?? g.unplanned.flatMap((u) => u.stores).find((s) => s.sid === c.dest_sid)?.seller_name;
    return `FBA ${name ?? c.dest_sid}`;
  }
  const w = g.warehouses.oversea.find((x) => x.wid === c.dest_wid);
  return `海外仓 ${w ? w.name : `#${c.dest_wid}`}`;
}

export function whName(g: SheetGrid, wid: number): string {
  return [...g.warehouses.local, ...g.warehouses.oversea].find((w) => w.wid === wid)?.name ?? `#${wid}`;
}

export function parseUnits(text: string): { ok: true; units: number } | { ok: false; reason: string } {
  const t = text.trim();
  if (t === '') return { ok: true, units: 0 };
  if (!/^\d+$/.test(t)) return { ok: false, reason: '件数要填不小于 0 的整数（空 = 撤回）' };
  return { ok: true, units: Number(t) };
}

/** 承重墙② 的恒等式在屏上也对一次：Σ 格子 == Σ 派生排货行（已放弃 / 已取消的单不算） */
export function identityHolds(g: SheetGrid, d: SheetDetail): boolean {
  if (d.state === '已取消') return true;   // 单都已取消，格子只是留档 —— 不在终态上报警（I4）
  const cells = g.cells.reduce((a, c) => a + c.units, 0);
  const lines = d.consignments.filter((c) => c.our_stage !== '已放弃' && c.our_stage !== '已取消')
    .flatMap((c) => c.lines).reduce((a, l) => a + l.units, 0);
  return cells === lines;
}

// ── 排货工作台（设计 artifact「DispatchSheet」）：全部只是把服务端的量摆出来，不重算判据 ──

export interface PlanTotals { total: number; here: number; other: number; sent: number; left: number; linesLeft: number; linesDone: number }

/** 页头「这张计划还差多少」：逐条记录的服务端量求和；★ 已撤销 / 已完结的记录不计入 */
export function planTotals(g: SheetGrid): PlanTotals {
  const live = g.demand.filter((l) => l.state !== '已撤销' && l.state !== '已完结');
  const sum = (f: (l: DemandRow) => number) => live.reduce((a, l) => a + f(l), 0);
  return {
    total: sum((l) => l.total), here: sum((l) => l.staged_here), other: sum((l) => l.staged_other),
    sent: sum((l) => l.dispatched), left: sum((l) => Math.max(0, l.pending)),
    linesLeft: live.filter((l) => l.pending > 0).length, linesDone: live.filter((l) => l.pending === 0).length,
  };
}

export type RecordFilter = 'pending' | 'stock' | 'all';

export function filterRecords(g: SheetGrid, f: RecordFilter, q: string): DemandRow[] {
  const needle = q.trim().toLowerCase();
  return g.demand.filter((l) => {
    if (f === 'pending' && !(l.pending > 0)) return false;
    if (f === 'stock' && !(g.stock ?? []).some((s) => s.sku === l.sku)) return false;
    return needle === '' || `${l.sku} ${l.sku_name}`.toLowerCase().includes(needle);
  });
}

/** 「加一格」的来源仓：只列这家店可安排 > 0 的仓，多的在前 */
export function addOptions(g: SheetGrid, sku: string, sid: string): WhStock[] {
  return (stockFor(g, sku, sid) ?? []).filter((w) => w.available > 0);
}

/** 「排 N」快捷数 = min(还差, 这个仓可安排)；没有可填的给 null */
export function fillFor(pending: number | null, available: number): number | null {
  if (pending === null || pending <= 0 || available <= 0) return null;
  return Math.min(pending, available);
}

const BOX_KEYS = ['box_count', 'box_len', 'box_wid', 'box_hei', 'box_weight', 'units_per_box'] as const;

/** FBA 单里装箱没填齐的行数（展示用；确认时以 G1 为准）。海外仓单恒 0 —— 那里没有装箱这一栏 */
export function missingPacking(c: ConsignmentView, packing: PackingRow[]): number {
  if (c.channel !== 'fba') return 0;
  return c.lines.filter((ln) => {
    const p = packing.find((x) => x.seller_sku === ln.seller_sku && x.sid === ln.sid && x.from_wid === ln.from_wid);
    return !p || BOX_KEYS.some((k) => p[k] === null);
  }).length;
}

/** 挡确认的闸：未过的 + 无法判定的；G5 待裁定、恒「？」，不拦（CA-Q9） */
export function blockingGates(gs: DispatchGate[]): DispatchGate[] {
  return gs.filter((x) => x.ok === false || (x.ok === null && x.gate !== 'G5'));
}


/** 来源时刻按浏览器本地时区显示（CH 的是 UTC、PG 的带库的时区 —— 不换算的话同一页上两个时刻没法比）。
 *  只有日期的原样给；认不出的原样给，不编。 */
export function fmtLocal(iso: string | null | undefined): string {
  if (!iso) return '—';
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
