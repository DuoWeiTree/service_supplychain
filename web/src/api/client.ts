import type {
  CancelRevResult, CatalogQuery, CatalogResult, ClaimResult, ClaimTarget, CreatePlanInput,
  DashboardPlans, DemandCell, GridResponse, LeadTime, LeadTimeInput, LeadTimeResult,
  ListPlansQuery, Period, PlanDiff, PlanId,
  PlanList, PlanSummary, PurchaseCell, RecomputeResult, ReleaseResult, RemoveSkuResult, RevList, Seller, Sid,
  SubmitResult, UnsubmittedBoard,
} from './types';

/** ★ 后端的错误形状是 `{error, hint, …点名字段平铺在顶层}`（team-lead 2026-09-22 裁定）。
 *  `08` §0 写的 `{code, message, detail}` 已被这一条覆盖 —— 两份形状只能有一份生效。 */
export class ApiError extends Error {
  readonly status: number;
  readonly error: string;
  readonly hint: string;
  /** 顶层除 error/hint 外的全部字段，就是「点名是哪几行」的那部分 */
  readonly fields: Record<string, unknown>;
  constructor(status: number, error: string, hint: string, fields: Record<string, unknown> = {}) {
    super(`${status} ${error}: ${hint}`);
    this.name = 'ApiError';
    this.status = status;
    this.error = error;
    this.hint = hint;
    this.fields = fields;
  }
}

/** ★ sku-image 计划 Task 4：`<img src>` 必须指向**本仓**的代理端点，不是 PM 的地址——
 *  PM 凭证不出后端，界面拿到的图片 URL 只能是我们自己的 `/v1/skus/{sku}/image`。
 *  与 `src/api/index.ts` 读同一个 `VITE_API_BASE`（默认 `/v1`）：`mock` 数据源下这个端点
 *  实际不存在，但字符串本身按同一条规则拼——组 URL 与「这条路能不能真的连上」是两件事，
 *  后者是 Step 5（加载失败态）要处理的，不是这个函数的职责。 */
export function skuImageUrl(sku: string): string {
  const base = import.meta.env.VITE_API_BASE ?? '/v1';
  return `${base}/skus/${encodeURIComponent(sku)}/image`;
}

export interface SupplyChainApi {
  listPlans(q: ListPlansQuery): Promise<PlanList>;
  /** `GET /grid` 不带计划抬头 ⇒ 从列表里取。封在数据层，页面不必知道要拉两次 */
  getPlan(planId: PlanId): Promise<PlanSummary>;
  createPlan(input: CreatePlanInput): Promise<{ plan_id: PlanId }>;
  dashboardPlans(): Promise<DashboardPlans>;
  dashboardUnsubmitted(): Promise<UnsubmittedBoard>;
  listSellers(): Promise<Seller[]>;

  getGrid(planId: PlanId): Promise<GridResponse>;
  /** ★ units 可以是 null —— 空 = 未知 */
  putDemand(planId: PlanId, sellerSku: string, sid: Sid, period: Period, units: number | null): Promise<DemandCell>;
  putPurchase(planId: PlanId, sku: string, period: Period, units: number | null): Promise<PurchaseCell>;

  searchCatalog(q: CatalogQuery): Promise<CatalogResult>;
  /** ★ 一次一个 msku（后端只有单条端点）；页面循环并逐条收集 409 */
  claim(planId: PlanId, target: ClaimTarget): Promise<ClaimResult>;
  releaseClaim(planId: PlanId, sellerSku: string, sid: Sid): Promise<ReleaseResult>;
  /** 移出货号：一个调用、一个事务（逐个 release 中途失败会留下半个货号） */
  removeSku(planId: PlanId, sku: string): Promise<RemoveSkuResult>;
  /** task-23：已认领的格子重新算一次预测销量——换源之后旧认领的格子不会自己回头算，
   *  绝不碰人填的 expected_units。一个事务：取数中途失败一格都不落。 */
  recomputeForecast(planId: PlanId): Promise<RecomputeResult>;

  /** 14 B12：全局配置，不带 planId */
  getLeadTime(): Promise<LeadTime>;
  putLeadTime(input: LeadTimeInput): Promise<LeadTimeResult>;

  submit(planId: PlanId): Promise<SubmitResult>;
  listRevs(planId: PlanId): Promise<RevList>;
  setCurrentRev(planId: PlanId, rev: number): Promise<{ current_rev: number }>;
  diff(planId: PlanId, from: number, to: number): Promise<PlanDiff>;
  cancelRev(planId: PlanId, rev: number, reason: string): Promise<CancelRevResult>;
}
