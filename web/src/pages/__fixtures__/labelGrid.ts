import type { GridResponse, Seller } from '../../api/types';
import short from './labelGrid.json';
import long from './labelGridLong.json';
import partial from './labelGridPartial.json';

/** spec §5 三种行的标签 / 点开看依据用的小网格（Task 18 / 19 共用）。
 *  ★ 一个数都不手写：三份 JSON 连同 demand / purchase 都由 tests/test_label_grid_fixture.py
 *    用 forecast_payload 跑出来并逐字守着 —— 喂给推演的入参就是界面拿到的格子。
 *    buildGridModel 对它们不许报任何 orphan（测试第一行钉这个）。 */

export const LABEL_SELLERS: Seller[] = [
  { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
  { seller_id: '90001', name: 'A4Pet-WM', market: 'US', has_fba: false, platform: 'walmart' },
];

/** 2026-10 ~ 11 */
export const LABEL_GRID: GridResponse = { plan_id: 1, ...(short as Omit<GridResponse, 'plan_id'>) };

/** 2026-10 ~ 2027-01：10 月下单的 200 件 2027-01-13 到货（30 + 60 天），12 / 1 月采购未填 */
export const LABEL_GRID_LONG: GridResponse = { plan_id: 1, ...(long as Omit<GridResponse, 'plan_id'>) };

/** as_of 2026-10-10 落在计划月里：2026-09 已过去、2026-10 从 10-11 起算（partial）、2026-11 照常 */
export const LABEL_GRID_PARTIAL: GridResponse = { plan_id: 1, ...(partial as Omit<GridResponse, 'plan_id'>) };
