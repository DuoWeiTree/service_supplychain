import type { QtyValue } from '../components/Qty';
import type {
  DemandBasis, GridResponse, InventoryCell, Period, Seller, Sid, SkuPipelineRow,
} from '../api/types';

export interface MskuCell {
  period: Period;
  system_units: number | null;
  system_extrapolated: boolean;
  expected_units: number | null;
  effective_units: number | null;
  basis: DemandBasis;
}
export interface MskuRow { seller_sku: string; sid: Sid; cells: MskuCell[] }
/** ★ 库存挂在**块**上（店铺 × 货号），不挂在 msku 上 */
export interface SkuBlock {
  sid: Sid; seller_name: string; has_fba: boolean; sku: string;
  mskus: MskuRow[]; inventory: InventoryCell[];
}
export interface PurchaseRow { sku: string; cells: { period: Period; planned_units: number | null }[] }
export interface Orphan {
  kind: 'unknown_seller' | 'inventory_without_demand' | 'demand_without_inventory'
      | 'na_disagrees_with_seller' | 'in_transit_disagrees' | 'demand_disagrees';
  key: string;
}
export interface GridModel {
  periods: Period[]; blocks: SkuBlock[]; purchase: PurchaseRow[];
  pipeline: SkuPipelineRow[]; orphans: Orphan[];
}

/** ★ 任何一项未知 ⇒ 和未知。空数组也是未知：没有数不等于 0 */
export function sumUnits(values: (number | null)[]): QtyValue {
  if (values.length === 0 || values.some((v) => v === null)) return { kind: 'unknown' };
  return { kind: 'num', value: values.reduce<number>((a, b) => a + (b as number), 0) };
}

export function demandAt(block: SkuBlock, period: Period): QtyValue {
  return sumUnits(block.mskus.map((r) => r.cells.find((c) => c.period === period)?.effective_units ?? null));
}

export function inventoryAt(block: SkuBlock, period: Period): QtyValue {
  const cell = block.inventory.find((i) => i.period === period);
  if (!cell) return { kind: 'unknown' };
  // ★ 「不适用」不是 0 也不是未知：该店根本没有 FBA，这一格问的问题不成立（02 §3.1a）
  // ★ 看的是 closing_reason，不是 reason —— reason 恒为 no_seller_attribution，只解释 inbound
  if (cell.basis.closing_reason === 'not_applicable') return { kind: 'na' };
  return cell.closing === null ? { kind: 'unknown' } : { kind: 'num', value: cell.closing };
}

/** ★ 合计列的存量给**期末**，不是三个月相加 —— `onhand` 是一条链，加起来等于把同一批货数三遍 */
export function closingOfLast(block: SkuBlock, periods: Period[]): QtyValue {
  const last = periods[periods.length - 1];
  return last === undefined ? { kind: 'unknown' } : inventoryAt(block, last);
}

/** ★ 断货计数按**折叠行的 closing** 数。未知与不适用都不算断货 —— 它们是「不知道」，不是「没货」 */
export function outageCount(block: SkuBlock): number {
  return block.inventory.filter((i) =>
    i.basis.closing_reason !== 'not_applicable' && i.closing !== null && i.closing <= 0).length;
}

export function buildGridModel(grid: GridResponse, sellers: Seller[]): GridModel {
  const sellerById = new Map(sellers.map((s) => [s.seller_id, s]));
  const orphans: Orphan[] = [];
  const blocks = new Map<string, SkuBlock>();
  const usedInv = new Set<string>();
  const invKey = (sku: string, sid: Sid, p: Period) => `${sku}/${sid}/${p}`;
  const invByKey = new Map(grid.inventory.map((i) => [invKey(i.sku, i.sid, i.period), i]));

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
      block = { sid: d.sid, seller_name: seller.name, has_fba: seller.has_fba, sku: d.sku, mskus: [], inventory: [] };
      blocks.set(bKey, block);
    }
    let row = block.mskus.find((r) => r.seller_sku === d.seller_sku);
    if (!row) { row = { seller_sku: d.seller_sku, sid: d.sid, cells: [] }; block.mskus.push(row); }
    row.cells.push({
      period: d.period, system_units: d.system_units, system_extrapolated: d.system_extrapolated,
      expected_units: d.expected_units, effective_units: d.effective_units, basis: d.basis,
    });
  }

  for (const block of blocks.values()) {
    for (const period of grid.periods) {
      const k = invKey(block.sku, block.sid, period);
      const cell = invByKey.get(k);
      if (!cell) { orphans.push({ kind: 'demand_without_inventory', key: k }); continue; }
      usedInv.add(k);
      // ★ 「不适用」必须与镜像里的 has_fba 一致；不一致是两个真相，要点名而不是挑一个信
      if ((cell.basis.closing_reason === 'not_applicable') !== !block.has_fba) {
        orphans.push({ kind: 'na_disagrees_with_seller', key: k });
      }
      // ★ 这一格用掉的需求有两处来源（后端的 basis.demand 与前端自己算的 Σ）；不一致要点名
      if (cell.basis.closing_reason !== 'not_applicable') {
        const mine = demandAt(block, period);
        const theirs: QtyValue = cell.basis.demand === null
          ? { kind: 'unknown' } : { kind: 'num', value: cell.basis.demand };
        if (JSON.stringify(mine) !== JSON.stringify(theirs)) {
          orphans.push({ kind: 'demand_disagrees', key: k });
        }
      }
      block.inventory.push(cell);
    }
  }

  for (const [k, cell] of invByKey) {
    if (!usedInv.has(k)) orphans.push({ kind: 'inventory_without_demand', key: `${cell.sku}/${cell.sid}/${cell.period}` });
  }

  // ★ 同一个在途数字有两处来源（basis 与 sku_pipeline）；不一致要报，不许挑一个显示
  for (const p of grid.sku_pipeline) {
    for (const cell of grid.inventory.filter((i) => i.sku === p.sku && i.period === p.period)) {
      if (cell.basis.closing_reason === 'not_applicable') continue;
      if (cell.basis.sku_level_in_transit !== p.units) {
        orphans.push({ kind: 'in_transit_disagrees', key: `${p.sku}/${p.period}` });
        break;
      }
    }
  }

  const skus = [...new Set(grid.purchase.map((p) => p.sku))];
  const purchase: PurchaseRow[] = skus.map((sku) => ({
    sku,
    cells: grid.periods.map((period) => ({
      period,
      planned_units: grid.purchase.find((p) => p.sku === sku && p.period === period)?.planned_units ?? null,
    })),
  }));

  // ★ pipeline 原样带过来，**不与 inventory 相加** —— 层级不同，加起来是每个店各多一份货
  return { periods: grid.periods, blocks: [...blocks.values()], purchase, pipeline: grid.sku_pipeline, orphans };
}
