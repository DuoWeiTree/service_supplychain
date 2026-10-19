import { ApiError, type SupplyChainApi } from './client';
import { getActor } from '../shell/actorStore';
import type {
  CatalogResult, RecentCatalogResult, LeadTime, LeadTimeResult, PlanList, PlanSummary, PutDemandResult,
  PutPurchaseResult, Seller,
} from './types';

interface HttpOptions { base: string; timeoutMs: number }

/** 读回要等后端打完领星：最坏 = 取 token + 查单，两次各受后端 timeout_s=30（shared/config.py，V6）所限，再留 5 s。
 *  ★ 设计取值 · 未实测上限；比后端短就会出现「前端放弃了、后端其实读到了」。领星这个接口实测 1.6~10 s */
export const READ_BACK_TIMEOUT_MS = 65_000;

/** 提交领星：每张备货单最坏 = 查重 + 建 + 再查重，各受后端 30 s 所限，一次提交可能好几张。
 *  ★ 设计取值 · 未实测。前端放弃 ≠ 后端停了：超时只能说「结果未知，刷新看」 */
export const SUBMIT_TIMEOUT_MS = 300_000;

/** ★ 三问日志：打的谁（method+path）· 多久（ms）· 怎么失败的（status+error 或 cause.code）。
 *  缺一个，下次就得重新复现一遍。 */
function logFail(call: string, ms: number, extra: Record<string, unknown>): void {
  console.error('[api]', JSON.stringify({ call, ms, ...extra }));
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  // ★ 未声明查询参数后端一律 400 ⇒ undefined 的不拼，也不发空串
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined);
  return pairs.length === 0 ? '' : `?${pairs.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')}`;
}

/** 建连或读体阶段的失败 → ApiError(0)。phase 写进日志：读体失败说明请求已经到了后端 */
function transportError(call_: string, ms: number, e: unknown, phase: 'connect' | 'body', timeoutMs: number): ApiError {
  // ★ 「超时」与「连不上」签名互斥、处置相反：前者调大超时有用，后者一行都不会生效
  if (e instanceof DOMException && e.name === 'TimeoutError') {
    logFail(call_, ms, { kind: 'TimeoutError', phase, timeout_ms: timeoutMs });
    return new ApiError(0, 'timeout', `${timeoutMs}ms 内没有响应`, { timeout_ms: timeoutMs, phase });
  }
  const cause = (e as { cause?: { code?: string; errors?: { code?: string }[] } }).cause;
  const code = cause?.code ?? cause?.errors?.[0]?.code ?? 'unknown';
  logFail(call_, ms, { kind: (e as Error).name, phase, cause_code: code, message: (e as Error).message });
  return new ApiError(0, 'network', `连不上（${code}）`, { cause_code: code, phase });
}

export function createHttpApi(opt: HttpOptions): SupplyChainApi {
  async function call<T>(method: string, path: string, body?: unknown, timeoutMs: number = opt.timeoutMs): Promise<T> {
    const url = `${opt.base}${path}`;
    const call_ = `${method} ${url}`;
    const started = Date.now();
    let res: Response;
    try {
      res = await fetch(url, {
        method,
        signal: AbortSignal.timeout(timeoutMs),
        headers: { 'content-type': 'application/json', 'x-actor': getActor() },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch (e) {
      throw transportError(call_, Date.now() - started, e, 'connect', timeoutMs);
    }

    const ms = Date.now() - started;
    if (ms >= 2000) console.warn('[api]', JSON.stringify({ call: call_, ms, status: res.status, slow: true }));

    // ★ 读体失败时请求已到后端：必须和建连失败一样落成 status 0（结果未知），不能漏成裸异常
    let text: string;
    try { text = await res.text(); }
    catch (e) { throw transportError(call_, Date.now() - started, e, 'body', timeoutMs); }
    let parsed: unknown = null;
    if (text !== '') {
      try { parsed = JSON.parse(text); }
      catch {
        // ★ 静默兜底是最坏的一种：「代理挂了」和「接口本身就错」必须长得不一样
        logFail(call_, ms, { status: res.status, kind: 'non_json_body', head: text.slice(0, 120) });
        throw new ApiError(res.status, 'bad_response', '返回的不是 JSON', { head: text.slice(0, 120) });
      }
    }
    if (!res.ok) {
      const { error, hint, ...fields } = (parsed ?? {}) as
        { error?: string; hint?: string } & Record<string, unknown>;
      logFail(call_, ms, { status: res.status, error: error ?? 'unknown' });
      throw new ApiError(res.status, error ?? 'unknown', hint ?? `${res.status}`, fields);
    }
    return parsed as T;
  }

  const plans = (q: Parameters<SupplyChainApi['listPlans']>[0]) =>
    call<PlanList>('GET', `/plans${qs({ state: q.state, owner: q.owner, archived: q.archived })}`);

  return {
    listPlans: plans,
    async getPlan(planId) {
      // ★ GET /grid 不带计划抬头；这里拆两次调用，页面不必知道
      // ★ I1 裁定：必须带 archived=true。后端 `GET /plans` 默认排除已归档
      //   （`api/ui/plans.py:69`），不带它的话 find 落空 → 前端抛 404「没有这张计划」，
      //   而后端专门把这两件事分开过（`deps.py:88`：404 是没有这张计划，
      //   409 是有、但它已经封存了）。运营打开一张归档计划，屏上说的是「没有这张计划」——
      //   而它就在那儿。带 true 时后端返回**全集**（含未归档），按 id 挑一张即可。
      const { plans: rows } = await plans({ archived: true });
      const hit = rows.find((p: PlanSummary) => p.plan_id === planId);
      if (!hit) throw new ApiError(404, 'plan_not_found', '没有这张计划', { plan_id: planId });
      return hit;
    },
    createPlan: (input) => call('POST', '/plans', input),
    dashboardPlans: () => call('GET', '/dashboard/plans'),
    dashboardUnsubmitted: () => call('GET', '/dashboard/unsubmitted'),
    listSellers: async () => (await call<{ sellers: Seller[] }>('GET', '/sellers')).sellers,

    getGrid: (planId) => call('GET', `/plans/${planId}/grid`),
    // ★ team-lead 09-22 裁定：显式命名的信封类型（PutDemandResult），不是从
    //   Awaited<ReturnType<SupplyChainApi['putDemand']>> 反推的部分形状 ——
    //   那种写法只是把接口签名"声称"的类型抄一遍，掩盖了后端曾经真的少发两个键
    //   （sku / effective_units）这件事。见 api/ui/plans.py 的 _demand_row()。
    // ★ M15：同一个模板里的每一段都过 encodeURIComponent —— sid / period 目前都是
    //   安全字符集，但「这一段为什么不用转义」的理由不写在代码里，下一个人只会照抄旁边那段
    putDemand: async (planId, sellerSku, sid, period, units) =>
      (await call<PutDemandResult>(
        'PUT', `/plans/${planId}/demand/${encodeURIComponent(sellerSku)}/${encodeURIComponent(sid)}/${encodeURIComponent(period)}`,
        { expected_units: units })).cell,
    // ★ M1：与 putDemand 同一个理由，用具名信封而不是从接口签名反推 —— 见 types.ts
    putPurchase: async (planId, sku, period, units) =>
      (await call<PutPurchaseResult>(
        'PUT', `/plans/${planId}/purchase/${encodeURIComponent(sku)}/${encodeURIComponent(period)}`,
        { planned_units: units })).cell,

    getLeadTime: () => call<LeadTime>('GET', '/lead-time'),
    putLeadTime: (input) => call<LeadTimeResult>('PUT', '/lead-time', input),

    searchCatalog: (q) => call<CatalogResult>('GET', `/catalog/skus${qs({ q: q.q, limit: q.limit })}`),
    recentCatalog: (q) => call<RecentCatalogResult>('GET', `/catalog/skus/recent${qs({ limit: q.limit })}`),
    claim: (planId, target) => call('POST', `/plans/${planId}/claims`, target),
    // ★ M15：sid 也要转义 —— putDemand 那一行补上了，这一行当时漏了，
    //   而「下一个人照抄旁边那段」正是这种漏活下去的方式
    releaseClaim: (planId, sellerSku, sid) =>
      call('DELETE', `/plans/${planId}/claims/${encodeURIComponent(sellerSku)}/${encodeURIComponent(sid)}`),
    removeSku: (planId, sku) => call('DELETE', `/plans/${planId}/skus/${encodeURIComponent(sku)}`),
    recomputeForecast: (planId) => call('POST', `/plans/${planId}/forecast/recompute`),

    submit: (planId) => call('POST', `/plans/${planId}/submit`),
    listRevs: (planId) => call('GET', `/plans/${planId}/revs`),
    setCurrentRev: (planId, rev) => call('POST', `/plans/${planId}/revs/${rev}/current`),
    diff: (planId, from, to) => call('GET', `/plans/${planId}/diff${qs({ from, to })}`),
    cancelRev: (planId, rev, reason) => call('POST', `/plans/${planId}/revs/${rev}/cancel`, { reason }),

    listPurchaseOrders: (state) => call('GET', `/purchase-orders${qs({ state })}`),
    getPurchaseOrder: (poId) => call('GET', `/purchase-orders/${encodeURIComponent(poId)}`),
    createPurchaseOrder: (body) => call('POST', '/purchase-orders', body),
    updatePurchaseOrder: (poId, body) => call('PUT', `/purchase-orders/${encodeURIComponent(poId)}`, body),
    async deletePurchaseOrder(poId) {
      await call<null>('DELETE', `/purchase-orders/${encodeURIComponent(poId)}`);
    },
    getPreflight: (poId) => call('GET', `/purchase-orders/${encodeURIComponent(poId)}/preflight`),
    placePurchaseOrder: (poId, confirmToken) =>
      call('POST', `/purchase-orders/${encodeURIComponent(poId)}/place`, { confirm_token: confirmToken }),
    getTimeline: (poId) => call('GET', `/purchase-orders/${encodeURIComponent(poId)}/timeline`),
    recheck: (poId) => call('POST', `/purchase-orders/${encodeURIComponent(poId)}/recheck`),
    readBack: (poId) => call('POST', `/purchase-orders/${encodeURIComponent(poId)}/read-back`, undefined, READ_BACK_TIMEOUT_MS),
    registerExisting: (poId, orderSn) =>
      call('POST', `/purchase-orders/${encodeURIComponent(poId)}/register-existing`, { order_sn: orderSn }),
    confirmAbsent: (poId, reason) =>
      call('POST', `/purchase-orders/${encodeURIComponent(poId)}/confirm-absent`, { reason }),
    acknowledgeMismatch: (poId, reason) =>
      call('POST', `/purchase-orders/${encodeURIComponent(poId)}/acknowledge-mismatch`, { reason }),
    listAttention: () => call('GET', '/attention'),
    getPurchaseOptions: () => call('GET', '/purchase-options'),
    getPurchaseChecks: () => call('GET', '/checks/purchase'),
    listCandidateLines: (params) => {
      const planIds = params.plan_ids && params.plan_ids.length > 0 ? params.plan_ids.join(',') : undefined;
      return call('GET', `/plan-lines${qs({ plan_ids: planIds, sku: params.sku, orderable: 1 })}`);
    },
    getLineTrace: (lineId) => call('GET', `/plan-lines/${encodeURIComponent(lineId)}/trace`),
    listPurchasePlans: () => call('GET', '/purchase/plans'),

    listSheets: (state) => call('GET', `/sheets${qs({ state })}`),
    listDispatchPlans: () => call('GET', '/dispatch/plans'),
    createSheet: (planId, route) => call('POST', '/sheets', { plan_id: planId, route }),
    getSheet: (sheetId) => call('GET', `/sheets/${encodeURIComponent(sheetId)}`),
    getSheetGrid: (sheetId) => call('GET', `/sheets/${encodeURIComponent(sheetId)}/grid`),
    putSheetCell: (sheetId, cell) => call('PUT', `/sheets/${encodeURIComponent(sheetId)}/cells`, cell),
    putSheetPacking: (sheetId, k, box) =>
      call('PUT', `/sheets/${encodeURIComponent(sheetId)}/packing/${encodeURIComponent(k.seller_sku)}/${encodeURIComponent(k.sid)}/${encodeURIComponent(k.from_wid)}`, box),
    sheetPreflight: (sheetId) => call('GET', `/sheets/${encodeURIComponent(sheetId)}/preflight`),
    confirmSheet: (sheetId, seen) => call('POST', `/sheets/${encodeURIComponent(sheetId)}/confirm`, { seen_watermark: seen }),
    liveRead: (sheetId, sku) => call('POST', `/sheets/${encodeURIComponent(sheetId)}/live-read`, { sku }, READ_BACK_TIMEOUT_MS),
    cancelSheet: (sheetId, reason) => call('POST', `/sheets/${encodeURIComponent(sheetId)}/cancel`, { reason }, SUBMIT_TIMEOUT_MS),
    receiptSheet: (sheetId) => call('POST', `/sheets/${encodeURIComponent(sheetId)}/receipt`, undefined, SUBMIT_TIMEOUT_MS),
    checkSkuMatch: (wid, sku) => call('GET', `/oversea-sku-match${qs({ wid, sku })}`, undefined, READ_BACK_TIMEOUT_MS),
    reopenSheet: (sheetId, reason) => call('POST', `/sheets/${encodeURIComponent(sheetId)}/reopen`, { reason }),
    listLogisticsChannels: () => call('GET', '/logistics-channels', undefined, READ_BACK_TIMEOUT_MS),
    refreshLogisticsChannels: () => call('POST', '/logistics-channels/refresh', undefined, READ_BACK_TIMEOUT_MS),
    setSheetLogistics: (sheetId, destWid, logisticsId) =>
      call('PUT', `/sheets/${encodeURIComponent(sheetId)}/logistics/${encodeURIComponent(destWid)}`, { logistics_id: logisticsId }),
    // ★ 预览要现查领星（海外仓三方 sku 配对实测 7.5~16 s，FBA listing）：全局 8 s 会把慢当成失败
    getSubmission: (sheetId) => call('GET', `/sheets/${encodeURIComponent(sheetId)}/submission`, undefined, READ_BACK_TIMEOUT_MS),
    submitSheet: (sheetId, token) =>
      call('POST', `/sheets/${encodeURIComponent(sheetId)}/submit`, { confirm_token: token }, SUBMIT_TIMEOUT_MS),
    verifyExtRef: (sheetId, aid) =>
      call('POST', `/sheets/${encodeURIComponent(sheetId)}/ext-refs/verify`, { attention_id: aid }, READ_BACK_TIMEOUT_MS),
    registerExtRef: (sheetId, aid, extNo) =>
      call('POST', `/sheets/${encodeURIComponent(sheetId)}/ext-refs/register`, { attention_id: aid, ext_no: extNo }, READ_BACK_TIMEOUT_MS),
    confirmExtAbsent: (sheetId, aid, reason) =>
      call('POST', `/sheets/${encodeURIComponent(sheetId)}/ext-refs/confirm-absent`, { attention_id: aid, reason }, READ_BACK_TIMEOUT_MS),
  };
}
