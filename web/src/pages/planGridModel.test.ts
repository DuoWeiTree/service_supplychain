import { describe, expect, it } from 'vitest';
import {
  buildGridModel, closingOfLast, demandAt, inventoryAt, outageCount, parseUnits, sumUnits,
} from './planGridModel';
import type { ClosingReason, DemandCell, GridResponse, InventoryCell, Seller } from '../api/types';
import realGrid from '../api/fixtures/grid-1.json';
import realSellersFixture from '../api/fixtures/sellers.json';

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

  it('★ 09-23 真实缺陷：有 FBA 的店给「未知在仓」不许被当成「不适用」误报',
     () => {
    // ★ sid 11072 在 sellers 里 has_fba=true；这一格 onhand=null 但成因是
    //   unknown_onhand（阶段 A 没取到这个 msku 的数字），不是该店没有 FBA。
    //   旧实现只看 onhand is null 就判「不适用」，会在这一格上误触发
    //   na_disagrees_with_seller —— 负责人在真实数据上看到的 12 个孤儿正是这个坍缩。
    const g = makeGrid();
    g.inventory[0] = { ...g.inventory[0]!, onhand: null, closing: null,
      basis: { ...g.inventory[0]!.basis, closing_reason: 'unknown_onhand' } };
    const m = buildGridModel(g, sellers);
    expect(m.orphans).not.toContainEqual(
      { kind: 'na_disagrees_with_seller', key: 'SKU-1/11072/2026-10' });
    expect(inventoryAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'unknown' });
    expect(inventoryAt(m.blocks[0]!, P[0]!)).not.toEqual({ kind: 'na' });
  });

  it('★ reason 恒定：它解释 inbound，不参与 closing 的判断', () => {
    const m = buildGridModel(makeGrid(), sellers);
    const all = m.blocks.flatMap((b) => b.inventory.map((i) => i.basis.reason));
    expect(new Set(all)).toEqual(new Set(['no_seller_attribution']));
  });
});

describe('parseUnits（F1/F2 裁定：两种人工输入共用同一套解析）', () => {
  it('空串 = 未知 ⇒ null，不是 0', () => {
    expect(parseUnits('')).toEqual({ ok: true, value: null });
    expect(parseUnits('   ')).toEqual({ ok: true, value: null });
  });
  it('非负整数直接收', () => {
    expect(parseUnits('0')).toEqual({ ok: true, value: 0 });
    expect(parseUnits('300')).toEqual({ ok: true, value: 300 });
  });
  it('★ 非数字（如「5oo」）拒收，且不落回 0', () => {
    const r = parseUnits('5oo');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.why).toContain('5oo');
  });
  it('★ 负数拒收 —— 客户端不能把这类值交给后端去兜', () => {
    const r = parseUnits('-5');
    expect(r.ok).toBe(false);
  });
  it('★ 小数拒收', () => {
    expect(parseUnits('1.5').ok).toBe(false);
  });
});

describe('★ F9 裁定：月链 onhand(N+1) ≡ closing(N) 必须由代码强制', () => {
  it('链断了要点名', () => {
    const g = makeGrid();
    // 10 月 closing=200，11 月 onhand 却是 150（不是 200）—— 链断在 11 月
    g.inventory[1] = inv('11072', P[1]!, 150, 140, null);
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'chain_broken', key: 'SKU-1/11072/2026-11' });
  });

  it('链完好不许误报', () => {
    const solid = buildGridModel({ ...makeGrid(), inventory: [
      inv('11072', P[0]!, 420, 200), inv('11072', P[1]!, 200, 150), inv('11072', P[2]!, 150, 100),
      inv('90001', P[0]!, null, null, 'not_applicable', null),
      inv('90001', P[1]!, null, null, 'not_applicable', null),
      inv('90001', P[2]!, null, null, 'not_applicable', null),
    ] }, sellers);
    expect(solid.orphans.filter((o) => o.kind === 'chain_broken')).toEqual([]);
  });

  it('缺格/未知的两端不算「断」——那是另一类问题，各自已有 orphan kind 覆盖', () => {
    // makeGrid() 本身 10→11 月 closing 是 null（unknown_demand），不该被当成链断
    const m = buildGridModel(makeGrid(), sellers);
    expect(m.orphans.filter((o) => o.kind === 'chain_broken')).toEqual([]);
  });

  it('★ 后端那份真 fixture：链条完好，零 chain_broken', () => {
    const realSellers = (realSellersFixture as { sellers: Seller[] }).sellers;
    const m = buildGridModel(realGrid as unknown as GridResponse, realSellers);
    expect(m.orphans.filter((o) => o.kind === 'chain_broken')).toEqual([]);
  });
});

describe('★ F6/F8 裁定：在途以 basis.sku_level_in_transit 为权威，sku_pipeline 只是交叉校验', () => {
  it('没有 pipeline 行的月份照样显示 basis 给出的数字（哪怕是 0），不许落成未知', () => {
    const g = makeGrid();
    // 11 月 basis 在两个 sid 上一致给 0；sku_pipeline 完全没有 11 月这一行
    g.inventory[1] = inv('11072', P[1]!, 200, null, 'unknown_demand', 0);
    g.inventory[4] = inv('90001', P[1]!, null, null, 'not_applicable', 0);
    const m = buildGridModel(g, sellers);
    const nov = m.transit.find((t) => t.sku === 'SKU-1')!.cells.find((c) => c.period === P[1]!)!;
    expect(nov.units).toBe(0);
  });

  it('同一 sku 两个 sid 的 basis 互相矛盾 —— 即便没有 pipeline 行也要点名', () => {
    const g = makeGrid();
    g.inventory[0] = inv('11072', P[0]!, 420, 200, null, 80);
    g.inventory[3] = inv('90001', P[0]!, null, null, 'not_applicable', 999);
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'in_transit_disagrees', key: 'SKU-1/2026-10' });
  });

  // ★ M4（终审）：交叉核对原来只有一个方向 —— 有 pipeline 行而 basis 对不上会报，
  //   basis 非零而**根本没有** pipeline 行则无人点名。后者同样是两个真相。
  it('★ basis 说有在途、sku_pipeline 里没有这一行 —— 也要点名', () => {
    const g = makeGrid();
    g.sku_pipeline = g.sku_pipeline.filter((p) => p.period !== P[0]!);   // 10 月那行整行拿掉
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'in_transit_disagrees', key: 'SKU-1/2026-10' });
  });

  it('★ basis 是 0 而没有 pipeline 行：那是「没有在途」不是「对不上」，不许误报', () => {
    const g = makeGrid();
    g.inventory[0] = inv('11072', P[0]!, 420, 200, null, 0);
    g.inventory[3] = inv('90001', P[0]!, null, null, 'not_applicable', null);
    g.sku_pipeline = g.sku_pipeline.filter((p) => p.period !== P[0]!);
    const m = buildGridModel(g, sellers);
    // ★ 只看 10 月这个 key：makeGrid 的 11/12 月本来就对不上（pipeline 为 null 而 basis 80），
    //   拿整张 orphans 断言会把这条判据托在别人的失败上
    expect(m.orphans.filter((o) => o.key === `SKU-1/${P[0]!}`)).toEqual([]);
  });

  it('货号级在途行按 blocks ∪ purchase ∪ sku_pipeline 的并集铺，不按 purchase 单独铺', () => {
    const g = makeGrid();
    // 追加一个只出现在 sku_pipeline、既不在 purchase 也不在任何块里的货号
    g.sku_pipeline.push({ sku: 'GHOST-SKU', period: P[0]!, units: 12, sources: [], no_seller_attribution: true });
    const m = buildGridModel(g, sellers);
    expect(m.transit.map((t) => t.sku)).toContain('GHOST-SKU');
  });

  it('★ 后端那份真 fixture：3 行 pipeline 与对应 basis 逐行相等，零 in_transit_disagrees', () => {
    const realSellers = (realSellersFixture as { sellers: Seller[] }).sellers;
    const m = buildGridModel(realGrid as unknown as GridResponse, realSellers);
    expect(m.orphans.filter((o) => o.kind === 'in_transit_disagrees')).toEqual([]);
    // 两个货号都要在 transit 里出现，且 10 月都有确定数字（不是 —）
    const oct = m.transit.map((t) => t.cells.find((c) => c.period === '2026-10')?.units);
    expect(oct.every((v) => typeof v === 'number')).toBe(true);
  });
});

describe('★ 后端那份真 fixture：orphans 应当为空（团队裁定的验收基线）', () => {
  it('5 块、零丢弃', () => {
    // ★ 09-23 补了第 5 块（A4P-TOY-003/11072）：有 FBA 但没取到在仓数字的
    //   unknown_onhand 形态。它必须与 na_disagrees_with_seller 的判据相容 ——
    //   该店 has_fba=true 且 closing_reason 不是 'not_applicable'，不该被点名。
    const realSellers = (realSellersFixture as { sellers: Seller[] }).sellers;
    const m = buildGridModel(realGrid as unknown as GridResponse, realSellers);
    expect(m.blocks).toHaveLength(5);
    expect(m.orphans).toEqual([]);
  });
});
