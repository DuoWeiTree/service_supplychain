// ★ 逐条对应后端计划的返回体。字段名、类型、null 语义都不许自己改 ——
//   mock 与真 API 对不上，判据⑥ 直接挂，而界面是照这份形状先写完的。
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
}

/** `PUT .../demand/...` 的信封。★ team-lead 09-22 裁定：回的是 grid() 里 demand[] 同一格
 *  的完整九键（`api/ui/plans.py` 的 `_demand_row()`），不是它的子集 —— 曾经漏过
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

/** ★ `inbound` 为什么是 null。阶段 A **恒定**是这一个值 —— 它不说明 closing 的任何事 */
export type InboundReason = 'no_seller_attribution';

/** ★ `closing` 为什么是 null。两种成因处置不同，**不许合成一个裸 null** */
export type ClosingReason = null | 'not_applicable' | 'unknown_demand';

export interface InventoryBasis {
  source: 'ch';
  as_of: string;
  /** ★ 恒 false 且必须显式返回 —— 屏上标「未计本计划采购」 */
  includes_plan_purchase: false;
  /** 这一格用掉的期望销量（= 该店该货号各 msku 生效值之和）。★ 与前端自己算的那份互为证人 */
  demand: number | null;
  /** ★ 恒 'no_seller_attribution' —— 只解释 inbound，不解释 closing */
  reason: InboundReason;
  /** ★ closing 的成因，与 reason 分开的那一个 */
  closing_reason: ClosingReason;
  /** 该货号该月的在途总量。★ 放在这里是因为它**不属于这一格**（没有店铺归属），只展示不分摊 */
  sku_level_in_transit: number | null;
  sources: { units: number; kind: string; ref: string }[];
}

/** ★ 可售库存的身份是 **[店铺, 货号]**（`02` §3.1a）—— 不是 msku，也不带任何分摊 */
export interface InventoryCell {
  sku: string;
  sid: Sid;
  period: Period;
  /** 该格期初：首月是在仓事实，其后是**上月期末**（★ 跨月是一条链，不是同一个快照） */
  onhand: number | null;
  /** ★ 阶段 A 恒 null —— 在途是货号级，按单店给全额也是分摊假设（C4 禁） */
  inbound: null;
  /** ★ = onhand − basis.demand。null 时看 basis.closing_reason */
  closing: number | null;
  basis: InventoryBasis;
}

/** 货号级在途总量。★ 只展示，不分摊到任何店铺 */
export interface SkuPipelineRow {
  sku: string;
  period: Period;
  units: number | null;
  sources: { units: number; kind: string; ref: string }[];
  no_seller_attribution: true;
}

export interface GridResponse {
  plan_id: PlanId;
  /** ["2026-10","2026-11","2026-12"] */
  periods: Period[];
  demand: DemandCell[];
  purchase: PurchaseCell[];
  /** ★ 店铺 × 货号 × 月 */
  inventory: InventoryCell[];
  /** ★ 货号 × 月；画成一行只读，**不并进任何一个店铺的库存** */
  sku_pipeline: SkuPipelineRow[];
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
/** ★ 没有历史 ≠ 预估 0（`api/ui/plans.py:163-166`）：格子照建，`system_units` 留 null 并点名，
 *  不能让「拿不到历史」悄悄长得跟「预估出来是 0」一样。 */
export type NoHistoryReason = 'no_sales_history';
export interface NoHistoryEntry { seller_sku: string; sid: Sid; reason: NoHistoryReason }
export interface ClaimResult {
  claimed: { seller_sku: string; sid: Sid; sku: string };
  /** ★ team-lead 09-22 裁定：`api/ui/plans.py:183-185` 保证返回，两个键都不可选 ——
   *  认领这一下真种出了几个月的格子，brief 原文的 `ClaimResult` 漏了这两个 */
  seeded: { demand_cells: number; purchase_cells: number };
  no_history: NoHistoryEntry[];
}
// ★ 后端把丢掉的格逐条回给界面（含人填过的数），不是一个数；货号级采购格不删、只点名「搁浅」
export interface DroppedCell { period: string; expected_units: number | null }
export interface StrandedPurchaseCell { sku: string; period: string }
export interface ReleaseResult {
  released: { seller_sku: string; sid: Sid };
  dropped_cells: DroppedCell[];
  stranded_purchase_cells: StrandedPurchaseCell[];
}

/** S-14：值域已裁定，只有这两个 */
export type SkipReason = 'zero_purchase' | 'no_claimed_msku';
export interface SkippedCell { sku: string; period: Period; reason: SkipReason }

export interface SubmitResult {
  rev: number;
  /** ★ 铸出几条。字段名是 `lines`（team-lead 裁定；后端若残留 `minted` 以 `lines` 为准） */
  lines: number;
  skipped: SkippedCell[];
  /** ★ team-lead 09-22 裁定：两个都是保证的，不是可选 —— 契约块原文「后两个也是保证的，
   *  不是可选」（`docs/superpowers/plans/2026-09-22-stage-a-backend.md:64`），
   *  `api/ui/submit.py:114-117` 恒返回两者。brief 原文 Step 1 写成可选，是 brief 自己的错，
   *  只有 `CatalogResult.matched` 允许可选。 */
  in_flight: boolean;
  content_digest: string;
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

export interface ListPlansQuery {
  /** ★ 只发契约里声明过的参数 —— 未声明查询参数一律 400 */
  state?: LineState;
  owner?: string;
  archived?: boolean;
}
export interface CreatePlanInput { title: string; period_start: string; months: number }
export interface CatalogQuery { q?: string; limit?: number }
