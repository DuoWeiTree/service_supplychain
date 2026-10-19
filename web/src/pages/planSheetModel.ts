import type { QtyValue } from '../components/Qty';
import type { Period } from '../api/types';
import type { GridModel, MskuRow, PurchaseRow, SkuBlock, TransitRow } from './planGridModel';
import { stockAt } from './planGridModel';

/** 计划整表的一组：一个货号。店铺块、货号级采购、货号级在途放在同一处。
 *  ★ 只是把 GridModel 按货号重新分组，不产生任何新数 —— 数的口径全在 planGridModel.ts。 */
export interface SkuCard {
  sku: string;
  stores: SkuBlock[];
  purchase: PurchaseRow | null;
  transit: TransitRow | null;
  /** ★ sku-image 计划 Task 4：三态已经在后端（Task 3）收敛成布尔，这里只是
   *  从 `model.totals`（按货号分组的 `forecast_sku`）原样取出来，不重算、
   *  不再引入第四种状态——`totals` 里没有这个货号的行时（sku_forecast_missing
   *  orphan，极端形态）按 false 兜底，与「没有真图」同一种处置。 */
  hasImage: boolean;
}

export function buildCards(model: GridModel): SkuCard[] {
  const order: string[] = [];
  const seen = new Set<string>();
  const add = (sku: string) => { if (!seen.has(sku)) { seen.add(sku); order.push(sku); } };
  model.blocks.forEach((b) => add(b.sku));
  model.purchase.forEach((p) => add(p.sku));
  model.transit.forEach((t) => add(t.sku));
  return order.map((sku) => ({
    sku,
    stores: model.blocks.filter((b) => b.sku === sku),
    purchase: model.purchase.find((p) => p.sku === sku) ?? null,
    transit: model.transit.find((t) => t.sku === sku) ?? null,
    hasImage: model.totals.find((t) => t.sku === sku)?.cells[0]?.has_image ?? false,
  }));
}

/** 断货 / 偏低判据。★ 未知与不适用都不算 —— 它们是「不知道」，不是「没货」。 */
export type StockState = 'out' | 'low' | undefined;
export function stockState(v: QtyValue, low: number): StockState {
  if (v.kind !== 'num') return undefined;
  if (v.value <= 0) return 'out';
  if (v.value < low) return 'low';
  return undefined;
}

export interface CardSummary {
  /** 每家店断货 / 偏低的月数 */
  outs: { block: SkuBlock; months: number }[];
  lows: { block: SkuBlock; months: number }[];
  /** 生效值未知（没人填、也没有预估）的格数 */
  todo: number;
  /** ★ 只加已知的数；有待填时它是「已知部分之和」，卡片上同时挂「N 格待填」 */
  demandKnown: number | null;
  purchase: number | null;
  transit: number | null;
}

function sumKnown(values: (number | null)[]): number | null {
  let s = 0; let any = false;
  for (const v of values) if (v !== null) { any = true; s += v; }
  return any ? s : null;
}

export function summarize(card: SkuCard, periods: Period[], low: number): CardSummary {
  const outs: CardSummary['outs'] = [];
  const lows: CardSummary['lows'] = [];
  let todo = 0;
  const known: number[] = [];
  for (const b of card.stores) {
    let o = 0; let l = 0;
    for (const p of periods) {
      const s = stockState(stockAt(b, p), low);
      if (s === 'out') o++; else if (s === 'low') l++;
    }
    if (o > 0) outs.push({ block: b, months: o });
    else if (l > 0) lows.push({ block: b, months: l });
    for (const r of b.mskus) for (const c of r.cells) {
      if (c.effective_units === null) todo++; else known.push(c.effective_units);
    }
  }
  return {
    outs, lows, todo,
    demandKnown: sumKnown(known),
    purchase: card.purchase ? sumKnown(card.purchase.cells.map((c) => c.planned_units)) : null,
    transit: card.transit ? sumKnown(card.transit.cells.map((c) => c.units)) : null,
  };
}

/** 风险优先：先断货、再待填、再偏低。★ 只决定先后，不进任何一个数 */
export function riskOrder(a: CardSummary, b: CardSummary): number {
  return b.outs.length - a.outs.length || b.todo - a.todo || b.lows.length - a.lows.length;
}

export function cardMatches(card: SkuCard, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (needle === '') return true;
  const hay: string[] = [card.sku];
  for (const b of card.stores) { hay.push(b.seller_name, b.market); b.mskus.forEach((m: MskuRow) => hay.push(m.seller_sku)); }
  return hay.join(' ').toLowerCase().includes(needle);
}

/** 搜索命中到哪一层（评审 P1「定位不到单个 msku」）：
 *  命中货号 / 没搜 → 整组；命中店铺名或市场 → 那家店整块；只命中 msku → 只留命中的 msku 行。
 *  ★ 只决定显示哪几行，不改任何一个数（合计行照旧是整店之和） */
export interface Narrowed { block: SkuBlock; mskus: MskuRow[]; narrowed: boolean }
export function narrowCard(card: SkuCard, q: string): { stores: Narrowed[]; hits: Set<MskuRow> } {
  const needle = q.trim().toLowerCase();
  const all = { stores: card.stores.map((b) => ({ block: b, mskus: b.mskus, narrowed: false })), hits: new Set<MskuRow>() };
  if (needle === '' || card.sku.toLowerCase().includes(needle)) return all;
  const stores: Narrowed[] = [];
  const hits = new Set<MskuRow>();
  for (const b of card.stores) {
    if (`${b.seller_name} ${b.market}`.toLowerCase().includes(needle)) { stores.push({ block: b, mskus: b.mskus, narrowed: false }); continue; }
    const mine = b.mskus.filter((m) => m.seller_sku.toLowerCase().includes(needle));
    mine.forEach((m) => hits.add(m));
    if (mine.length > 0) stores.push({ block: b, mskus: mine, narrowed: mine.length < b.mskus.length });
  }
  return { stores, hits };
}

export function monthLabel(p: Period): { m: string; y: string } {
  const m = /^(\d{4})-(\d{2})$/.exec(p);
  return m ? { m: `${Number(m[2])} 月`, y: m[1]! } : { m: p, y: '' };
}
