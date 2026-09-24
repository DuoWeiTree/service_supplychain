import type { QtyValue } from '../components/Qty';
import type {
  BeyondHorizon, DemandBasis, DemandCell, ForecastParams, ForecastState, GridResponse, MskuForecast, Period,
  Seller, Sid, SkuForecast, StoreForecast, Unallocated,
} from '../api/types';

export interface MskuCell {
  period: Period;
  system_units: number | null;
  /** ★ 认领时记下的「没有销量取数源」—— 预测不适用，不是未知 */
  system_na: boolean;
  system_extrapolated: boolean;
  expected_units: number | null;
  effective_units: number | null;
  basis: DemandBasis;
  /** 认领时记下的预测依据（点开看依据用）。★ null = 旧认领没种（OP-8），不是 0 */
  system_basis: DemandCell['system_basis'];
}
export interface MskuRow { seller_sku: string; sid: Sid; cells: MskuCell[]; forecast: MskuForecast[] }
/** ★ 店铺库存挂在**块**上（店铺 × 货号），= Σ 该块 msku 行（spec §4.6），不是另算的一份 */
export interface SkuBlock {
  sid: Sid; seller_name: string; market: string; has_fba: boolean; sku: string;
  mskus: MskuRow[]; stock: StoreForecast[];
}
export interface PurchaseRow { sku: string; cells: { period: Period; planned_units: number | null }[] }
/** 货号级在途，读 `forecast_sku[].in_transit`。★ null = 该月没有货号行（未知），不是 0 */
export interface TransitCell { period: Period; units: number | null }
export interface TransitRow { sku: string; cells: TransitCell[] }
export interface SkuTotals { sku: string; cells: SkuForecast[] }
export interface Orphan {
  kind: 'unknown_seller' | 'inventory_without_demand' | 'demand_without_inventory'
      | 'na_disagrees_with_seller' | 'in_transit_disagrees' | 'demand_disagrees' | 'chain_broken'
      | 'forecast_sum_disagrees' | 'sku_forecast_missing';
  key: string;
}
export interface GridModel {
  periods: Period[]; blocks: SkuBlock[]; purchase: PurchaseRow[]; transit: TransitRow[];
  totals: SkuTotals[]; params: ForecastParams; beyond: BeyondHorizon[]; unallocated: Unallocated[];
  /** `${seller_sku}/${sid}` → 整本台账第一个断货日（forecast_msku）；null = 不断货 */
  firstStockout: Map<string, string | null>;
  orphans: Orphan[];
}

/** ★ 任何一项未知 ⇒ 和未知。空数组也是未知：没有数不等于 0 */
export function sumUnits(values: (number | null)[]): QtyValue {
  if (values.length === 0 || values.some((v) => v === null)) return { kind: 'unknown' };
  return { kind: 'num', value: values.reduce<number>((a, b) => a + (b as number), 0) };
}

export function demandAt(block: SkuBlock, period: Period): QtyValue {
  return sumUnits(block.mskus.map((r) => r.cells.find((c) => c.period === period)?.effective_units ?? null));
}

/** ★ 不适用 ≠ 未知 ≠ 0（02 §3.1a · 14 B5） */
export function qtyOf(state: ForecastState, v: number | null): QtyValue {
  if (state === 'not_applicable') return { kind: 'na' };
  return v === null ? { kind: 'unknown' } : { kind: 'num', value: v };
}

export function stockAt(block: SkuBlock, period: Period): QtyValue {
  const r = block.stock.find((s) => s.period === period);
  return r ? qtyOf(r.state, r.closing) : { kind: 'unknown' };
}

export function mskuStockAt(row: MskuRow, period: Period): QtyValue {
  const r = row.forecast.find((f) => f.period === period);
  return r ? qtyOf(r.state, r.closing) : { kind: 'unknown' };
}

export function systemAt(block: SkuBlock, period: Period): QtyValue {
  const r = block.stock.find((s) => s.period === period);
  if (r?.system_not_applicable) return { kind: 'na' };
  return r?.system == null ? { kind: 'unknown' } : { kind: 'num', value: r.system };
}

export function totalsAt(model: GridModel, sku: string, period: Period): SkuForecast | undefined {
  return model.totals.find((t) => t.sku === sku)?.cells.find((c) => c.period === period);
}

/** ★ 合计列的存量给**期末**，不是三个月相加 —— 期初是一条链，加起来等于把同一批货数三遍 */
export function closingOfLast(block: SkuBlock, periods: Period[]): QtyValue {
  const last = periods[periods.length - 1];
  return last === undefined ? { kind: 'unknown' } : stockAt(block, last);
}

/** ★ 断货计数按**折叠行的期末**数。未知与不适用都不算断货 —— 它们是「不知道」，不是「没货」 */
export function outageCount(block: SkuBlock): number {
  return block.stock.filter((s) =>
    s.state !== 'not_applicable' && s.closing !== null && s.closing <= 0).length;
}

/** ★ F1/F2 裁定：两种人工输入共用同一套解析——空串 = 未知 ⇒ null；
 *  否则必须是非负整数，拒绝 NaN / 小数 / 负数，且带上能直接展示的理由。 */
export type ParsedUnits = { ok: true; value: number | null } | { ok: false; why: string };
export function parseUnits(raw: string): ParsedUnits {
  const trimmed = raw.trim();
  if (trimmed === '') return { ok: true, value: null };
  const n = Number(trimmed);
  if (Number.isNaN(n) || !Number.isInteger(n) || n < 0) {
    return { ok: false, why: `${raw} 不是非负整数` };
  }
  return { ok: true, value: n };
}

export function buildGridModel(grid: GridResponse, sellers: Seller[]): GridModel {
  const sellerById = new Map(sellers.map((s) => [s.seller_id, s]));
  const orphans: Orphan[] = [];
  const blocks = new Map<string, SkuBlock>();
  const usedStock = new Set<string>();
  const stockKey = (sku: string, sid: Sid, p: Period) => `${sku}/${sid}/${p}`;
  const stockByKey = new Map(grid.forecast_store.map((s) => [stockKey(s.sku, s.sid, s.period), s]));
  // ★ 几百个货号一张表：按键建索引，不在循环里 filter / find
  const mskuForecast = new Map<string, MskuForecast[]>();
  for (const f of grid.forecast) {
    const k = `${f.seller_sku}/${f.sid}`;
    const list = mskuForecast.get(k);
    if (list) list.push(f); else mskuForecast.set(k, [f]);
  }

  for (const d of grid.demand) {
    const seller = sellerById.get(d.sid);
    if (!seller) {
      // ★ 认不出的店铺硬性点名，不许落进 else 再被下游过滤掉
      orphans.push({ kind: 'unknown_seller', key: `${d.sid}/${d.seller_sku}` });
      continue;
    }
    const bKey = `${d.sid}/${d.sku}`;
    let block = blocks.get(bKey);
    if (!block) {
      block = { sid: d.sid, seller_name: seller.name, market: seller.market, has_fba: seller.has_fba, sku: d.sku, mskus: [], stock: [] };
      blocks.set(bKey, block);
    }
    let row = block.mskus.find((r) => r.seller_sku === d.seller_sku);
    if (!row) {
      row = { seller_sku: d.seller_sku, sid: d.sid, cells: [],
              forecast: mskuForecast.get(`${d.seller_sku}/${d.sid}`) ?? [] };
      block.mskus.push(row);
    }
    row.cells.push({
      period: d.period, system_units: d.system_units,
      system_na: d.system_basis?.reason === 'not_applicable_no_sales_source',
      system_extrapolated: d.system_extrapolated,
      expected_units: d.expected_units, effective_units: d.effective_units, basis: d.basis,
      system_basis: d.system_basis,
    });
  }

  for (const block of blocks.values()) {
    // 构造器没给 msku 行时互证无从谈起 —— 跳过，而不是把「没给」当成「对不上」
    const hasMskuForecast = block.mskus.some((r) => r.forecast.length > 0);
    for (const period of grid.periods) {
      const k = stockKey(block.sku, block.sid, period);
      const row = stockByKey.get(k);
      if (!row) { orphans.push({ kind: 'demand_without_inventory', key: k }); continue; }
      usedStock.add(k);
      // ★ 「不适用」必须与镜像里的 has_fba 一致；不一致是两个真相，要点名而不是挑一个信
      if ((row.state === 'not_applicable') !== !block.has_fba) {
        orphans.push({ kind: 'na_disagrees_with_seller', key: k });
      }
      if (row.state !== 'not_applicable') {
        // ★ 这一格用掉的需求有两处来源（后端的 demand 与前端自己算的 Σ）；不一致要点名
        //   OP-17：mock 标了「未重算」的行自己声明没有现时的数，不是证人
        const mine = demandAt(block, period);
        const theirs: QtyValue = row.demand === null ? { kind: 'unknown' } : { kind: 'num', value: row.demand };
        if (row.reason !== 'not_recomputed_in_mock' && JSON.stringify(mine) !== JSON.stringify(theirs)) {
          orphans.push({ kind: 'demand_disagrees', key: k });
        }
        // ★ spec §4.6：店铺 = Σ msku。后端算了一遍，这里再加一遍互为证人 —— 对不上就是两个真相
        if (hasMskuForecast) {
          const sum = sumUnits(block.mskus.map((r) => r.forecast.find((f) => f.period === period)?.closing ?? null));
          const closing: QtyValue = row.closing === null ? { kind: 'unknown' } : { kind: 'num', value: row.closing };
          if (JSON.stringify(sum) !== JSON.stringify(closing)) {
            orphans.push({ kind: 'forecast_sum_disagrees', key: k });
          }
        }
      }
      block.stock.push(row);
    }
  }

  for (const [k, row] of stockByKey) {
    if (!usedStock.has(k)) orphans.push({ kind: 'inventory_without_demand', key: `${row.sku}/${row.sid}/${row.period}` });
  }

  // ★ F9 裁定：月链是恒等式（14 §1.1 ④），必须由代码强制。只在两端都是具体数字时比较——
  //   缺格/未知/不适用不算「断」，它们是另一类问题，各自已有别的 orphan kind 覆盖。
  for (const block of blocks.values()) {
    for (let idx = 1; idx < grid.periods.length; idx++) {
      const prev = block.stock.find((s) => s.period === grid.periods[idx - 1]);
      const cur = block.stock.find((s) => s.period === grid.periods[idx]);
      if (!prev || !cur) continue;
      if (prev.closing !== null && cur.opening !== null && prev.closing !== cur.opening) {
        orphans.push({ kind: 'chain_broken', key: `${block.sku}/${block.sid}/${cur.period}` });
      }
    }
  }

  const skuRow = new Map(grid.forecast_sku.map((r) => [`${r.sku}/${r.period}`, r]));
  const skuHasRows = new Set(grid.forecast_sku.map((r) => r.sku));
  // ★ 货号全集 = 块 ∪ 采购 ∪ forecast_sku。某个货号一行 forecast_sku 都没有 → 照铺全未知并点名，
  //   不许让它的在途行悄悄消失（统计被丢掉的那一侧）
  const allSkus = [...new Set([
    ...[...blocks.values()].map((b) => b.sku), ...grid.purchase.map((p) => p.sku), ...skuHasRows,
  ])];
  for (const sku of allSkus) if (!skuHasRows.has(sku)) orphans.push({ kind: 'sku_forecast_missing', key: sku });
  const totals: SkuTotals[] = allSkus.map((sku) => ({
    sku,
    cells: grid.periods.map((p) => skuRow.get(`${sku}/${p}`)).filter((r): r is SkuForecast => r !== undefined),
  }));
  // ★ 在途总数与它的组成是两个证人：对不上要点名，不挑一个信。
  //   任一为 null（OP-17 mock 改采购后未重算）⇒ 未知，没有两个证人可比
  const transit: TransitRow[] = totals.map((t) => ({
    sku: t.sku,
    cells: grid.periods.map((period) => {
      const c = skuRow.get(`${t.sku}/${period}`);
      if (c && c.in_transit !== null && c.pipeline.making !== null && c.pipeline.shipping !== null
          && c.in_transit !== c.pipeline.making + c.pipeline.shipping + c.pipeline.ordered_open) {
        orphans.push({ kind: 'in_transit_disagrees', key: `${t.sku}/${period}` });
      }
      return { period, units: c ? c.in_transit : null };
    }),
  }));

  const purchaseByKey = new Map(grid.purchase.map((p) => [`${p.sku}/${p.period}`, p]));
  const skus = [...new Set(grid.purchase.map((p) => p.sku))];
  const purchase: PurchaseRow[] = skus.map((sku) => ({
    sku,
    cells: grid.periods.map((period) => ({
      period,
      planned_units: purchaseByKey.get(`${sku}/${period}`)?.planned_units ?? null,
    })),
  }));

  return {
    periods: grid.periods, blocks: [...blocks.values()], purchase, transit, totals,
    params: grid.params, beyond: grid.beyond_horizon, unallocated: grid.unallocated,
    firstStockout: new Map(grid.forecast_msku.map((r) => [`${r.seller_sku}/${r.sid}`, r.first_stockout_date])),
    orphans,
  };
}
