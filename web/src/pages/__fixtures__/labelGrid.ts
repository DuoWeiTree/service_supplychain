import type { DemandCell, GridResponse, Seller, SystemBasis } from '../../api/types';
import forecast from './labelGrid.forecast.json';

/** spec §5 三种行的标签用的小网格（Task 18 / 19 共用）。
 *  ★ 预测块不手写：labelGrid.forecast.json 是 forecast_payload 用同一组入参跑出来的，
 *    tests/test_label_grid_fixture.py 逐字守着。这里的 demand / purchase 就是那组入参 ——
 *    改一边必须改另一边；buildGridModel 对它不许报任何 orphan（测试第一行钉这个）。 */

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

/** 推演只读 base_rate；估计器的中间量这里不编，给 null */
const B_BASIS: SystemBasis = {
  ...A10_BASIS, base_rate: 0.32, units_raw: 0.32 * 31, raw_rate: null, n_eff: null, target_rate: null, winsor_cap: null,
};

export const LABEL_GRID: GridResponse = {
  plan_id: 1,
  periods: [P10, P11],
  demand: [
    dc('MSKU-A', '11072', P10, 135, 100, A10_BASIS), dc('MSKU-A', '11072', P11, 130, 90, { ...A10_BASIS, days_in_month: 30, units_raw: 4.325139122267326 * 30 }),
    dc('MSKU-B', '11072', P10, 10, 5, B_BASIS), dc('MSKU-B', '11072', P11, 10, 5, { ...B_BASIS, days_in_month: 30, units_raw: 0.32 * 30 }),
    // 沃尔玛店没有销量取数源：认领时不种预测（system_units / system_basis 都是 null）
    dc('MSKU-W', '90001', P10, null, 60), dc('MSKU-W', '90001', P11, null, 65),
  ],
  purchase: [
    { sku: SKU, period: P10, planned_units: 200 },
    { sku: SKU, period: P11, planned_units: 80 },
  ],
  ...(forecast as Pick<GridResponse, 'params' | 'forecast' | 'forecast_msku' | 'forecast_store' | 'forecast_sku'
                                     | 'beyond_horizon' | 'unallocated'>),
};
