import type { QtyValue } from '../components/Qty';
import type { Period } from '../api/types';
import type { GridModel, MskuRow, PurchaseRow, SkuBlock, TransitRow } from './planGridModel';
import { inventoryAt } from './planGridModel';

/** 计划矩阵的一张卡：一个货号。店铺块、货号级采购、货号级在途放在同一处。
 *  ★ 只是把 GridModel 按货号重新分组，不产生任何新数 —— 数的口径全在 planGridModel.ts。 */
export interface SkuCard {
  sku: string;
  stores: SkuBlock[];
  purchase: PurchaseRow | null;
  transit: TransitRow | null;
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
      const s = stockState(inventoryAt(b, p), low);
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

export function cardMatches(card: SkuCard, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (needle === '') return true;
  const hay: string[] = [card.sku];
  for (const b of card.stores) { hay.push(b.seller_name); b.mskus.forEach((m: MskuRow) => hay.push(m.seller_sku)); }
  return hay.join(' ').toLowerCase().includes(needle);
}

export function monthLabel(p: Period): { m: string; y: string } {
  const m = /^(\d{4})-(\d{2})$/.exec(p);
  return m ? { m: `${Number(m[2])} 月`, y: m[1]! } : { m: p, y: '' };
}
