import type {
  DemandCell, ForecastBridge, GridResponse, MskuForecast, Seller, SkuForecast, StoreForecast, SystemBasis,
} from '../../api/types';

/** spec §5 三种行的标签用的小网格（Task 18 / 19 共用）。
 *  ★ 手写的数必须自洽：buildGridModel 对它不许报任何 orphan —— 测试第一行就钉这个。 */

export const LABEL_SELLERS: Seller[] = [
  { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
  { seller_id: '90001', name: 'A4Pet-WM', market: 'US', has_fba: false, platform: 'walmart' },
];

const P10 = '2026-10';
const P11 = '2026-11';
const SKU = 'SKU-1';

const A10_BASIS: SystemBasis = {
  method: 'baseline_rate_v1', as_of: '2026-09-21', estimation_anchor: '2026-09-14', embargo_days: 7,
  base_rate: 4.325139122267326, days_in_month: 31, units_raw: 4.325139122267326 * 31,
  window: { from: '2026-07-21', until: '2026-09-14' }, target_window: { from: '2026-06-17', until: '2026-09-14' },
  censored_days: 0, stock_days_rejected: 0, stock_days_uncaptured: 0, reason: null,
  raw_rate: 4.0, n_eff: 19.4, target_rate: 4.5, winsor_cap: 9.0, first_seen: '2026-07-21',
  constants: { halflife_days: 14, window_days: 56, target_window_days: 90, shrink_prior: 3, winsor_q: 0.95 },
};

function dc(seller_sku: string, sid: string, period: string, system: number | null, expected: number,
            system_basis: SystemBasis | null = null): DemandCell {
  return {
    seller_sku, sid, sku: SKU, period, system_units: system, system_extrapolated: false,
    expected_units: expected, effective_units: expected, basis: 'human', system_basis,
  };
}

const A10_BRIDGE: ForecastBridge = {
  from: '2026-09-22', to: '2026-09-30', units: 36, by: 'forecast', rate: 4.0,
  month_totals: { '2026-09': 120 }, onhand_as_of: 300,
  arrivals: [], arrival_units: 0, stockout_date: null,
};

function mf(seller_sku: string, sid: string, period: string,
            v: { opening: number; demand: number; inbound: number; closing: number },
            basis: MskuForecast['basis'] = { bridge: null, arrivals: [] }): MskuForecast {
  return {
    seller_sku, sid, sku: SKU, period, state: 'ok', reason: null, unknown_since: null,
    opening: v.opening, demand: v.demand, inbound_planned: v.inbound, closing: v.closing,
    stockout_date: null, basis,
  };
}

function naMsku(period: string): MskuForecast {
  return {
    seller_sku: 'MSKU-W', sid: '90001', sku: SKU, period, state: 'not_applicable', reason: 'not_applicable',
    unknown_since: null, opening: null, demand: null, inbound_planned: null, closing: null,
    stockout_date: null, basis: { bridge: null, arrivals: [] },
  };
}

function sf(period: string, v: { demand: number; system: number; opening: number; inbound: number; closing: number }): StoreForecast {
  return {
    sku: SKU, sid: '11072', period, state: 'ok', reason: null,
    demand: v.demand, system: v.system, opening: v.opening, inbound_planned: v.inbound, closing: v.closing,
  };
}

function naStore(period: string): StoreForecast {
  return {
    sku: SKU, sid: '90001', period, state: 'not_applicable', reason: 'not_applicable',
    demand: null, system: null, opening: null, inbound_planned: null, closing: null,
  };
}

function kf(period: string, v: { opening: number; stockDemand: number; inbound: number; closing: number;
                                 making: number; shipping: number; ordered: number }): SkuForecast {
  return {
    sku: SKU, period, state: 'ok', reason: null, reason_from: null,
    // ★ 无 FBA 店的预测是 null（不适用）⇒ 货号级 Σ 预测未知；需求同理
    demand: null, system: null,
    opening: v.opening, stock_demand: v.stockDemand, inbound_planned: v.inbound, closing: v.closing,
    excluded_not_applicable: 1,
    pipeline: {
      making: v.making, shipping: v.shipping, ordered_open: v.ordered,
      ordered_open_sources: [{ period: P10, units: v.ordered, ref: 'PO-2026-0918' }],
      fba_inbound: null, fba_inbound_reason: 'not_fetched',
    },
    in_transit: v.making + v.shipping + v.ordered, in_transit_excludes: ['fba_inbound'], unfilled_purchase: [],
  };
}

export const LABEL_GRID: GridResponse = {
  plan_id: 1,
  periods: [P10, P11],
  demand: [
    dc('MSKU-A', '11072', P10, 135, 100, A10_BASIS), dc('MSKU-A', '11072', P11, 130, 120),
    dc('MSKU-B', '11072', P10, 10, 5), dc('MSKU-B', '11072', P11, 10, 5),
    dc('MSKU-W', '90001', P10, null, 60), dc('MSKU-W', '90001', P11, null, 65),
  ],
  purchase: [
    { sku: SKU, period: P10, planned_units: 200 },
    { sku: SKU, period: P11, planned_units: 80 },
  ],
  params: { as_of: '2026-09-21', route: 'oversea_sea', make_days: 30, ship_days: 60, anchor_day: 15, embargo_days: 7 },
  forecast: [
    mf('MSKU-A', '11072', P10, { opening: 264, demand: 100, inbound: 0, closing: 164 }, { bridge: A10_BRIDGE, arrivals: [] }),
    mf('MSKU-A', '11072', P11, { opening: 164, demand: 120, inbound: 90, closing: 134 }, {
      bridge: null,
      arrivals: [{ date: '2026-11-14', units: 90, order_period: P10, rule: '缺口 0 补齐，余量按期望销量再摊 90',
                   gap_part: 0, weight_part: 90, pool: 200 }],
    }),
    mf('MSKU-B', '11072', P10, { opening: 10, demand: 5, inbound: 0, closing: 5 }),
    mf('MSKU-B', '11072', P11, { opening: 5, demand: 5, inbound: 0, closing: 0 }),
    naMsku(P10), naMsku(P11),
  ],
  forecast_msku: [
    { seller_sku: 'MSKU-A', sid: '11072', sku: SKU, first_stockout_date: null },
    { seller_sku: 'MSKU-B', sid: '11072', sku: SKU, first_stockout_date: null },
    { seller_sku: 'MSKU-W', sid: '90001', sku: SKU, first_stockout_date: null },
  ],
  forecast_store: [
    sf(P10, { demand: 105, system: 145, opening: 274, inbound: 0, closing: 169 }),
    sf(P11, { demand: 125, system: 140, opening: 169, inbound: 90, closing: 134 }),
    naStore(P10), naStore(P11),
  ],
  forecast_sku: [
    kf(P10, { opening: 274, stockDemand: 105, inbound: 0, closing: 169, making: 200, shipping: 0, ordered: 50 }),
    kf(P11, { opening: 169, stockDemand: 125, inbound: 90, closing: 134, making: 0, shipping: 0, ordered: 50 }),
  ],
  beyond_horizon: [{ sku: SKU, order_period: P11, units: 80, placed: '2026-11-15', made: '2026-12-15',
                     arrive: '2027-02-13', reason: 'arrives_after_horizon' }],
  unallocated: [],
};
