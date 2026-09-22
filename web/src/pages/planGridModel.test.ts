import { describe, expect, it } from 'vitest';
import {
  buildGridModel, closingOfLast, demandAt, inventoryAt, outageCount, sumUnits,
} from './planGridModel';
import type { ClosingReason, DemandCell, GridResponse, InventoryCell, Seller } from '../api/types';

const P = ['2026-10', '2026-11', '2026-12'];

const sellers: Seller[] = [
  { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
  { seller_id: '90001', name: 'A4Pet-WM', market: 'US', has_fba: false, platform: 'walmart' },
];

const d = (seller_sku: string, sid: string, period: string, eff: number | null): DemandCell => ({
  seller_sku, sid, sku: 'SKU-1', period, system_units: 100, system_extrapolated: false,
  expected_units: eff, effective_units: eff, basis: eff === null ? 'unknown' : 'human',
});
const inv = (sid: string, period: string, onhand: number | null, closing: number | null,
             closing_reason: ClosingReason = null, transit: number | null = 80,
             demand: number | null = null): InventoryCell => ({
  sku: 'SKU-1', sid, period, onhand, inbound: null, closing,
  basis: { source: 'ch', as_of: '2026-09-21', includes_plan_purchase: false,
           demand: demand ?? (onhand !== null && closing !== null ? onhand - closing : null),
           reason: 'no_seller_attribution', closing_reason,
           sku_level_in_transit: transit, sources: [] },
});

function makeGrid(): GridResponse {
  return {
    plan_id: 1, periods: [...P],
    demand: [
      d('MSKU-A', '11072', P[0]!, 180), d('MSKU-A', '11072', P[1]!, 200), d('MSKU-A', '11072', P[2]!, null),
      d('MSKU-B', '11072', P[0]!, 40), d('MSKU-B', '11072', P[1]!, null), d('MSKU-B', '11072', P[2]!, null),
      d('MSKU-W', '90001', P[0]!, 60), d('MSKU-W', '90001', P[1]!, 65), d('MSKU-W', '90001', P[2]!, 70),
    ],
    purchase: P.map((p, n) => ({ sku: 'SKU-1', period: p, planned_units: n === 0 ? 500 : null })),
    inventory: [
      inv('11072', P[0]!, 420, 200), inv('11072', P[1]!, 200, null, 'unknown_demand'),
      inv('11072', P[2]!, null, null, 'unknown_demand'),
      inv('90001', P[0]!, null, null, 'not_applicable', null),
      inv('90001', P[1]!, null, null, 'not_applicable', null),
      inv('90001', P[2]!, null, null, 'not_applicable', null),
    ],
    sku_pipeline: P.map((p, n) => ({
      sku: 'SKU-1', period: p, units: n === 0 ? 80 : null,
      sources: [], no_seller_attribution: true as const,
    })),
  };
}

describe('网格模型', () => {
  it('分块：一个店铺 · 一个货号 一块；msku 挂在块下，库存挂在块上', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(m.blocks.map((b) => `${b.sid}/${b.sku}`)).toEqual(['11072/SKU-1', '90001/SKU-1']);
    expect(m.blocks[0]!.mskus.map((r) => r.seller_sku)).toEqual(['MSKU-A', 'MSKU-B']);
    expect(m.blocks[0]!.inventory).toHaveLength(3);
    // ★ 库存不挂在 msku 上 —— 挂上去就会被画两遍、加两遍
    expect(Object.keys(m.blocks[0]!.mskus[0]!.cells[0]!)).not.toContain('inventory');
  });

  it('★ 未知向后传染：任何一个是 null，和就是未知；空数组也是未知，不是 0', () => {
    expect(sumUnits([180, 200, 210])).toEqual({ kind: 'num', value: 590 });
    expect(sumUnits([180, null, 210])).toEqual({ kind: 'unknown' });
    expect(sumUnits([])).toEqual({ kind: 'unknown' });
  });

  it('期望销量跨 msku 可以求和', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(demandAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'num', value: 220 });
    expect(demandAt(m.blocks[0]!, P[1]!)).toEqual({ kind: 'unknown' });  // MSKU-B 11 月未知
  });

  it('★ 库存一格三种形态三个字：有数 / 未知 / 不适用（都由 closing_reason 说了算）', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(inventoryAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'num', value: 200 });
    expect(inventoryAt(m.blocks[0]!, P[1]!)).toEqual({ kind: 'unknown' });
    expect(inventoryAt(m.blocks[1]!, P[0]!)).toEqual({ kind: 'na' });
    // ★ 「不适用」与「未知」都不是 0，也互不相同
    expect(inventoryAt(m.blocks[1]!, P[0]!)).not.toEqual(inventoryAt(m.blocks[0]!, P[1]!));
  });

  it('★ 合计列的存量给「期末」，不是三个月相加（onhand 是一条链，加起来数三遍）', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(closingOfLast(m.blocks[0]!, m.periods)).toEqual({ kind: 'unknown' });   // 12 月未知
    const solid = buildGridModel({ ...makeGrid(), inventory: [
      inv('11072', P[0]!, 420, 200), inv('11072', P[1]!, 200, 150), inv('11072', P[2]!, 150, 100),
      inv('90001', P[0]!, null, null, 'not_applicable', null),
      inv('90001', P[1]!, null, null, 'not_applicable', null),
      inv('90001', P[2]!, null, null, 'not_applicable', null),
    ] }, sellers);
    expect(closingOfLast(solid.blocks[0]!, solid.periods)).toEqual({ kind: 'num', value: 100 });
    expect(closingOfLast(solid.blocks[0]!, solid.periods)).not.toEqual({ kind: 'num', value: 450 });
  });

  it('★ 断货计数按折叠行的 closing 数：未知与不适用都不算断货', () => {
    const g = makeGrid();
    g.inventory[0] = inv('11072', P[0]!, 420, -5);
    const m = buildGridModel(g, sellers);
    expect(outageCount(m.blocks[0]!)).toBe(1);
    expect(outageCount(m.blocks[1]!)).toBe(0);    // 整块不适用
  });

  it('★ 在途只展示不分摊：一件都没并进任何一格库存', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(m.pipeline[0]!.units).toBe(80);
    const closings = m.blocks.flatMap((b) => b.inventory.map((i) => i.closing));
    expect(closings).not.toContain(280);   // 200 + 80 —— 并进去就是这个数
  });

  it('★ basis.demand 与前端算的 Σ 是两个证人；不一致要点名，不许挑一个信', () => {
    const g = makeGrid();
    g.inventory[0] = { ...g.inventory[0]!, basis: { ...g.inventory[0]!.basis, demand: 999 } };
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'demand_disagrees', key: 'SKU-1/11072/2026-10' });
  });

  it('★ 被丢掉的那一侧必须统计：认不出的店铺 / 对不上的库存行 / 两处在途不一致', () => {
    const g = makeGrid();
    g.demand.push(d('GHOST', '99999', P[0]!, 1));
    g.inventory.push(inv('11094', P[0]!, 5, 5));                       // 没有对应的需求
    g.inventory[0] = { ...g.inventory[0]!, basis: { ...g.inventory[0]!.basis, sku_level_in_transit: 999 } };
    // ★ 在途两处来源不一致 —— 两个真相不报，最后就会有人拿其中一个去对账
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toEqual(expect.arrayContaining([
      { kind: 'unknown_seller', key: '99999/GHOST' },
      { kind: 'inventory_without_demand', key: 'SKU-1/11094/2026-10' },
      { kind: 'in_transit_disagrees', key: 'SKU-1/2026-10' },
    ]));
    expect(m.blocks.some((b) => b.sid === '99999')).toBe(false);   // 认不出的不许静默并块
  });

  it('★ 「不适用」必须与镜像里的 has_fba 一致 —— 不一致是两个真相，要点名', () => {
    const g = makeGrid();
    g.inventory[0] = { ...g.inventory[0]!,
      basis: { ...g.inventory[0]!.basis, closing_reason: 'not_applicable' } };
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'na_disagrees_with_seller', key: 'SKU-1/11072/2026-10' });
  });

  it('★ reason 恒定：它解释 inbound，不参与 closing 的判断', () => {
    const m = buildGridModel(makeGrid(), sellers);
    const all = m.blocks.flatMap((b) => b.inventory.map((i) => i.basis.reason));
    expect(new Set(all)).toEqual(new Set(['no_seller_attribution']));
  });
});
