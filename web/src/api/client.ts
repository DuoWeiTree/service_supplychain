import type {
  CancelRevResult, CatalogQuery, CatalogResult, RecentCatalogResult, ClaimResult, ClaimTarget, CreatePlanInput,
  DashboardPlans, DemandCell, GridResponse, LeadTime, LeadTimeInput, LeadTimeResult,
  ListPlansQuery, Period, PlanDiff, PlanId, CellsBatch, PutCellsResult,
  PlanList, PlanSummary, PurchaseCell, RecomputeResult, ReleaseResult, RemoveSkuResult, RevList, Seller, Sid,
  SubmitResult, UnsubmittedBoard,
  AcknowledgeResult, AttentionQueue, CandidateLineQuery, CandidateLines, ConfirmAbsentResult, LineTrace, PurchasePlans,
  PlaceResult, PoBody, PoId, PoList, PoState, PoTimeline, PreflightResult, PurchaseChecks,
  PurchaseOptions, PurchaseOrder, ReadBackResult, RecheckResult, RegisterResult,
  CellPut, CellResult, DispatchPlans, LiveReadResult, PackingBox, PackingRow, SheetConfirmResult, SheetCreated, SheetDetail, SheetGrid, SheetList,
  SheetPreflight, SheetState, DispatchRoute, SheetReceiptResult, SkuMatchStatus, LogisticsChannels, OwsSubmission, OwsSubmitResult, SheetCancelResult,
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

/** 非 ApiError（建连失败等）也落成可显示的明细。★ cause.code 是分辨「超时」与「连不上」的唯一线索 */
export function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  const err = e as { name?: string; message?: string; cause?: { code?: string } };
  return new ApiError(0, err.name ?? 'Error', err.message ?? String(e),
    err.cause?.code ? { cause_code: err.cause.code } : {});
}

/** ★ sku-image 计划 Task 4：`<img src>` 必须指向**本仓**的代理端点，不是 PM 的地址——
 *  PM 凭证不出后端，界面拿到的图片 URL 只能是我们自己的 `/v1/skus/{sku}/image`。
 *  与 `src/api/index.ts` 读同一个 `VITE_API_BASE`（默认 `/v1`）。组 URL 与「这条路能不能真的连上」
 *  是两件事，后者是 Step 5（加载失败态）要处理的，不是这个函数的职责。 */
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
  /** UX-D14：粘贴 / 填充 / 批量清空一次写完；任一格不过整批不写，错误 `fields.cells` 逐格点名 */
  putCells(planId: PlanId, batch: CellsBatch): Promise<PutCellsResult>;

  searchCatalog(q: CatalogQuery): Promise<CatalogResult>;
  /** 以前计划里用过的货号（任一计划认领过，含已释放），最近认领在前 */
  recentCatalog(q: { limit?: number }): Promise<RecentCatalogResult>;
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

  // ───── 采购（spec §8.1）─────
  listPurchaseOrders(state?: PoState): Promise<PoList>;
  getPurchaseOrder(poId: PoId): Promise<PurchaseOrder>;
  createPurchaseOrder(body: PoBody): Promise<{ po_id: PoId }>;
  /** ★ draft_version 不对 ⇒ 409 stale_draft */
  updatePurchaseOrder(poId: PoId, body: PoBody & { draft_version: number }): Promise<{ po_id: PoId; draft_version: number }>;
  deletePurchaseOrder(poId: PoId): Promise<void>;
  getPreflight(poId: PoId): Promise<PreflightResult>;
  /** ⛔ 不可逆。★ 超时转人工，绝不自动重试（CLAUDE.md 出站 #2）——这里不重试，也不许调用方重试 */
  placePurchaseOrder(poId: PoId, confirmToken: string): Promise<PlaceResult>;
  getTimeline(poId: PoId): Promise<PoTimeline>;
  recheck(poId: PoId): Promise<RecheckResult>;
  /** 现查领星（只读）：落观察 + 落地核对。409 po_not_placed */
  readBack(poId: PoId): Promise<ReadBackResult>;
  registerExisting(poId: PoId, orderSn: string): Promise<RegisterResult>;
  /** 409 contradicts_evidence 的 fields.found_order_sns 点名领星里已有的单 */
  confirmAbsent(poId: PoId, reason: string): Promise<ConfirmAbsentResult>;
  acknowledgeMismatch(poId: PoId, reason: string): Promise<AcknowledgeResult>;
  listAttention(): Promise<AttentionQueue>;
  getPurchaseOptions(): Promise<PurchaseOptions>;
  getPurchaseChecks(): Promise<PurchaseChecks>;
  /** 固定带 orderable=1：只要还能组单的记录 */
  listCandidateLines(params: CandidateLineQuery): Promise<CandidateLines>;
  getLineTrace(lineId: number): Promise<LineTrace>;
  /** UX-D1/D2：采购首页待采购按销售计划分组 */
  listPurchasePlans(): Promise<PurchasePlans>;

  // ───── 排货（阶段 C-a）─────
  /** CA-D11 入口：未完成的销售计划 */
  listDispatchPlans(): Promise<DispatchPlans>;
  listSheets(state?: SheetState): Promise<SheetList>;
  /** ★ CA-D13：建表就定去向；同计划同去向已有草稿 → 409 draft_exists（带 sheet_id） */
  createSheet(planId: PlanId, route: DispatchRoute): Promise<SheetCreated>;
  getSheet(sheetId: number): Promise<SheetDetail>;
  getSheetGrid(sheetId: number): Promise<SheetGrid>;
  /** ★ 唯一的安排原语：units 0 / null = 撤回。同事务重派生排货单 */
  putSheetCell(sheetId: number, cell: CellPut): Promise<CellResult>;
  putSheetPacking(sheetId: number, key: { seller_sku: string; sid: string; from_wid: number }, box: PackingBox): Promise<PackingRow>;
  sheetPreflight(sheetId: number): Promise<SheetPreflight>;
  /** 409 confirm_blocked 的 fields.gates 列出全部六闸；503 stock_unavailable */
  confirmSheet(sheetId: number, seenWatermark: number): Promise<SheetConfirmResult>;
  /** CA-D12「现查领星」：一个货号的本地库存 + 关联已下单采购单的读回 */
  liveRead(sheetId: number, sku: string): Promise<LiveReadResult>;
  /** CB-D6：领星里建过的备货单先删；任一已过待发货 → 409 ows_not_cancellable，整表不取消 */
  cancelSheet(sheetId: number, reason: string): Promise<SheetCancelResult>;
  /** ★ CA-D14：已确认 → 草稿；有单发到过领星 → 409 already_submitted（只能取消） */
  /** 现查领星状态（回执，spec 2026-10-13）；与提交 / 取消互斥 → 409 sheet_busy */
  receiptSheet(sheetId: number): Promise<SheetReceiptResult>;
  /** 选目的仓时现查：这个货号在这个第三方海外仓配没配对三方 sku（503 = 领星读不到） */
  checkSkuMatch(wid: number, sku: string): Promise<SkuMatchStatus>;
  reopenSheet(sheetId: number, reason: string): Promise<{ sheet_id: number; state: SheetState; from: SheetState }>;

  // ───── 提交领星备货单（C-b）─────
  listLogisticsChannels(): Promise<LogisticsChannels>;
  refreshLogisticsChannels(): Promise<{ channels: LogisticsChannels['channels'] }>;
  setSheetLogistics(sheetId: number, destWid: number, logisticsId: number): Promise<{ dest_wid: number; logistics_id: number; channel_name: string }>;
  getSubmission(sheetId: number): Promise<OwsSubmission>;
  /** ⛔ 出站：逐张建领星备货单。出错就停在那一张 */
  submitSheet(sheetId: number, confirmToken: string): Promise<OwsSubmitResult>;
  verifyExtRef(sheetId: number, attentionId: number): Promise<{ attention_id: number; ext_no: string; dispatched: number[] }>;
  registerExtRef(sheetId: number, attentionId: number, extNo: string): Promise<{ attention_id: number; ext_no: string; dispatched: number[] }>;
  confirmExtAbsent(sheetId: number, attentionId: number, reason: string): Promise<{ attention_id: number; closed: string }>;
}
