// ★ 逐条对应后端计划的返回体。字段名、类型、null 语义都不许自己改 ——
//   测试替身与真 API 对不上，判据⑥ 直接挂，而界面是照这份形状先写完的。
// 来源：docs/superpowers/plans/2026-09-22-stage-a-backend.md §「接口形状」

/** ★ 对外一律 "YYYY-MM"（如 "2026-10"）。库里是月初 date —— 前端永远不碰那个形态 */
export type Period = string;
/** ★ 店铺号 18 位，JS number 会精度丢失 ⇒ 全字段字符串 */
export type SellerId = string;
export type Sid = string;
export type PlanId = number;

/** 04 §2 的 S1 值域。★ 没有「未提交」—— 从未提交的计划 state 为 null */
export type LineState = '已提交' | '已确认' | '已下单' | '准备排货' | '已排货' | '已完结' | '已撤销';

export interface PlanSummary {
  plan_id: PlanId;
  title: string;
  /** ★ 这一个是月初日期 "YYYY-MM-01"（建计划时的入参口径） */
  period_start: string;
  months: number;
  owner_actor: string;
  /** null = 未归档 */
  archived_at: string | null;
  /** ★ 木桶派生；null = 从未提交过（没有 rev 就没有记录） */
  state: LineState | null;
  state_rev: number | null;
}

export interface PlanList {
  plans: PlanSummary[];
  /** ★ 被挡掉的那一侧：不说「挡掉了几张」，人只会觉得计划凭空少了 */
  excluded: { archived: number };
}

/** 看板计数的键是**中文状态名**（后端 `COUNTED`），不是英文。
 *  ★ team-lead 2026-09-22 裁定：brief 原文只列了 6 个，是旧值 —— 实际是
 *  `api/ui/dashboard.py:18-31` 的 `DERIVED_BUCKETS + known_states(cur)`：
 *  两个派生桶（进行中 / 已提交未确认）+ `plan_line_state_rank`（`004` 迁移，按 rank 排序）
 *  的全部记录状态 + 旁路终态「已撤销」。手列会漏掉第 N+1 种状态（`已确认` 09-22 前就漏过）——
 *  这份顺序必须跟后端的发出顺序一致，不是字母序或别的排法。 */
export type CountKey =
  | '进行中' | '已提交未确认'
  | '已提交' | '已确认' | '已下单' | '准备排货' | '已排货' | '已完结' | '已撤销';

export interface DashboardPlans {
  counts: Record<CountKey, number>;
  /** ★ 后端返回全集（诚实），由前端按阶段不渲染（S-20） */
  scope_note: { unreachable_in_stage_a: CountKey[]; never_submitted_excluded: number };
}

export interface UnsubmittedBoard {
  never_submitted: { plan_id: PlanId; title: string }[];
  /** 按 content_digest 判（A-2）；`since_rev` = 与哪一版比出来的 */
  changed_since_submit: { plan_id: PlanId; title: string; since_rev: number }[];
}

export interface Seller {
  seller_id: SellerId;
  name: string;
  market: string;
  /** ★ false ⇒ 该店的 FBA 库存是「不适用」，不是 0（02 §3.1a） */
  has_fba: boolean;
  platform: string;
}

/** S-15：生效值的来源。★ 冻结后靠它分清人填与采用预估 */
export type DemandBasis = 'human' | 'system' | 'unknown';

export interface DemandCell {
  seller_sku: string;
  sid: Sid;
  sku: string;
  period: Period;
  /** 系统预估；★ null = 连预估都没有 */
  system_units: number | null;
  /** ★ 14 §5：外推标记随结果一起回来，界面不许回头读原始格子 */
  system_extrapolated: boolean;
  /** ★ null = 未知（M-8），不是 0 */
  expected_units: number | null;
  /** 生效值 = 人填优先，否则系统预估，两者都没有则 null */
  effective_units: number | null;
  basis: DemandBasis;
  /** ★ F16：预测依据快照（spec §3.3，`api/ui/plans.py::_system_basis`）；
   *  null = 旧认领没种过（OP-8，桥接段读到时按未知处置，不当 0） */
  system_basis: SystemBasis | SystemBasisNoSource | null;
}

/** 认领时记下「这个 msku 压根没有销量取数源」（`api/ui/plans.py::_no_sales_source_basis`）。
 *  ★ 预测是**不适用**，不是未知：`system_basis` 为 null 的旧认领（OP-8）与查过没卖的才是未知 */
export interface SystemBasisNoSource {
  reason: 'not_applicable_no_sales_source'; cause: NoSalesSourceCause;
  days_in_month: number; units_raw: null;
}

/** `api/ui/plans.py::_system_basis` + 认领时逐月补的 `days_in_month` / `units_raw`。
 *  ★ `base_rate` 为 null 时 `reason` 说明为什么（窗口内没有销量），不是 0 */
export interface SystemBasis {
  method: 'baseline_rate_v1'; as_of: string; estimation_anchor: string; embargo_days: number;
  base_rate: number | null; days_in_month: number; units_raw: number | null;
  window: { from: string; until: string }; target_window: { from: string; until: string };
  censored_days: number; stock_days_rejected: number; stock_days_uncaptured: number;
  reason: null | 'no_sales_in_window';
  raw_rate: number | null; n_eff: number | null; target_rate: number | null;
  winsor_cap: number | null; first_seen: string | null;
  /** target_rate = target_units ÷ target_days。★ 可选：这两个键之前认领的格子里没有 */
  target_units?: number | null; target_days?: number | null;
  constants: { halflife_days: number; window_days: number; target_window_days: number;
               shrink_prior: number; winsor_q: number;
               /** 非零日少于它就不削峰。★ 可选：之前认领的格子里没有 */
               winsor_min_nonzero?: number };
}

/** `PUT .../demand/...` 的信封。★ team-lead 09-22 裁定：回的是 grid() 里 demand[] 同一格
 *  的完整键集（`api/ui/plans.py` 的 `_demand_row()`），不是它的子集 —— 曾经漏过
 *  `sku`/`effective_units`，http.ts 用 `Awaited<ReturnType<...>>` 从接口签名反推类型，
 *  掩盖了「真正的响应比类型声称的少两个键」这件事。这里改成显式命名的信封类型，
 *  不再从签名里套一个部分形状回来。 */
export interface PutDemandResult { cell: DemandCell }

export interface PurchaseCell {
  /** ★ 货号级，不带店铺（P1/P2） */
  sku: string;
  period: Period;
  planned_units: number | null;
}

/** `PUT .../purchase/...` 的信封。★ M1（终审）：与 `PutDemandResult` 同一个理由 ——
 *  `Awaited<ReturnType<SupplyChainApi['putPurchase']>>` 只是把接口签名"声称"的类型
 *  抄一遍，真实响应少发了键也照样通过。这半边此前没跟着改。 */
export interface PutPurchaseResult { cell: PurchaseCell }

export interface GridResponse {
  plan_id: PlanId;
  /** ["2026-10","2026-11","2026-12"] */
  periods: Period[];
  demand: DemandCell[];
  purchase: PurchaseCell[];
  params: ForecastParams;
  /** msku × 月 */
  forecast: MskuForecast[];
  forecast_msku: MskuFirstStockout[];
  /** ★ 店铺 × 货号 × 月 = Σ 该店该货号的 msku 行（spec §4.6） */
  forecast_store: StoreForecast[];
  /** 货号 × 月：库存合计 + 在途（在途不分摊到店铺） */
  forecast_sku: SkuForecast[];
  beyond_horizon: BeyondHorizon[];
  unallocated: Unallocated[];
  /** 取数说明（数据截至 / 丢弃计数 / 海外仓实查时刻，spec 2026-10-15） */
  source_notes?: SourceNotes;
}
export interface SourceNotes {
  as_of: string; purchase_as_of: string | null;
  /** 海外仓在途是领星实查的：查于何时；读不到的原因 */
  oversea_in_transit_at?: string | null; oversea_in_transit_error?: string | null;
  /** FBA 货件实际用的快照日（10-15 裁定：当天没采到时用前一天） */
  fba_inbound_as_of?: string | null;
  dropped?: Record<string, unknown>;
}
/** 当月在途的一条（spec 2026-10-15）：运营拿 `ref` 去领星核实 */
export interface MonthInTransitItem {
  source: 'fba' | 'oversea'; ref: string; units: number; eta: string | null;
  /** 到达日未知 / 已过还没收完：此刻在路上，只计 as_of 所在月 */
  current_only: boolean;
  sid?: Sid; msku?: string; status?: string; deliver_from?: string | null;
  from_wid?: number; to_wid?: number; shipped_at?: string | null;
}
/** 货号 × 月「当月在途」= 当月任一天在路上的货（FM-D2）。`total` / 某来源为 null = 读不到（不是 0） */
export interface MonthInTransit {
  total: number | null; fba: number | null; oversea: number | null; items: MonthInTransitItem[];
  /** as_of 之前的月份：那时在不在路上没有记录（全是 null，不是 0） */
  before_as_of?: boolean;
}

// ─── 预测块（spec §6 · `api/ui/grid_forecast.py::_serialize`） ───

export type ForecastState = 'ok' | 'partial' | 'unknown' | 'not_applicable' | 'elapsed' | 'no_msku';
/** ★ null 只在 ok 时出现 */
export type ForecastReason = null | 'as_of_inside_month' | 'unknown_onhand' | 'unknown_demand'
  | 'bridge_rate_unknown' | 'inbound_unknown' | 'not_applicable' | 'elapsed' | 'no_msku'
  /** spec 2026-10-14：当天没有 FBA 货件快照 —— 在途未知，月末跟着未知 */
  | 'fba_inbound_unavailable';

/** 一次计划采购到货。★ `units` 为 null = 这批分不出去（OP-12），不是 0 */
export interface PurchaseArrival {
  kind?: undefined;
  date: string; units: number | null; order_period: Period; pool: number | null;
  rule?: string; gap_part?: number; weight_part?: number;
  reason?: 'gap_unknown';
}
/** 已发出的 FBA 货件（spec 2026-10-14）：到货日 = 亚马逊送达时段结束日 */
export interface FbaShipmentRef {
  kind: 'fba_shipment'; shipment_id: string; status: string;
  deliver_from: string | null; deliver_to: string | null; order_period: null;
}
export interface FbaArrival extends FbaShipmentRef { date: string; units: number }
export type ForecastArrival = PurchaseArrival | FbaArrival;
/** 期初两部分（OP-19 c）：可售 + 调仓中 */
export interface OnhandParts { fulfillable: number; fc_transfers: number }
/** 一段扣减的每一步（`forecast/calendar.py::segment_detail`，终审 I-4）：
 *  month_total × (前半月天数 ÷ first_half_divisor + 后半月天数 ÷ second_half_divisor) = raw → 向上取整 units。
 *  ★ 面板照这个复算，不自己再算一遍 */
export interface SegmentDetail {
  from: string; to: string; month_total: number; month_days: number;
  first_half_days: number; first_half_divisor: number;
  second_half_days: number; second_half_divisor: number;
  raw: number; units: number;
}
/** 桥接段的一个月：月总量 = ceil(速率 × 当月天数)，`month_total_raw` 是取整前的乘积 */
export interface BridgeSegment extends SegmentDetail { month: Period; month_total_raw: number }
/** W-6 桥接段（as_of 次日 → 首个计划月前一天）。`by`：`forecast` 按预测速率；
 *  `first_month_expected` 没有预测速率时按首月期望折成日速率（终审 I-5，`first_month_expected` 给折算用的数）；
 *  `unknown` 两样都没有 —— `rate` / `units` 为 null */
export interface ForecastBridge {
  from: string; to: string; units: number | null;
  by: 'forecast' | 'first_month_expected' | 'unknown'; rate: number | null;
  first_month_expected?: { period: Period; units: number; days: number };
  month_totals: Record<Period, number>; segments: BridgeSegment[]; onhand_as_of: number;
  onhand_parts?: OnhandParts | null;
  arrivals: ForecastArrival[]; arrival_units: number | null; stockout_date: string | null;
}
/** as_of 落在本月里（state = partial）：台账从 `from`（as_of 次日）起，起点是 as_of 当天可售。
 *  ★ 本月已过去的那几天不在账上，所以这不是「期初」；其余月份为 null */
export interface ForecastAsOfStart {
  as_of: string; onhand_as_of: number; from: string;
  onhand_parts?: OnhandParts | null;
  /** 全月期望切到 as_of 次日起那一段；期望未填为 null */
  segment: SegmentDetail | null;
}
export interface MskuForecast {
  seller_sku: string; sid: Sid; sku: string; period: Period;
  state: ForecastState; reason: ForecastReason; unknown_since: Period | null;
  opening: number | null; demand: number | null; inbound_planned: number | null;
  closing: number | null;
  /** 该月内第一个断货日（OP-18）；整本台账第一个在 `forecast_msku` */
  stockout_date: string | null;
  basis: { bridge: ForecastBridge | null; arrivals: ForecastArrival[]; as_of_start: ForecastAsOfStart | null };
}
export interface MskuFirstStockout {
  seller_sku: string; sid: Sid; sku: string; first_stockout_date: string | null;
}
export interface StoreForecast {
  sku: string; sid: Sid; period: Period; state: ForecastState; reason: ForecastReason;
  demand: number | null; system: number | null;
  /** ★ 店里每个 msku 都没有销量取数源 ⇒ 预测不适用（`system` 为 null 但不是未知） */
  system_not_applicable: boolean;
  opening: number | null; inbound_planned: number | null; closing: number | null;
}
/** ★ making / shipping 由计划采购推出，恒有数（`forecast/plan_forecast.py::SkuMonth`） */
export interface SkuPipeline {
  making: number; shipping: number; ordered_open: number;
  /** ordered_open 里已逾期的那部分（OQ-6，终审 I-2） */
  ordered_open_overdue: number;
  /** ★ `period` 是**预计到货月**（采购单 ETA 所在月），不是下单月；
   *  `overdue` = 预计到货月早于 `params.in_transit_as_of` 所在月（OQ-6，不改日期） */
  ordered_open_sources: { period: Period; units: number; ref: string; overdue: boolean }[];
  /** spec 2026-10-14：已进月末的 FBA 货件剩余；null = 当天没采到（未知，不是 0） */
  fba_inbound: number | null; fba_inbound_reason: null | 'fba_inbound_unavailable';
}
export interface SkuForecast {
  sku: string; period: Period; state: ForecastState; reason: ForecastReason;
  /** 未知时：让它未知的那个 msku */
  reason_from: { seller_sku: string; sid: Sid } | null;
  demand: number | null; system: number | null;
  /** ★ 每家店的预测都不适用；不适用的店不进 `system` 的 Σ（不当 0，也不让它变未知） */
  system_not_applicable: boolean;
  opening: number | null;
  /** ★ 只含进库存求和的 msku —— 恒等式用这个，不是 demand */
  stock_demand: number | null; inbound_planned: number | null;
  closing: number | null; excluded_not_applicable: number;
  pipeline: SkuPipeline; in_transit: number; in_transit_excludes: 'fba_inbound'[];
  /** spec 2026-10-15：当月在途（FBA 货件 + 海外仓）；null = 这次没取（不是 0） */
  month_in_transit: MonthInTransit | null;
  unfilled_purchase: Period[];
  /** ★ 三态收敛成一个布尔（sku-image 计划 Task 3）：有真图 true；只有占位图
   *  false；`sku_category` 里没有这个货号也 false —— 从不是 null、从不缺字段。
   *  界面据它决定出不出图片入口（Task 4，负责人裁定 I-A：按需预览）。 */
  has_image: boolean;
}
/** ★ 日期只由采购月 + 提前期定；`units` 与「这条还在不在」随采购量变 */
export interface BeyondHorizonPurchase {
  kind?: undefined;
  sku: string; order_period: Period; units: number; placed: string; made: string; arrive: string;
  reason: 'arrives_after_horizon' | 'arrives_before_as_of';
}
export interface BeyondHorizonFba extends FbaShipmentRef {
  sku: string; seller_sku: string; sid: Sid; units: number; arrive: string; reason: 'arrives_after_horizon';
}
export type BeyondHorizon = BeyondHorizonPurchase | BeyondHorizonFba;
/** 整批分不出去（`forecast/allocate.py`） */
export interface UnallocatedBatch {
  scope: 'batch'; kind?: undefined;
  sku: string; order_period: Period; arrive: string; units: number | null;
  reason: 'gap_unknown' | 'no_fba_msku';
  unknown: [string, Sid][];
}
/** 分到了、但这个 msku 推演不了（不适用 / 在仓未知） */
export interface UnallocatedMsku {
  scope: 'msku'; kind?: undefined;
  sku: string; order_period: Period; arrive: string; units: number | null;
  reason: 'not_applicable' | 'unknown_onhand';
  seller_sku: string; sid: Sid; date: string; pool: number | null;
  rule?: string; gap_part?: number; weight_part?: number;
}
/** FBA 货件没进台账：逾期未签收 / 没有送达时段 / 这个 msku 推演不了（spec 2026-10-14） */
export interface UnallocatedFba extends FbaShipmentRef {
  scope: 'msku'; sku: string; seller_sku: string; sid: Sid; units: number; arrive: string | null;
  reason: 'fba_overdue' | 'fba_no_eta' | 'not_applicable' | 'unknown_onhand';
}
export type Unallocated = UnallocatedBatch | UnallocatedMsku | UnallocatedFba;
export interface ForecastParams {
  as_of: string; route: 'oversea_sea'; make_days: number; ship_days: number;
  anchor_day: number; embargo_days: number | null;
  /** 判在途「已逾期」用的快照日：采购快照日；源没给采购快照日时退到 as_of */
  in_transit_as_of: string;
}

export interface ClaimHolder { plan_id: PlanId; title: string; actor: string }

export interface CatalogMsku {
  seller_sku: string;
  sid: Sid;
  seller_name: string;
  /** ★ false = 已被别的计划占用；★ 行**留在表里标出来，不过滤**（P11） */
  selectable: boolean;
  claimed_by: ClaimHolder | null;
}

/** 06 §1.2：店铺没挂渠道 → 建不出格子，必须点名。★ 阶段 A 后端恒返回空数组 */
export interface UnbuildableSeller { sid: Sid; reason: 'no_channel_code' }

export interface CatalogItem {
  sku: string;
  name: string;
  mskus: CatalogMsku[];
  unbuildable_sellers: UnbuildableSeller[];
  /** 占用方唯一时才有一个答案；两张计划各占一部分 → null，名单在 claimed_by_plans */
  claimed_by: { plan_id: PlanId; title: string } | null;
  claimed_by_plans: { plan_id: PlanId; title: string }[];
}

export interface CatalogResult {
  /** ★ 与「查不到」分得开：没给条件是 need_query，不是空结果 */
  need_query: boolean;
  truncated: boolean;
  limit: number;
  items: CatalogItem[];
  /** 头部接口形状块没列它（实现里有）⇒ 可选。★ 判「查不到」一律用 `!need_query && items.length === 0`，
   *  不依赖这个字段 —— 依赖一个可能缺的字段去判空，缺了就会显示成「还没搜」 */
  matched?: number;
}

export interface ClaimTarget { seller_sku: string; sid: Sid }
/** ★ 没有历史 ≠ 预估 0：格子照建，`system_units` 留 null 并点名，
 *  不能让「拿不到历史」悄悄长得跟「预估出来是 0」一样。
 *
 *  ★ 两个值回答的是**两件不同的事**，不许合并：
 *  - `no_sales_history` —— 查过了，这个 msku 一行销量都没有（新品）。
 *  - `not_applicable_no_sales_source` —— **不适用**：这个店压根没有销量取数源，
 *    这一格只能由人来填。成因在 `cause` 里（后端 `api/ui/plans.py`）。
 *  「查过了没有」与「压根没得查」长得一样的话，界面就没法告诉人哪一格该自己填。 */
export type NoHistoryReason = 'no_sales_history' | 'not_applicable_no_sales_source';
/** `not_applicable_no_sales_source` 的成因。★ 与 reason 分开：reason 是「这一格是什么」，
 *  cause 是「为什么」。挤进一个字段就会出现一个名字里写着 non_amazon 的标记被贴在
 *  一个 Amazon 店上 —— 那个标记会如实地撒谎。 */
export type NoSalesSourceCause = 'non_amazon_platform' | 'store_absent_from_order_report';
export interface NoHistoryEntry {
  seller_sku: string;
  sid: Sid;
  reason: NoHistoryReason;
  /** 只在 `reason === 'not_applicable_no_sales_source'` 时出现 */
  cause?: NoSalesSourceCause;
}
export interface ClaimResult {
  claimed: { seller_sku: string; sid: Sid; sku: string };
  /** ★ team-lead 09-22 裁定：`api/ui/plans.py:183-185` 保证返回，两个键都不可选 ——
   *  认领这一下真种出了几个月的格子，brief 原文的 `ClaimResult` 漏了这两个 */
  seeded: { demand_cells: number; purchase_cells: number };
  no_history: NoHistoryEntry[];
  /** ★ 同一计划里已在认领中 → 幂等空操作：没取数、没重种格子（seeded 为 0） */
  already_claimed: boolean;
}
/** `POST .../forecast/recompute`（task-23）：已认领的格子重新算一次预测销量。
 *  ★ 绝不碰 expected_units —— 只重算 system_units/system_basis。
 *  ★ 「算不出速率」与「算出来了」分开列，不合成一个成功计数：no_rate 是 msku 级
 *  （一个 msku 各月速率相同，一条就够），recomputed 是格子级、逐格点名变化，
 *  unchanged 只是一个数（before === after 的格子）。 */
export interface RecomputedCell {
  seller_sku: string; sid: Sid; period: Period;
  before: number | null; after: number | null;
}
export interface RecomputeResult {
  recomputed: RecomputedCell[];
  unchanged: number;
  no_rate: NoHistoryEntry[];
}
// ★ 后端把丢掉的格逐条回给界面（含人填过的数），不是一个数
export interface DroppedCell { period: string; expected_units: number | null }
/** ★ 业主裁定 2026-09-27（推翻 F5「搁浅采购格未删、待处理」）：货号没有 msku 了，采购格随之删掉，
 *  逐格带值回报（null = 没填，照报） */
export interface DroppedPurchaseCell { sku: string; period: string; planned_units: number | null }
export interface ReleaseResult {
  released: { seller_sku: string; sid: Sid };
  dropped_cells: DroppedCell[];
  /** 释放的是该货号在本计划的最后一个 msku 时非空；否则 [] */
  dropped_purchase_cells: DroppedPurchaseCell[];
}
/** `DELETE /plans/{id}/skus/{sku}`：跨店释放全部 msku + 删期望与采购格，一个事务 */
export interface RemoveSkuResult {
  sku: string;
  released: { seller_sku: string; sid: Sid }[];
  dropped_cells: (DroppedCell & { seller_sku: string; sid: Sid })[];
  dropped_purchase_cells: DroppedPurchaseCell[];
}

/** S-14 + 013：空（没打算采购）与 0（明确表态）是两个值 —— R1「空 ≠ 0」 */
export type SkipReason = 'empty_purchase' | 'zero_purchase' | 'no_claimed_msku';
export interface SkippedCell { sku: string; period: Period; reason: SkipReason }

/** 再提交（R2）的每一类都是「计数 + 明细」—— 只给数，人对不上是哪几条 */
export interface SubmitBucket<T> { count: number; lines: T[] }
export interface SubmitKept { sku: string; period: Period; line_id: number; state: LineState; total_units: number }
export interface SubmitReplaced {
  sku: string;
  period: Period;
  old_line_id: number;
  new_line_id: number;
  total_units: { from: number; to: number };
  demand_at_submit: { from: number; to: number };
}
export interface SubmitMoved { sku: string; period: Period; line_id: number; total_units: number }
/** 裁定 B：已进采购、总量没变、分店拆分变了 —— 记录带着旧拆分留下。null = 这家店没数，不是 0 */
export interface SubmitSplitFrozen {
  sku: string;
  period: Period;
  line_id: number;
  split: { from: Record<Sid, number | null>; to: Record<Sid, number | null> };
}
/** 裁定 A：这次铸不出、但已 已完结 —— 终态撤不掉，原地留着 */
export interface SubmitLeftCompleted { sku: string; period: Period; line_id: number; rev: number; total_units: number }

export interface SubmitResult {
  rev: number;
  /** ★ 这一版的记录条数（保留 + 替换 + 新增），不只是新铸的 */
  lines: number;
  skipped: SkippedCell[];
  /** ★ team-lead 09-22 裁定：两个都是保证的，不是可选（`api/ui/submit.py` 恒返回两者） */
  in_flight: boolean;
  content_digest: string;
  kept: SubmitBucket<SubmitKept>;
  replaced: SubmitBucket<SubmitReplaced>;
  added: SubmitBucket<SubmitMoved>;
  removed: SubmitBucket<SubmitMoved>;
  split_frozen: SubmitBucket<SubmitSplitFrozen>;
  left_completed: SubmitBucket<SubmitLeftCompleted>;
}

/** 409 `lines_in_purchase`（D1）：`fields.lines` 的一条 */
export interface LineInPurchase {
  sku: string;
  period: Period;
  line_id: number;
  state: LineState;
  change: 'replace' | 'remove';
  po_ids: PoId[];
  /** to = null：这次不再包含（不是 0） */
  total_units: { from: number; to: number | null };
}

export interface Rev {
  rev: number;
  content_digest: string;
  is_current: boolean;
  in_flight: boolean;
  submitted_by: string;
  submitted_at: string;
  lines: number;
}

export interface RevList {
  revs: Rev[];
  /** ★ 两个标记不是一回事（06 §1.3）：流转中 ≠ 当前使用 */
  in_flight_rev: number | null;
  current_rev: number | null;
}

export interface DiffMoved { sku: string; period: Period; total_units: number }
export interface DiffChanged {
  sku: string;
  period: Period;
  total_units: { from: number; to: number };
  demand_at_submit: { from: number; to: number };
}
/** ★ 三个数组分开，不合成一个「变化量」—— 新增和改动的处置不同 */
export interface PlanDiff { added: DiffMoved[]; removed: DiffMoved[]; changed: DiffChanged[] }

export interface CancelRevResult {
  cancelled: number[];
  /** ★ team-lead 09-22 裁定：`api/ui/submit.py:213-214` 保证返回，不许丢——
   *  「这一版本来就只有 1 条」与「另外 3 条早已在终态」长得一模一样，
   *  丢了这个数就分不清两者 */
  skipped_terminal: number;
  reason: string;
}

/** 14 B12：提前期是用户变量，全局配置，所有计划共用。★ `route` 目前只有一种，
 *  但字段保留是因为后端按航线区分（`GET/PUT /v1/lead-time`，Task 8） */
export interface LeadTime {
  route: 'oversea_sea'; make_days: number; ship_days: number; anchor_day: number;
  valid_from: { making: string; shipping: string };
}
export interface LeadTimeInput { make_days?: number; ship_days?: number }
/** ★ `changed` 只列真变了的段（后端按 old→to 记日志，Task 8）；没变的段不进这个数组 */
export interface LeadTimeResult extends LeadTime {
  changed: { segment: 'making' | 'shipping'; from: number; to: number }[];
}

export interface ListPlansQuery {
  /** ★ 只发契约里声明过的参数 —— 未声明查询参数一律 400 */
  state?: LineState;
  owner?: string;
  archived?: boolean;
}
export interface CreatePlanInput { title: string; period_start: string; months: number }
export interface CatalogQuery { q?: string; limit?: number }

// ───────── 采购单（spec §8.1；字段照 api/ui/purchase_orders.py · repo/purchase.py · usecase/*）─────────

export type PoState = '草稿' | '已下单';
export type PoId = number;

export interface PoSource { line_id: number; seller_id: SellerId; units: number }
/** 读回的来源带来源链（repo.read_po JOIN plan_line / plan）：组满的记录不在候选里，链不能靠候选补 */
export interface PoSourceOut extends PoSource {
  plan_id: PlanId;
  plan_title: string;
  rev: number;
  sku: string;
  period: Period;
}

/** 请求体里的一行：price 为字符串（后端 Decimal，库里 numeric::text） */
/** ★ purchaser_id / opt_uid / wid 后端是整数列（010_po.sql：int / bigint / int），JSON 里是数；
 *  界面输入框里是文本，在 poEditorModel 的 fromPo / toBody 边界上转换。契约样本：testing/fixtures/contract/ */
export interface PoLineInput {
  sku: string;
  wid: number;
  units: number;
  price: string;
  sources: PoSource[];
  unplanned_reason?: string;
  /** YYYY-MM-DD；后端 repo.replace_lines 按 ln.get 读，可不带 */
  expect_arrive_date?: string;
}
export interface PoBody {
  supplier_id: string;
  purchaser_id: number;
  opt_uid: number;
  qc_type: 0 | 1 | 2;
  lines: PoLineInput[];
}

export interface PoLine {
  line_no: number;
  sku: string;
  wid: number;
  units: number;
  price: string;
  expect_arrive_date: string | null;
  unplanned_reason: string | null;
  lx_item_id: string | null;
  sources: PoSourceOut[];
  sources_units: number;
  unplanned_units: number;
}

export interface PreflightGate {
  gate: string;
  ok: boolean;
  /** 随闸不同（line_no / offenders / units …），后端 `_gate(**detail)` */
  detail: Record<string, unknown>;
}
export interface PoAttentionBrief { attention_id: number; kind: AttentionKind; opened_at: string }

export interface PurchaseOrder {
  po_id: PoId;
  supplier_id: string;
  purchaser_id: number;
  opt_uid: number;
  qc_type: 0 | 1 | 2;
  wid: number | null;
  custom_order_sn: string;
  /** 草稿为 null */
  order_sn: string | null;
  state: PoState;
  placed_at: string | null;
  draft_version: number;
  created_by: string;
  is_void: boolean;
  discarded_at: string | null;
  lines: PoLine[];
  source_line_states: { line_id: number; state: LineState }[];
  attention: PoAttentionBrief[];
  preflight: PreflightGate[];
  confirm_token: string;
  /** 领星单头最近一次观察（po_snapshot）。null = 从没读回过 */
  lx: LxObservation | null;
}

/** 领星单头的一次观察。status / status_shipped 码见领星 PurchaseOrderList 文档 */
export interface LxObservation {
  status: number;
  status_shipped: number | null;
  quantity_real: number | null;
  quantity_receive: number | null;
  quantity_entry: number | null;
  source: 'api' | 'ch';
  captured_at: string;
  call_id: number | null;
}
/** POST /purchase-orders/{id}/read-back：checked=false 时 lx 是库里上一次观察（可能为 null） */
export interface ReadBackResult {
  checked: boolean;
  read_outcome: string | null;
  read_cause: string | null;
  read_back_call: number | null;
  problems: Record<string, unknown>[];
  lx: LxObservation | null;
}

export interface PoSummary {
  po_id: PoId;
  custom_order_sn: string;
  supplier_id: string;
  supplier_name: string;
  state: PoState;
  order_sn: string | null;
  is_void: boolean;
  lines: number;
  units: number;
  open_attention: number;
  created_by: string;
  created_at: string;
}
export interface PoList { purchase_orders: PoSummary[] }

export interface PreflightResult { po_id: PoId; ok: boolean; gates: PreflightGate[] }

/** 下单结果（usecase/place_po.py）。needs_verify 的可选字段随路径不同：
 *  place 内查重给 lookup_*；_lost_race 给 settled_by（及对方回报的字段，L8 禁词不在此声明）；有同键异单时给 landed_mismatch。 */
export type PlaceResult =
  | { outcome: 'placed'; po_id: PoId; order_sn: string | null; erp_call_id?: number;
      found_by?: 'lookup' | 'already_recorded'; settled_by?: 'recover';
      landed_mismatch?: string | string[]; timeline?: string }
  | { outcome: 'rejected'; po_id: PoId; lx_code: string | number | null; lx_msg: string | null;
      rejected_kind: RejectedKind; host: string; path: string; erp_call_id: number }
  | { outcome: 'not_sent'; po_id: PoId; cause: string | null; erp_call_id: number; hint: string }
  | { outcome: 'needs_verify'; po_id: PoId; erp_call_id: number | null; timeline: string;
      lookup_call_id?: number; lookup_outcome?: string; truncated?: boolean;
      landed_mismatch?: string[]; settled_by?: 'recover';
      order_sn?: string | null };

export type AttentionKind = 'needs_verify' | 'landed_mismatch' | 'stuck_call';
export interface AttentionItem {
  /** stuck_call 是读时推导，没有行 ⇒ null */
  attention_id: number | null;
  po_id: PoId;
  kind: AttentionKind;
  opened_at: string;
  custom_order_sn: string;
  supplier_id: string;
  timeline: string;
  /** 以下仅 stuck_call */
  call_id?: number;
  endpoint?: string;
  actor?: string;
  started_at?: string;
  stale_after_minutes?: number;
}
export interface AttentionQueue { count: number; items: AttentionItem[] }

/** 后端判定（erp/transport.py::rejection_kind）：auth = 授权 / 权限，改草稿无用 */
export type RejectedKind = 'auth' | 'business';
export interface PoAttempt {
  call_id: number;
  at: string;
  actor: string;
  endpoint: string;
  host: string | null;
  path: string | null;
  idem_key: string | null;
  elapsed_ms: number | null;
  outcome: string | null;
  http_status: number | null;
  lx_code: string | number | null;
  lx_msg: string | null;
  /** 只有 rejected 有 */
  rejected_kind: RejectedKind | null;
  cause: string | null;
  retry_of: number | null;
  request: Record<string, unknown> | null;
  response: Record<string, unknown> | null;
}
export interface PoEvent { from: string; to: string; actor: string; reason: string | null; src: string | null; at: string }
export interface PoAttentionRow {
  attention_id: number;
  kind: AttentionKind;
  opened_at: string;
  resolved_at: string | null;
  resolution: string | null;
  resolved_by: string | null;
  reason: string | null;
  detail: Record<string, unknown>;
}
export interface TimelineConclusion {
  kind: AttentionKind;
  known: string;
  unknown: string;
  /** needs_verify */
  last_lookup?: { call_id: number; at: string; outcome: string; truncated: boolean | null;
                  window: Record<string, unknown> } | null;
  how_to_search?: { custom_order_sn: string | null; window: Record<string, unknown> };
  /** landed_mismatch */
  facts?: Record<string, unknown>;
}
export interface PoTimeline {
  po_id: PoId;
  conclusion: TimelineConclusion | null;
  attempts: PoAttempt[];
  events: PoEvent[];
  attention: PoAttentionRow[];
}

export type RecheckResult = Extract<PlaceResult, { outcome: 'placed' | 'needs_verify' }>;
export interface RegisterResult { outcome: 'placed'; po_id: PoId; order_sn: string; found_by: 'manual' }
export interface ConfirmAbsentResult { po_id: PoId; unlocked: ('place' | 'update' | 'delete')[] }
export interface AcknowledgeResult { po_id: PoId; resolution: 'mismatch_acknowledged' }

export interface PurchaseOptions {
  suppliers: { supplier_id: string; name: string }[];
  warehouses: { wid: number; name: string }[];
  purchasers: { purchaser_id: number; source: '本服务' | '领星历史'; count: number | null }[];
  opt_uids: { opt_uid: number; name: string | null; source: '本服务' | '领星历史'; count: number | null }[];
  history: { available: boolean; cause: string | null; as_of: string | null; dropped: number };
}

export type PurchaseCheckName = 'po_source' | 'line_state' | 'po_unplanned' | 'po_landed';
export interface PurchaseChecks {
  ok: boolean;
  counts: Record<PurchaseCheckName, number>;
  violations: { check_name: PurchaseCheckName; ref: string }[];
}

/** PB-D22：没有分店上限。expected = null 是没有依据，不是 0 */
export interface CandidateSeller { expected: number | null; used: number }
export interface CandidateLine {
  line_id: number;
  plan_id: PlanId;
  /** plan.title：采购侧要说出「来自哪张计划」，编号人认不出 */
  plan_title: string;
  rev: number;
  sku: string;
  period: Period;
  total_units: number;
  demand_at_submit: number;
  demand_by_seller: Record<SellerId, { units: number | null; basis?: string } | null>;
  state: LineState;
  confirmed_units: number;
  ordered_units: number;
  remaining: number;
  sellers: Record<SellerId, CandidateSeller>;
}
export interface CandidateLines {
  lines: CandidateLine[];
  groups: Record<string, unknown>;
  excluded: { state: number; sku: number; category: number; period: number; fully_confirmed: number };
}
export interface CandidateLineQuery { plan_ids?: number[]; sku?: string }

export interface LineTrace {
  line_id: number;
  sku: string;
  period: Period;
  total_units: number;
  state: LineState;
  confirmed_units: number;
  ordered_units: number;
  purchase_orders: {
    po_id: PoId; custom_order_sn: string; order_sn: string | null; state: PoState; is_void: boolean;
    sources: { seller_id: SellerId; units: number }[];
  }[];
}

// ───── 排货（阶段 C-a，spec 2026-10-10 §6；形状照 usecase/sheet.py）─────
export type SheetState = '草稿' | '已确认' | '已取消';
export type ConsignmentStage = '草稿' | '已确认' | '已投送' | '已归档' | '已放弃' | '已取消';
export type DispatchChannel = 'fba' | 'oversea';
/** ★ CA-D13 排货表的去向：平台仓 · 亚马逊 FBA / 海外仓。null = 021 之前的旧版混合 / 空表，只能看与取消 */
export type DispatchRoute = 'fba' | 'oversea';

/** CA-D11 入口：未完成的销售计划（需求侧的量，PG 内可得） */
export interface DispatchPlan {
  plan_id: PlanId; title: string; rev: number; lines: number; total: number; confirmed: number; ordered: number;
  staged: number; dispatched: number; pending: number; sheets: DispatchPlanSheet[];
  /** 每个产品（活记录）一行，还差多的在前 */
  records: DispatchPlanRecord[];
}
/** chip「下一步」的本地事实（DL-D7：只读 PG，列表不现查领星） */
export interface DispatchPlanSheet {
  sheet_id: number; state: SheetState; route: DispatchRoute | null;
  in_flight: number; attention: number; drift: string[]; to_lock: number; to_submit: number;
  packing_missing: number; logistics_missing: number; consignments: number; all_submitted: boolean;
  /** DL-D13：被回执归档的排货单数；all_archived = 活的排货单至少一张且张张已归档 */
  archived: number; all_archived: boolean;
}
export interface DispatchPlanRecord {
  line_id: number; sku: string; sku_name: string | null; period: string; state: string;
  total: number; confirmed: number; ordered: number; staged: number; dispatched: number; pending: number;
}
export interface DispatchPlans { plans: DispatchPlan[] }

export interface SheetSummary {
  sheet_id: number; state: SheetState; created_by: string; created_at: string;
  /** null = CA-D11 之前建的历史表 */
  plan_id: PlanId | null; plan_title: string | null; route: DispatchRoute | null;
  consignments: number; units: number;
}
export interface SheetList { sheets: SheetSummary[] }

export interface ConsignmentLineView {
  seller_sku: string; sid: Sid; from_wid: number; sku: string;
  units: number; planned: number; unplanned: number;
}
export interface ConsignmentView {
  consignment_id: number; channel: DispatchChannel; dest_wid: number | null; dest_sid: Sid | null;
  our_stage: ConsignmentStage; idem_key: string; lines: ConsignmentLineView[];
}
export interface PackingBox {
  box_count: number | null; box_len: number | null; box_wid: number | null; box_hei: number | null;
  box_weight: number | null; units_per_box: number | null;
}
export interface PackingRow extends PackingBox { seller_sku: string; sid: Sid; from_wid: number; orphan: boolean }

/** 七闸（G7 = 海外仓排货单选了物流方式，C-b）：ok = true 过 · false 不过 · null 无法判定 / 未检查（★ 不当成过） */
export interface DispatchGate {
  gate: 'G1' | 'G2' | 'G3' | 'G4' | 'G5' | 'G6' | 'G7';
  ok: boolean | null; code: string; detail: unknown; fix: string;
}
export interface SheetDetail {
  sheet_id: number; state: SheetState; created_by: string; created_at: string; watermark: number;
  /** 在途写调用数（DL-D8） */
  in_flight: number;
  plan_id: PlanId | null; plan_title: string | null; route: DispatchRoute | null;
  consignments: ConsignmentView[]; packing: PackingRow[];
  last_gate_run: { run_id: number; ok: boolean; gates: DispatchGate[]; stock_as_of: string | null; at: string; actor: string } | null;
  events: { from_state: string; to_state: string; actor: string; reason: string | null; at: string }[];
  /** 每个海外仓目的地选的物流方式（C-b · G7） */
  logistics: { dest_wid: number; logistics_id: number; channel_name: string; enabled: boolean }[];
  /** 在领星建过的备货单（含已作废的） */
  ext_refs: ExtRef[];
}

// ───── 提交领星备货单（C-b，spec 2026-10-12；形状照 usecase/submit_ows.py）─────
export interface LogisticsChannel {
  logistics_id: number; channel_name: string; method_name: string | null; provider_name: string | null;
  receive_country_codes: unknown; enabled: boolean;
}
export interface LogisticsChannels { channels: LogisticsChannel[]; refreshed_at: string | null }
export interface ExtRef {
  ext_ref_id: number; consignment_id: number; from_wid: number; kind: string; idem_key: string; ext_no: string;
  found_by: 'create' | 'lookup' | 'register'; actor: string; at: string; voided_at: string | null; void_reason: string | null;
  /** FBA 发货计划批次的计划号（R…）与是否已锁库存；海外仓单为 null */
  order_sns: string[] | null; locked: boolean | null;
  /** 回执读回的领星进度（领星的词）；null = 还没读回过 */
  erp_stage: string | null; stage_observed_at: string | null;
  /** 两轴不一致（我方已投送，领星走不下去了）—— 只点名 */
  drift: string | null;
}
/** 选目的仓时现查三方 sku 配对（CB-D8 同一判据） */
export interface SkuMatchStatus {
  wid: number; sku: string; product_id: number | null;
  /** 第三方海外仓（kind = oversea_3pl） */
  third_party: boolean;
  /** 接了 API 的第三方仓：必须配对才能提交备货单 */
  needs_match: boolean; matched: boolean | null; codes: string[];
}
export interface SheetReceiptResult {
  sheet_id: number; run_id: number; archived: number[];
  results: { ext_no: string; outcome: string; stage?: string; sub_status?: string | null; lx_msg?: string | null; cause?: string | null }[];
}
export interface OwsOrder {
  key: string; consignment_id: number; s_wid: number;
  /** 海外仓目的仓；FBA 批次为 null（目的 = dest_sid 本店） */
  r_wid: number | null; logistics_id: number | null;
  channel_name: string | null; idem_key: string;
  /** 海外仓：product_id / stock_num …；FBA：msku / fnsku / shipment_plan_quantity / box_num …（领星原样字段） */
  product_list: Record<string, unknown>[];
  lines: { seller_sku: string; sid: Sid; sku: string; units: number }[];
  /** null = 还没建 */
  ext_no: string | null;
  dest_sid?: Sid | null; locked?: boolean | null; order_sns?: string[] | null;
}
export interface OwsBlocker { code: 'product_id_missing' | 'logistics_missing' | 'logistics_disabled' | 'cancel_incomplete' | 'third_party_sku_missing' | 'third_party_sku_ambiguous' | 'sku_match_unavailable' | 'route_not_submittable' | 'legacy_mixed' | string; sku?: string; dest_wid?: number; logistics_id?: number; ext_nos?: string[]; product_id?: number; codes?: string[]; seller_sku?: string; sid?: Sid; from_wid?: number }
export interface DispatchAttention {
  attention_id: number; consignment_id: number; from_wid: number; idem_key: string; call_id: number | null;
  detail: Record<string, unknown>; opened_at: string;
}
export interface OwsSubmission {
  sheet_id: number; state: SheetState; orders: OwsOrder[]; blockers: OwsBlocker[]; skipped_fba: number[];
  confirm_token: string; attention: DispatchAttention[];
  route?: DispatchRoute | null;
  /** C-c1：建成了但库存没锁上的 FBA 批次参考号 —— 再提交只补锁（CC-D3） */
  to_lock?: string[];
}
export type OwsOutcome = 'created' | 'adopted' | 'rejected' | 'not_sent' | 'lookup_failed' | 'needs_verify' | 'unrecorded'
  | 'locked' | 'lock_failed';
export interface OwsResult {
  key: string; idem_key: string; outcome: OwsOutcome; ext_no?: string; attention_id?: number;
  lx_code?: unknown; lx_msg?: string | null; cause?: string | null; lookup_outcome?: string;
}
export interface OwsSubmitResult { sheet_id: number; results: OwsResult[]; dispatched: number[]; submission: OwsSubmission }
export interface SheetCancelResult {
  sheet_id: number; state: SheetState; from: SheetState; deleted_ows: string[];
  /** CC-D4：FBA 发货计划没有作废接口 —— 已释放库存、改了备注，需在领星手工作废 */
  manual_void: { ext_no: string; plans: string[]; marked: string[]; manual_void: boolean }[];
}

export interface DispatchWarehouse { wid: number; name: string; market: string | null; kind: string }
export interface DispatchMsku { seller_sku: string; fnsku: string | null }
export interface DispatchStore {
  sid: Sid; seller_name: string; market: string; has_fba: boolean;
  /** 计划里该店的期望，只作参考（空 = 未知，不是 0） */
  expected: number | null; basis: string | null; mskus: DispatchMsku[];
}
export interface LinkedPo {
  po_id: PoId; order_sn: string | null; state: PoState; is_void: boolean; wid: number; units: number;
  /** 领星行项：已入库 / 待到货（CA-D9 口径）。null = 没读到（读不到 ≠ 0） */
  entered: number | null; pending: number | null; captured: string | null;
  /** CA-D12：api = 采购页 / 现查领星的读回；ch = 每日采集；取两者中较新的 */
  source: 'api' | 'ch' | null;
}
export interface DemandRow {
  line_id: number; plan_id: PlanId; plan_title: string; rev: number; sku: string; sku_name: string;
  state: LineState; period: string; total: number; staged: number; dispatched: number; pending: number;
  staged_here: number; staged_other: number;
  /** 承重墙①：已组单（含草稿）/ 已下单 —— 只展示，不拦排货（CA-D10） */
  confirmed: number; ordered: number;
  /** 关联已下单采购单同货号行项的 已入库 / 待到货（不摊回记录，C4）；null = 读不到 */
  entered: number | null; arriving: number | null;
  purchase_orders: LinkedPo[]; stores: DispatchStore[];
}
export interface UnplannedSku {
  sku: string; sku_name: string | null;
  stores: { sid: Sid; seller_name: string; market: string; has_fba: boolean; mskus: DispatchMsku[] }[];
}
export interface StockRow {
  sku: string; wid: number;
  /** "shared" = 共享桶；其余键 = 店铺桶（CA-D8） */
  buckets: Record<string, number>;
  committed: Record<string, number>;
  /** 每店还能再承诺多少；负数 = 已超 */
  available: Record<string, number>;
  /** CA-D12：api = 领星现读；ch = 每日采集（as_of 是那批的采集时刻） */
  source: 'api' | 'ch'; as_of: string | null;
}

export interface LiveReadResult {
  sku: string;
  stock: { read_id: number; read_at: string; buckets: { wid: number; buckets: Record<string, number> }[];
    dropped: Record<string, { rows: number; units: number }> } | { error: Record<string, unknown> & { target: string } };
  purchase_orders: { po_id: PoId; order_sn: string; outcome: string; cause: string | null }[];
}
export interface DispatchCell {
  arrangement_id: number; line_id: number | null; sku: string; seller_sku: string; sid: Sid; from_wid: number;
  channel: DispatchChannel; dest_wid: number | null; dest_sid: Sid | null; units: number;
}
export interface StockError { target: string; cause: Record<string, unknown>; rejected?: unknown[] }
export interface SheetGrid {
  sheet: { sheet_id: number; state: SheetState; watermark: number; plan_id: PlanId | null; plan_title: string | null; route: DispatchRoute | null };
  stock_as_of: string | null; stock_rejected: unknown[];
  stock_error: StockError | null; po_progress_error: StockError | null;
  warehouses: { local: DispatchWarehouse[]; oversea: DispatchWarehouse[] };
  demand: DemandRow[]; unplanned: UnplannedSku[];
  /** null = 本地库存读不到（看 stock_error），不是「没有库存」 */
  stock: StockRow[] | null;
  cells: DispatchCell[];
  excluded: {
    lines_not_dispatchable: number;
    seller_not_in_mirror: { line_id: number; sid: Sid }[];
    msku_missing: { line_id: number; sid: Sid }[];
    stock: Record<string, { rows: number; units: number }> | null;
  };
}
export interface CellTarget {
  channel: DispatchChannel; dest_wid: number | null; dest_sid: Sid | null; from_wid: number;
}
export interface CellPut extends CellTarget {
  line_id: number | null; seller_sku: string; sid: Sid; units: number | null;
  /** 改目的地 / 来源仓：同一事务撤旧格 */
  replaces?: CellTarget;
}
export interface CellResult {
  cell: Omit<DispatchCell, 'arrangement_id'>;
  consignments_diff: { created: [string, string | number][]; removed: [string, string | number][]; kept: [string, string | number][] };
  orphan_packing: [string, string, number][];
  watermark: number;
  warnings: ({ code: 'over_available'; sku: string; from_wid: number; sid: Sid; available: number }
    | ({ code: 'stock_unavailable' } & StockError))[];
}
export interface SheetPreflight { sheet_id: number; ok: boolean; gates: DispatchGate[]; stock_as_of: string | null }
export interface SheetConfirmResult { sheet_id: number; state: SheetState; gates: DispatchGate[]; gate_run: number }
