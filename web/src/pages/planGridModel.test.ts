import { describe, expect, it } from 'vitest';
import {
  buildGridModel, closingOfLast, demandAt, mskuStockAt, outageCount, parseUnits, stockAt, sumUnits,
  systemAt, totalsAt,
} from './planGridModel';
import type {
  DemandCell, ForecastState, GridResponse, MskuForecast, Seller, SkuForecast, StoreForecast,
} from '../api/types';
import realGrid from '../testing/fixtures/grid-1.json';
import realSellersFixture from '../testing/fixtures/sellers.json';

const P = ['2026-10', '2026-11', '2026-12'];

const sellers: Seller[] = [
  { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
  { seller_id: '90001', name: 'A4Pet-WM', market: 'US', has_fba: false, platform: 'walmart' },
];

const d = (seller_sku: string, sid: string, period: string, eff: number | null): DemandCell => ({
  seller_sku, sid, sku: 'SKU-1', period, system_units: 100, system_extrapolated: false,
  expected_units: eff, effective_units: eff, basis: eff === null ? 'unknown' : 'human',
  system_basis: null,
});
const st = (sid: string, period: string, opening: number | null, closing: number | null,
            state: ForecastState = closing === null ? 'unknown' : 'ok',
            demand: number | null = opening !== null && closing !== null ? opening - closing : null,
): StoreForecast => ({
  sku: 'SKU-1', sid, period, state,
  reason: state === 'ok' ? null : state === 'not_applicable' ? 'not_applicable' : 'unknown_demand',
  demand, system: null, system_not_applicable: false, opening, inbound_planned: 0, closing,
});
const fm = (seller_sku: string, sid: string, period: string, closing: number | null): MskuForecast => ({
  seller_sku, sid, sku: 'SKU-1', period, state: closing === null ? 'unknown' : 'ok',
  reason: closing === null ? 'unknown_demand' : null, unknown_since: null,
  opening: null, demand: null, inbound_planned: 0, closing, stockout_date: null,
  basis: { bridge: null, arrivals: [], as_of_start: null },
});
const fk = (period: string, making: number, shipping = 0, ordered = 0): SkuForecast => ({
  sku: 'SKU-1', period, state: 'ok', reason: null, reason_from: null,
  demand: null, system: null, system_not_applicable: false, opening: null,
  stock_demand: null, inbound_planned: 0, closing: null, excluded_not_applicable: 1,
  pipeline: { making, shipping, ordered_open: ordered, ordered_open_overdue: 0, ordered_open_sources: [],
              fba_inbound: null, fba_inbound_reason: 'not_fetched' },
  in_transit: making + shipping + ordered, in_transit_excludes: ['fba_inbound'],
  unfilled_purchase: [], has_image: false,
});
const naStore = () => P.map((p) => st('90001', p, null, null, 'not_applicable'));

function makeGrid(): GridResponse {
  return {
    plan_id: 1, periods: [...P],
    demand: [
      d('MSKU-A', '11072', P[0]!, 180), d('MSKU-A', '11072', P[1]!, 200), d('MSKU-A', '11072', P[2]!, null),
      d('MSKU-B', '11072', P[0]!, 40), d('MSKU-B', '11072', P[1]!, null), d('MSKU-B', '11072', P[2]!, null),
      d('MSKU-W', '90001', P[0]!, 60), d('MSKU-W', '90001', P[1]!, 65), d('MSKU-W', '90001', P[2]!, 70),
    ],
    purchase: P.map((p, n) => ({ sku: 'SKU-1', period: p, planned_units: n === 0 ? 500 : null })),
    params: { as_of: '2026-09-21', route: 'oversea_sea', make_days: 30, ship_days: 60,
              anchor_day: 15, embargo_days: 7, in_transit_as_of: '2026-09-21' },
    forecast: [],
    forecast_msku: [],
    forecast_store: [
      st('11072', P[0]!, 420, 200), st('11072', P[1]!, 200, null), st('11072', P[2]!, null, null),
      ...naStore(),
    ],
    forecast_sku: [fk(P[0]!, 80), fk(P[1]!, 0), fk(P[2]!, 0)],
    beyond_horizon: [],
    unallocated: [],
  };
}

describe('网格模型', () => {
  it('分块：一个店铺 · 一个货号 一块；msku 挂在块下，库存挂在块上', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(m.blocks.map((b) => `${b.sid}/${b.sku}`)).toEqual(['11072/SKU-1', '90001/SKU-1']);
    expect(m.blocks[0]!.mskus.map((r) => r.seller_sku)).toEqual(['MSKU-A', 'MSKU-B']);
    expect(m.blocks[0]!.stock).toHaveLength(3);
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

  it('★ 库存一格三种形态三个字：有数 / 未知 / 不适用（都由 state 说了算）', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(stockAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'num', value: 200 });
    expect(stockAt(m.blocks[0]!, P[1]!)).toEqual({ kind: 'unknown' });
    expect(stockAt(m.blocks[1]!, P[0]!)).toEqual({ kind: 'na' });
    // ★ 「不适用」与「未知」都不是 0，也互不相同
    expect(stockAt(m.blocks[1]!, P[0]!)).not.toEqual(stockAt(m.blocks[0]!, P[1]!));
  });

  it('★ 合计列的存量给「期末」，不是三个月相加（onhand 是一条链，加起来数三遍）', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(closingOfLast(m.blocks[0]!, m.periods)).toEqual({ kind: 'unknown' });   // 12 月未知
    const solid = buildGridModel({ ...makeGrid(), forecast_store: [
      st('11072', P[0]!, 420, 200), st('11072', P[1]!, 200, 150), st('11072', P[2]!, 150, 100),
      ...naStore(),
    ] }, sellers);
    expect(closingOfLast(solid.blocks[0]!, solid.periods)).toEqual({ kind: 'num', value: 100 });
    expect(closingOfLast(solid.blocks[0]!, solid.periods)).not.toEqual({ kind: 'num', value: 450 });
  });

  it('★ 断货计数按折叠行的 closing 数：未知与不适用都不算断货', () => {
    const g = makeGrid();
    g.forecast_store[0] = st('11072', P[0]!, 420, -5);
    const m = buildGridModel(g, sellers);
    expect(outageCount(m.blocks[0]!)).toBe(1);
    expect(outageCount(m.blocks[1]!)).toBe(0);    // 整块不适用
  });

  it('★ 在途只展示不分摊：一件都没并进任何一格库存', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(m.transit[0]!.cells[0]!.units).toBe(80);
    const closings = m.blocks.flatMap((b) => b.stock.map((i) => i.closing));
    expect(closings).not.toContain(280);   // 200 + 80 —— 并进去就是这个数
  });

  it('★ 店铺行的 demand 与前端算的 Σ 是两个证人；不一致要点名，不许挑一个信', () => {
    const g = makeGrid();
    g.forecast_store[0] = { ...g.forecast_store[0]!, demand: 999 };
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'demand_disagrees', key: 'SKU-1/11072/2026-10' });
  });

  it('★ 被丢掉的那一侧必须统计：认不出的店铺 / 对不上的库存行 / 两处在途不一致', () => {
    const g = makeGrid();
    g.demand.push(d('GHOST', '99999', P[0]!, 1));
    g.forecast_store.push(st('11094', P[0]!, 5, 5));                    // 没有对应的需求
    g.forecast_sku[0] = { ...g.forecast_sku[0]!, in_transit: 999 };
    // ★ 在途总数与组成不一致 —— 两个真相不报，最后就会有人拿其中一个去对账
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
    g.forecast_store[0] = { ...g.forecast_store[0]!, state: 'not_applicable', reason: 'not_applicable' };
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'na_disagrees_with_seller', key: 'SKU-1/11072/2026-10' });
  });

  it('★ 09-23 真实缺陷：有 FBA 的店给「未知在仓」不许被当成「不适用」误报',
     () => {
    // ★ sid 11072 在 sellers 里 has_fba=true；这一格 opening=null 但成因是
    //   unknown_onhand（没取到这个 msku 的在仓数字），不是该店没有 FBA。
    //   旧实现只看 onhand is null 就判「不适用」，会在这一格上误触发
    //   na_disagrees_with_seller —— 负责人在真实数据上看到的 12 个孤儿正是这个坍缩。
    const g = makeGrid();
    g.forecast_store[0] = { ...g.forecast_store[0]!, state: 'unknown', reason: 'unknown_onhand',
      opening: null, closing: null };
    const m = buildGridModel(g, sellers);
    expect(m.orphans).not.toContainEqual(
      { kind: 'na_disagrees_with_seller', key: 'SKU-1/11072/2026-10' });
    expect(stockAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'unknown' });
    expect(stockAt(m.blocks[0]!, P[0]!)).not.toEqual({ kind: 'na' });
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

describe('★ F9 裁定：月链 期初(N+1) ≡ 期末(N) 必须由代码强制', () => {
  it('链断了要点名', () => {
    const g = makeGrid();
    // 10 月期末=200，11 月期初却是 150（不是 200）—— 链断在 11 月
    g.forecast_store[1] = st('11072', P[1]!, 150, 140);
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'chain_broken', key: 'SKU-1/11072/2026-11' });
  });

  it('链完好不许误报', () => {
    const solid = buildGridModel({ ...makeGrid(), forecast_store: [
      st('11072', P[0]!, 420, 200), st('11072', P[1]!, 200, 150), st('11072', P[2]!, 150, 100),
      ...naStore(),
    ] }, sellers);
    expect(solid.orphans.filter((o) => o.kind === 'chain_broken')).toEqual([]);
  });

  it('缺格/未知的两端不算「断」——那是另一类问题，各自已有 orphan kind 覆盖', () => {
    // makeGrid() 本身 11→12 月期末是 null（unknown_demand），不该被当成链断
    const m = buildGridModel(makeGrid(), sellers);
    expect(m.orphans.filter((o) => o.kind === 'chain_broken')).toEqual([]);
  });

  it('★ 后端那份真 fixture：链条完好，零 chain_broken', () => {
    const realSellers = (realSellersFixture as { sellers: Seller[] }).sellers;
    const m = buildGridModel(realGrid as unknown as GridResponse, realSellers);
    expect(m.orphans.filter((o) => o.kind === 'chain_broken')).toEqual([]);
  });
});

describe('在途读 forecast_sku（货号级，只展示不分摊）', () => {
  it('在途为 0 的月份显示 0，不许落成未知', () => {
    const m = buildGridModel(makeGrid(), sellers);
    const nov = m.transit.find((t) => t.sku === 'SKU-1')!.cells.find((c) => c.period === P[1]!)!;
    expect(nov.units).toBe(0);
  });

  it('★ 该月没有 forecast_sku 行 → 未知（null），不是 0', () => {
    const g = makeGrid();
    g.forecast_sku = g.forecast_sku.filter((r) => r.period !== P[2]!);
    const m = buildGridModel(g, sellers);
    expect(m.transit[0]!.cells.find((c) => c.period === P[2]!)!.units).toBeNull();
    expect(totalsAt(m, 'SKU-1', P[2]!)).toBeUndefined();
  });

  it('货号级在途行按 forecast_sku 铺，不按 purchase 单独铺', () => {
    const g = makeGrid();
    // 追加一个只出现在 forecast_sku、既不在 purchase 也不在任何块里的货号
    g.forecast_sku.push({ ...fk(P[0]!, 12), sku: 'GHOST-SKU' });
    const m = buildGridModel(g, sellers);
    expect(m.transit.map((t) => t.sku)).toContain('GHOST-SKU');
  });

  it('★ 块 / 采购里有、forecast_sku 里一行都没有的货号：在途行照铺（全未知）并点名，不静默丢', () => {
    const g = makeGrid();
    g.purchase.push({ sku: 'SKU-P', period: P[0]!, planned_units: 1 });
    const m = buildGridModel(g, sellers);
    const row = m.transit.find((t) => t.sku === 'SKU-P');
    expect(row?.cells.map((c) => c.units)).toEqual([null, null, null]);
    expect(m.orphans).toContainEqual({ kind: 'sku_forecast_missing', key: 'SKU-P' });
  });

  it('★ 后端那份真 fixture：零 in_transit_disagrees，10 月每个货号都有确定数字', () => {
    const realSellers = (realSellersFixture as { sellers: Seller[] }).sellers;
    const m = buildGridModel(realGrid as unknown as GridResponse, realSellers);
    expect(m.orphans.filter((o) => o.kind === 'in_transit_disagrees')).toEqual([]);
    const oct = m.transit.map((t) => t.cells.find((c) => c.period === '2026-10')?.units);
    expect(oct.length).toBeGreaterThan(0);
    expect(oct.every((v) => typeof v === 'number')).toBe(true);
  });
});

describe('库存改从 forecast* 取（spec §4.6）', () => {
  it('stockAt 读店铺行的期末；不适用是 na，不是 0 也不是未知', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(stockAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'num', value: 200 });
    expect(stockAt(m.blocks[0]!, P[1]!)).toEqual({ kind: 'unknown' });
    expect(stockAt(m.blocks[1]!, P[0]!)).toEqual({ kind: 'na' });
  });

  it('msku 行有自己的期末', () => {
    const g = makeGrid();
    g.forecast = [fm('MSKU-A', '11072', P[0]!, 150), fm('MSKU-B', '11072', P[0]!, 50)];
    const m = buildGridModel(g, sellers);
    expect(mskuStockAt(m.blocks[0]!.mskus[0]!, P[0]!)).toEqual({ kind: 'num', value: 150 });
    expect(m.orphans.filter((o) => o.kind === 'forecast_sum_disagrees')).toEqual([]);
  });

  it('★ 店铺期末 ≠ Σ msku 期末 → 点名 forecast_sum_disagrees（两个真相）', () => {
    const g = makeGrid();
    g.forecast = [fm('MSKU-A', '11072', P[0]!, 150), fm('MSKU-B', '11072', P[0]!, 49)];
    expect(buildGridModel(g, sellers).orphans).toContainEqual(
      { kind: 'forecast_sum_disagrees', key: 'SKU-1/11072/2026-10' });
  });

  it('★ 期初[m+1] ≠ 期末[m] → chain_broken', () => {
    const g = makeGrid();
    g.forecast_store = [st('11072', P[0]!, 420, 200), st('11072', P[1]!, 199, 150),
                        st('11072', P[2]!, 150, 100), ...g.forecast_store.filter((r) => r.sid === '90001')];
    expect(buildGridModel(g, sellers).orphans).toContainEqual(
      { kind: 'chain_broken', key: 'SKU-1/11072/2026-11' });
  });

  it('在途读 forecast_sku.in_transit；组成对不上 → in_transit_disagrees', () => {
    const g = makeGrid();
    g.forecast_sku = [fk(P[0]!, 200, 0, 50), { ...fk(P[1]!, 0), in_transit: 7 }, fk(P[2]!, 0)];
    const m = buildGridModel(g, sellers);
    expect(m.transit[0]!.cells.map((c) => c.units)).toEqual([250, 7, 0]);
    expect(m.orphans).toContainEqual({ kind: 'in_transit_disagrees', key: 'SKU-1/2026-11' });
  });

  it('systemAt：店铺行的预测销量；null 是未知不是 0', () => {
    const g = makeGrid();
    g.forecast_store[0] = { ...g.forecast_store[0]!, system: 230 };
    const m = buildGridModel(g, sellers);
    expect(systemAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'num', value: 230 });
    expect(systemAt(m.blocks[0]!, P[1]!)).toEqual({ kind: 'unknown' });
  });

  it('totalsAt 给货号合计行；params / beyond / unallocated 原样带出', () => {
    const g = makeGrid();
    g.beyond_horizon = [{ sku: 'SKU-1', order_period: P[2]!, units: 500, placed: '2026-12-15',
                          made: '2027-01-14', arrive: '2027-03-15', reason: 'arrives_after_horizon' }];
    const m = buildGridModel(g, sellers);
    expect(totalsAt(m, 'SKU-1', P[0]!)!.in_transit).toBe(80);
    expect(m.params.as_of).toBe('2026-09-21');
    expect(m.beyond).toHaveLength(1);
    expect(m.unallocated).toEqual([]);
  });
});

describe('★ 后端那份真 fixture：orphans 应当为空（团队裁定的验收基线）', () => {
  it('5 块、零丢弃（含 Σ msku = 店铺 的互证）', () => {
    // ★ 09-23 补了第 5 块（A4P-TOY-003/11072）：有 FBA 但没取到在仓数字的
    //   unknown_onhand 形态。它必须与 na_disagrees_with_seller 的判据相容 ——
    //   该店 has_fba=true 且 state 不是 'not_applicable'，不该被点名。
    const realSellers = (realSellersFixture as { sellers: Seller[] }).sellers;
    const m = buildGridModel(realGrid as unknown as GridResponse, realSellers);
    expect(m.blocks).toHaveLength(5);
    // ★ 互证那条真的跑到了：真 fixture 的 msku 行都挂上了
    expect(m.blocks.every((b) => b.mskus.every((r) => r.forecast.length === m.periods.length))).toBe(true);
    expect(m.orphans).toEqual([]);
  });
});
