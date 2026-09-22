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
/** ★ 货号级在途的权威读数：来自 `inventory[].basis.sku_level_in_transit`（每月必有），
 *  `sku_pipeline` 只是交叉校验 —— 见 `buildGridModel` 里的判据（F6 裁定） */
export interface TransitCell { period: Period; units: number | null }
export interface TransitRow { sku: string; cells: TransitCell[] }
export interface Orphan {
  kind: 'unknown_seller' | 'inventory_without_demand' | 'demand_without_inventory'
      | 'na_disagrees_with_seller' | 'in_transit_disagrees' | 'demand_disagrees' | 'chain_broken';
  key: string;
}
export interface GridModel {
  periods: Period[]; blocks: SkuBlock[]; purchase: PurchaseRow[];
  /** ★ 原样透传，仅供交叉校验/调试；页面渲染改用 `transit`（F6/F8 裁定） */
  pipeline: SkuPipelineRow[];
  transit: TransitRow[];
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

  // ★ F9 裁定：月链是恒等式（14 §1.1 ④），必须由代码强制。只在两端都是具体数字时比较——
  //   缺格/未知/不适用不算「断」，它们是另一类问题，各自已有别的 orphan kind 覆盖。
  for (const block of blocks.values()) {
    for (let idx = 1; idx < grid.periods.length; idx++) {
      const prevP = grid.periods[idx - 1]!;
      const curP = grid.periods[idx]!;
      const prev = block.inventory.find((i) => i.period === prevP);
      const cur = block.inventory.find((i) => i.period === curP);
      if (!prev || !cur) continue;
      if (prev.closing !== null && cur.onhand !== null && prev.closing !== cur.onhand) {
        orphans.push({ kind: 'chain_broken', key: `${block.sku}/${block.sid}/${curP}` });
      }
    }
  }

  // ★ F6/F8 裁定：在途的权威来源是 inventory[].basis.sku_level_in_transit（逐行都有，
  //   同一 sku 不同 sid 上应当一致）；sku_pipeline 只是交叉校验，不是权威。
  //   两处不一致（同 sku 不同 sid 的 basis 互相矛盾 / pipeline 与 basis 矛盾）都要点名，
  //   不许挑一个信。没有 pipeline 行的月份照样显示 basis 给出的数字（哪怕是 0），不许落成「未知」。
  const transitSkus = new Set<string>([
    ...[...blocks.values()].map((b) => b.sku),
    ...grid.purchase.map((p) => p.sku),
    ...grid.sku_pipeline.map((p) => p.sku),
  ]);
  const transit: TransitRow[] = [...transitSkus].map((sku) => ({
    sku,
    cells: grid.periods.map((period) => {
      const rows = grid.inventory.filter((i) => i.sku === sku && i.period === period);
      const nonNull = [...new Set(rows.map((r) => r.basis.sku_level_in_transit).filter((v): v is number => v !== null))];
      if (nonNull.length > 1) {
        orphans.push({ kind: 'in_transit_disagrees', key: `${sku}/${period}` });
      }
      const basisVal = rows.find((r) => r.basis.sku_level_in_transit !== null)?.basis.sku_level_in_transit ?? null;
      const pipelineRow = grid.sku_pipeline.find((p) => p.sku === sku && p.period === period);
      if (pipelineRow && pipelineRow.units !== basisVal && nonNull.length <= 1) {
        // ★ 两 sid 已经互相矛盾时不再重复报第二条同 key 的 orphan
        orphans.push({ kind: 'in_transit_disagrees', key: `${sku}/${period}` });
      }
      // ★ M4（终审）：交叉核对原来只有一个方向。basis 说这个月有在途、而 sku_pipeline
      //   里**根本没有这一行**，同样是两个真相 —— 少报一侧就等于挑一个信。
      //   basis 为 0 的月份不报：fixture 里缺行的月份都是 0，那是「没有在途」不是「对不上」。
      if (!pipelineRow && basisVal !== null && basisVal !== 0 && nonNull.length <= 1) {
        orphans.push({ kind: 'in_transit_disagrees', key: `${sku}/${period}` });
      }
      return { period, units: basisVal };
    }),
  }));

  const skus = [...new Set(grid.purchase.map((p) => p.sku))];
  const purchase: PurchaseRow[] = skus.map((sku) => ({
    sku,
    cells: grid.periods.map((period) => ({
      period,
      planned_units: grid.purchase.find((p) => p.sku === sku && p.period === period)?.planned_units ?? null,
    })),
  }));

  return {
    periods: grid.periods, blocks: [...blocks.values()], purchase,
    pipeline: grid.sku_pipeline, transit, orphans,
  };
}
