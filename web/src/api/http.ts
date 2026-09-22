import { ApiError, type SupplyChainApi } from './client';
import { getActor } from '../shell/actorStore';
import type {
  CatalogResult, PlanList, PlanSummary, PutDemandResult, PutPurchaseResult, Seller,
} from './types';

interface HttpOptions { base: string; timeoutMs: number }

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

export function createHttpApi(opt: HttpOptions): SupplyChainApi {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${opt.base}${path}`;
    const call_ = `${method} ${url}`;
    const started = Date.now();
    let res: Response;
    try {
      res = await fetch(url, {
        method,
        signal: AbortSignal.timeout(opt.timeoutMs),
        headers: { 'content-type': 'application/json', 'x-actor': getActor() },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch (e) {
      const ms = Date.now() - started;
      // ★ 「超时」与「连不上」签名互斥、处置相反：前者调大超时有用，后者一行都不会生效
      if (e instanceof DOMException && e.name === 'TimeoutError') {
        logFail(call_, ms, { kind: 'TimeoutError', timeout_ms: opt.timeoutMs });
        throw new ApiError(0, 'timeout', `${opt.timeoutMs}ms 内没有响应`, { timeout_ms: opt.timeoutMs });
      }
      const cause = (e as { cause?: { code?: string; errors?: { code?: string }[] } }).cause;
      const code = cause?.code ?? cause?.errors?.[0]?.code ?? 'unknown';
      logFail(call_, ms, { kind: (e as Error).name, cause_code: code, message: (e as Error).message });
      throw new ApiError(0, 'network', `连不上（${code}）`, { cause_code: code });
    }

    const ms = Date.now() - started;
    if (ms >= 2000) console.warn('[api]', JSON.stringify({ call: call_, ms, status: res.status, slow: true }));

    const text = await res.text();
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

    searchCatalog: (q) => call<CatalogResult>('GET', `/catalog/skus${qs({ q: q.q, limit: q.limit })}`),
    claim: (planId, target) => call('POST', `/plans/${planId}/claims`, target),
    releaseClaim: (planId, sellerSku, sid) =>
      call('DELETE', `/plans/${planId}/claims/${encodeURIComponent(sellerSku)}/${sid}`),

    submit: (planId) => call('POST', `/plans/${planId}/submit`),
    listRevs: (planId) => call('GET', `/plans/${planId}/revs`),
    setCurrentRev: (planId, rev) => call('POST', `/plans/${planId}/revs/${rev}/current`),
    diff: (planId, from, to) => call('GET', `/plans/${planId}/diff${qs({ from, to })}`),
    cancelRev: (planId, rev, reason) => call('POST', `/plans/${planId}/revs/${rev}/cancel`, { reason }),
  };
}
