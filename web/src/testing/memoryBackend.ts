/** 测试专用的内存后端（PB-D17：产品前端没有假数据源，只有测试 import 它）。
 *  ★ 预测块是 fixture 里的固定样本，编辑后不重算 —— 重算是后端 forecast/ 的活，照抄一份迟早分叉；
 *    要测编辑之后的屏，用 stubBackend 给出后端的新 grid。
 *  ★ 下单与「转人工」不模拟领星，调用即抛（见 notSimulated）。 */
import { ApiError, type SupplyChainApi } from '../api/client';
import type {
  CatalogResult, CountKey, DroppedPurchaseCell, GridResponse, LeadTime,
  LeadTimeResult, NoHistoryEntry, PlanSummary, RevList, Seller, Sid, SkippedCell, SubmitResult, SystemBasisNoSource,
  CandidateLine, LineState, PoBody, PoId, PoLine, PoSummary, PurchaseOptions, PurchaseOrder,
  PurchaseChecks, PreflightGate,
} from '../api/types';
import plansFixture from './fixtures/plans.json';
import gridFixture from './fixtures/grid-1.json';
import catalogFixture from './fixtures/catalog.json';
import revsFixture from './fixtures/revs-1.json';
import sellersFixture from './fixtures/sellers.json';
import purchaseFixture from './fixtures/purchase.json';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/** 同 rules/purchase.py::seller_caps：最大余数法，余数同分按 seller_id 升序。
 *  返回 null = 无法定上限（任一店 unknown / 总期望为 0），对应后端 NoSellerBasis */
function sellerCaps(total: number, dbs: CandidateLine['demand_by_seller']): Record<string, number> | null {
  const ids = Object.keys(dbs);
  const exp: Record<string, number> = {};
  for (const id of ids) {
    const v = dbs[id];
    if (!v || v.basis === 'unknown' || typeof v.units !== 'number') return null;
    exp[id] = v.units;
  }
  const sum = ids.reduce((a, id) => a + exp[id]!, 0);
  if (sum === 0) return null;
  const caps: Record<string, number> = {};
  for (const id of ids) caps[id] = Math.floor((total * exp[id]!) / sum);
  const extra = total - ids.reduce((a, id) => a + caps[id]!, 0);
  const order = [...ids].sort((a, b) => (total * exp[b]! % sum) - (total * exp[a]! % sum) || a.localeCompare(b));
  for (const id of order.slice(0, extra)) caps[id]! += 1;
  return caps;
}

const ORDERABLE: LineState[] = ['已提交', '已确认'];

export function createMemoryBackend(): SupplyChainApi {
  // ★ fixture 里只有 plans：`excluded.archived` 是**按这次查询现算**的（见 listPlans），
  //   冻一个常量进去就会在加了归档计划之后继续报旧数，而那正是它要挡的事
  const list = { plans: clone(plansFixture.plans) as PlanSummary[] };
  const grids = new Map<number, GridResponse>([[1, clone(gridFixture) as GridResponse]]);
  const catalog = clone(catalogFixture) as CatalogResult;
  const revs = new Map<number, RevList>([[2, clone(revsFixture) as RevList]]);
  const sellers = (clone(sellersFixture) as { sellers: Seller[] }).sellers;
  const inFlight = new Map<number, number | null>([[1, null], [2, 2], [3, null], [4, null]]);
  /** 每张计划当前的活记录，键 `${sku}/${period}` —— 再提交按它分保留 / 替换 / 新增 / 去掉 */
  type LiveLine = { line_id: number; sku: string; period: string; total_units: number };
  const liveLines = new Map<number, Map<string, LiveLine>>();
  let nextLineId = 1001;
  // ★ 14 B12：全局配置，与 grid-1.json 的 params 出厂值一致（30/60/15）
  let lead: LeadTime = {
    route: 'oversea_sea', make_days: 30, ship_days: 60, anchor_day: 15,
    valid_from: { making: '2026-01-01', shipping: '2026-01-01' },
  };

  // ★ 采购单：只读 + 草稿增删改。下单与「转人工」处理动作调用即抛（见下方 notSimulated）
  type PoRow = PurchaseOrder & { created_at: string };
  const pos = new Map<PoId, PoRow>(
    (clone(purchaseFixture.purchase_orders) as unknown as PoRow[]).map((p) => {
      return [p.po_id, { ...p, attention: [], source_line_states: [], preflight: [], confirm_token: '' }];
    }));
  type PlanLineRow = Pick<CandidateLine, 'line_id' | 'plan_id' | 'rev' | 'sku' | 'period' | 'total_units' |
    'demand_at_submit' | 'demand_by_seller' | 'state' | 'confirmed_units' | 'ordered_units'>;
  const planLines = clone(purchaseFixture.lines) as unknown as PlanLineRow[];
  let nextPo = Math.max(0, ...pos.keys()) + 1;
  const poErr = (status: number, error: string, hint: string, fields: Record<string, unknown> = {}) =>
    new ApiError(status, error, hint, fields);
  /** 草稿占用额度（排除 exclude 自己）：未作废的所有单（草稿 + 已下单），同 repo.seller_used */
  const usedBy = (exclude?: PoId): Map<string, number> => {
    const m = new Map<string, number>();
    for (const p of pos.values()) {
      if (p.po_id === exclude || p.discarded_at) continue;
      for (const ln of p.lines) for (const sc of ln.sources) {
        const k = `${sc.line_id}|${sc.seller_id}`;
        m.set(k, (m.get(k) ?? 0) + sc.units);
      }
    }
    return m;
  };
  const candidate = (l: PlanLineRow): CandidateLine => {
    const used = usedBy();
    const caps = sellerCaps(l.total_units, l.demand_by_seller);
    return {
      ...l,
      remaining: l.total_units - l.confirmed_units,
      blocked: caps ? null : { reason: 'no_seller_basis', sellers: Object.keys(l.demand_by_seller) },
      sellers: Object.fromEntries(Object.entries(l.demand_by_seller).map(([sid, v]) => {
        const u = used.get(`${l.line_id}|${sid}`) ?? 0;
        return [sid, { expected: v?.units ?? null, cap: caps?.[sid] ?? null, used: u,
                       remaining: caps && sid in caps ? caps[sid]! - u : null }];
      })),
    };
  };
  const gate = (g: string, ok: boolean, detail: Record<string, unknown> = {}): PreflightGate => ({ gate: g, ok, detail });
  /** 同 rules/purchase.py::preflight 的前五类 + 逐行三类；sources_orderable 取现算的来源状态 */
  const preflight = (p: PoRow): PreflightGate[] => {
    const g: PreflightGate[] = [
      gate('supplier', !!p.supplier_id), gate('purchaser', !!p.purchaser_id), gate('opt_uid', !!p.opt_uid),
      gate('qc_type', [0, 1, 2].includes(p.qc_type), { got: p.qc_type }), gate('lines', p.lines.length > 0),
    ];
    for (const ln of p.lines) {
      g.push(gate('line_wid', !!ln.wid, { line_no: ln.line_no }));
      g.push(gate('line_price', Number(ln.price) >= 0, { line_no: ln.line_no }));
      g.push(gate('line_units', ln.units >= ln.sources_units, { line_no: ln.line_no, units: ln.units, sources: ln.sources_units }));
      g.push(gate('line_unplanned_reason', ln.unplanned_units <= 0 || !!(ln.unplanned_reason ?? '').trim(),
                  { line_no: ln.line_no, unplanned: Math.max(ln.unplanned_units, 0) }));
    }
    const offenders = p.source_line_states.filter((s) => !ORDERABLE.includes(s.state));
    g.push(gate('sources_orderable', offenders.length === 0, { offenders }));
    return g;
  };
  const withDerived = (p: PoRow): PurchaseOrder => {
    const ids = [...new Set(p.lines.flatMap((l) => l.sources.map((s) => s.line_id)))].sort((a, b) => a - b);
    const full: PoRow = { ...p, source_line_states: ids.map((id) => ({
      line_id: id, state: planLines.find((l) => l.line_id === id)?.state ?? '已提交' })) };
    const { created_at: _c, ...rest } = full;
    return clone({ ...rest, preflight: preflight(full),
                   confirm_token: `mock-${p.po_id}-v${p.draft_version}` } as PurchaseOrder);
  };
  const po = (id: PoId): PoRow => {
    const p = pos.get(id);
    if (!p) throw poErr(404, 'po_not_found', '采购单不存在', { po_id: id });
    return p;
  };
  const draft = (id: PoId): PoRow => {
    const p = po(id);
    if (p.discarded_at) throw poErr(409, 'po_discarded', '这张草稿已作废', { po_id: id });
    if (p.state !== '草稿') throw poErr(409, 'po_frozen', '采购单已下单，不可改', { po_id: id, order_sn: p.order_sn });
    return p;
  };
  /** 同 usecase/draft_po.py::_normalize + _check_quantities 里替身能复刻的闸 */
  const applyBody = (p: PoRow, body: PoBody): void => {
    const missing = (['supplier_id', 'purchaser_id', 'opt_uid', 'qc_type'] as const)
      .filter((k) => body[k] === undefined || body[k] === null || (body[k] as unknown) === '');
    if (missing.length) throw poErr(400, 'missing_required', '单头必填项缺失', { missing });
    if (!body.lines || body.lines.length === 0) throw poErr(400, 'missing_required', '至少一行明细', { missing: ['lines'] });
    const seen = new Set<string>();
    const lines: PoLine[] = body.lines.map((ln, i) => {
      const key = `${ln.sku}|${ln.wid}`;
      if (seen.has(key)) throw poErr(400, 'duplicate_line', '同一张单里同货号同仓只能一行', { sku: ln.sku, wid: ln.wid });
      seen.add(key);
      const srcUnits = ln.sources.reduce((a, s) => a + s.units, 0);
      if (ln.units > srcUnits && !(ln.unplanned_reason ?? '').trim())
        throw poErr(400, 'unplanned_reason_required', '超出计划来源的数量必须写理由', { sku: ln.sku, unplanned: ln.units - srcUnits });
      return { line_no: i + 1, sku: ln.sku, wid: ln.wid, units: ln.units, price: ln.price, expect_arrive_date: ln.expect_arrive_date ?? null,
               unplanned_reason: ln.unplanned_reason ?? null, lx_item_id: null, sources: clone(ln.sources),
               sources_units: srcUnits, unplanned_units: ln.units - srcUnits };
    });
    const want = new Map<string, number>();
    for (const ln of lines) for (const s of ln.sources) {
      const k = `${s.line_id}|${s.seller_id}`;
      want.set(k, (want.get(k) ?? 0) + s.units);
    }
    const used = usedBy(p.po_id);
    for (const id of [...new Set(lines.flatMap((l) => l.sources.map((s) => s.line_id)))].sort((a, b) => a - b)) {
      const pl = planLines.find((l) => l.line_id === id);
      if (!pl) throw poErr(404, 'line_not_found', '没有这条计划单记录', { line_id: id });
      if (!ORDERABLE.includes(pl.state))
        throw poErr(409, 'line_not_orderable', '只有 已提交/已确认 的记录能组单', { line_id: id, state: pl.state });
      const caps = sellerCaps(pl.total_units, pl.demand_by_seller);
      if (!caps) throw poErr(409, 'no_seller_basis', '期望销量为 0 或 unknown 的店无法定分店上限',
                             { line_id: id, sellers: Object.keys(pl.demand_by_seller) });
      const rows = [...want].filter(([k]) => k.startsWith(`${id}|`));
      const totalWant = rows.reduce((a, [, v]) => a + v, 0);
      const totalUsed = [...used].filter(([k]) => k.startsWith(`${id}|`)).reduce((a, [, v]) => a + v, 0);
      if (totalUsed + totalWant > pl.total_units)
        throw poErr(409, 'over_total', '超过记录总量', { line_id: id, total: pl.total_units, used: totalUsed, requested: totalWant });
      for (const [k, req] of rows) {
        const sid = k.split('|')[1]!;
        const cap = caps[sid] ?? 0;
        const u = used.get(k) ?? 0;
        if (u + req > cap)
          throw poErr(409, 'over_seller_cap', '超过该店上限（按期望销量占比）',
                      { line_id: id, seller_id: sid, cap, used: u, requested: req });
      }
    }
    Object.assign(p, { supplier_id: body.supplier_id, purchaser_id: body.purchaser_id, opt_uid: body.opt_uid,
                       qc_type: body.qc_type, wid: lines[0]?.wid ?? null, lines });
  };
  /** ★ 下单与四个「转人工」动作的结果由领星决定（建单 / 查重 / 登记要对领星核对），替身编一个结果就是编造外部事实。
   *  抛的不是 ApiError：它不冒充任何后端答复，用到它的测试应当直接挂掉，改用 stubBackend。 */
  const notSimulated = (what: string): never => {
    throw new Error(`memoryBackend 不模拟领星：${what} —— 要测这一屏，用 testing/backendStub 的 stubBackend 给出后端答复`);
  };

  const plan = (id: number): PlanSummary => {
    const p = list.plans.find((x) => x.plan_id === id);
    if (!p) throw new ApiError(404, 'plan_not_found', '没有这张计划', { plan_id: id });
    return p;
  };
  /** ★ 归档后一律拒写（`api/ui/deps.py:82-95` 的 `ensure_writable`，后端每个写端点都挂着它）。
   *  替身没有它的话，归档计划上的认领/填数/提交在替身下全成功、在真 API 下全 409。
   *  ★ 与「计划不存在」分得开：404 是没有这张计划，409 是有、但它已经封存了。 */
  const writable = (id: number): PlanSummary => {
    const p = plan(id);
    if (p.archived_at !== null) {
      throw new ApiError(409, 'plan_archived', '计划已归档，不接受写入',
        { plan_id: id, archived_at: p.archived_at });
    }
    return p;
  };
  /** 候选集 = owner/state 圈定的那一批。★ `excluded.archived` 只能在候选集里数 ——
   *  在全库上数的话，按 owner 过滤时报的是别人计划的归档数（`api/ui/plans.py:83-86`）。 */
  const candidates = (q: { state?: string; owner?: string }) => list.plans.filter((p) =>
    (q.state === undefined || p.state === q.state) &&
    (q.owner === undefined || p.owner_actor === q.owner));
  /** ★ 两个看板都只数没归档的（`api/ui/dashboard.py:30`/`:57` 的 `archived_at IS NULL`）*/
  const live = () => list.plans.filter((p) => p.archived_at === null);
  const grid = (id: number): GridResponse => {
    const g = grids.get(id);
    if (!g) throw new ApiError(404, 'plan_not_found', '这张计划还没有网格', { plan_id: id });
    return g;
  };
  /** 一个 msku 的格子与它自己的预测行一起走；目录里还它自由（★ 释放不删目录行） */
  const dropMsku = (g: GridResponse, sellerSku: string, sid: Sid) => {
    const mine = (r: { seller_sku: string; sid: Sid }) => r.seller_sku === sellerSku && r.sid === sid;
    const cells = g.demand.filter(mine);
    g.demand = g.demand.filter((r) => !mine(r));
    g.forecast = g.forecast.filter((r) => !mine(r));
    g.forecast_msku = g.forecast_msku.filter((r) => !mine(r));
    g.unallocated = g.unallocated.filter((u) => !(u.scope === 'msku' && mine(u)));
    const m = catalog.items.flatMap((x) => x.mskus).find((x) => x.seller_sku === sellerSku && x.sid === sid);
    if (m) { m.selectable = true; m.claimed_by = null; }
    return cells;
  };
  /** ★ Task 22（业主裁定 2026-09-27）：与 api/ui/plans.py 同口径 —— 店里没 msku 了店铺行走；
   *  货号没 msku 了，采购格与该货号的每一个预测块一起走，删掉的采购格逐格回报。 */
  const settleSku = (g: GridResponse, sku: string, sids: Iterable<Sid>): DroppedPurchaseCell[] => {
    for (const sid of sids) {
      if (!g.demand.some((d) => d.sku === sku && d.sid === sid)) {
        g.forecast_store = g.forecast_store.filter((r) => !(r.sku === sku && r.sid === sid));
      }
    }
    if (g.demand.some((d) => d.sku === sku)) return [];
    const gone = g.purchase.filter((p) => p.sku === sku)
      .map((p) => ({ sku, period: p.period, planned_units: p.planned_units }))
      .sort((a, b) => a.period.localeCompare(b.period));
    const other = (r: { sku: string }) => r.sku !== sku;
    g.purchase = g.purchase.filter(other);
    g.forecast = g.forecast.filter(other);
    g.forecast_msku = g.forecast_msku.filter(other);
    g.forecast_store = g.forecast_store.filter(other);
    g.forecast_sku = g.forecast_sku.filter(other);
    g.beyond_horizon = g.beyond_horizon.filter(other);
    g.unallocated = g.unallocated.filter(other);
    return gone;
  };

  return {
    async listPlans(q) {
      // ★ `archived` 不给时后端**排除**已归档（`api/ui/plans.py:69` 的默认值 false），
      //   不是「不过滤」；给 true 时是**全给**（含未归档），不是「只给归档的」。
      const want = q.archived === true;
      const pool = candidates(q);
      const plans = clone(want ? pool : pool.filter((p) => p.archived_at === null));
      return { plans, excluded: { archived: want ? 0 : pool.filter((p) => p.archived_at !== null).length } };
    },
    async getPlan(planId) { return clone(plan(planId)); },
    async createPlan(input) {
      const plan_id = Math.max(...list.plans.map((p) => p.plan_id)) + 1;
      list.plans.push({
        plan_id, title: input.title, period_start: input.period_start, months: input.months,
        owner_actor: 'ops.zhang', archived_at: null, state: null, state_rev: null,
      });
      inFlight.set(plan_id, null);
      grids.set(plan_id, {
        plan_id, periods: [], demand: [], purchase: [],
        params: clone((gridFixture as GridResponse).params),
        forecast: [], forecast_msku: [], forecast_store: [], forecast_sku: [],
        beyond_horizon: [], unallocated: [],
      });
      return { plan_id };
    },
    async dashboardPlans() {
      // ★ 键的全集按 team-lead 09-22 裁定，照 api/ui/dashboard.py:18-31 的推导法算
      //   （派生桶 + plan_line_state_rank 按 rank 顺序 + 旁路终态已撤销），不手列 ——
      //   手列的宇宙会漏掉第 N+1 种状态，而这里从 list.plans 现算，fixture 改了状态
      //   计数也跟着对，不会像写死的数字那样悄悄脱节。
      const RANKED: readonly CountKey[] = ['已提交', '已确认', '已下单', '准备排货', '已排货', '已完结'];
      const counted: CountKey[] = ['进行中', '已提交未确认', ...RANKED, '已撤销'];
      const counts = Object.fromEntries(counted.map((k) => [k, 0])) as Record<CountKey, number>;
      let neverSubmittedExcluded = 0;
      for (const p of live()) {
        if (p.state === null) { neverSubmittedExcluded += 1; continue; }
        counts[p.state] += 1;
        // ★ 与后端一致：只要不是已完结/已撤销就算「进行中」，已提交额外再计一次「已提交未确认」
        if (p.state !== '已完结' && p.state !== '已撤销') counts['进行中'] += 1;
        if (p.state === '已提交') counts['已提交未确认'] += 1;
      }
      return {
        counts,
        // ★ 够不着的三个态由前端不渲染，不是接口抹成 0（S-20）
        scope_note: { unreachable_in_stage_a: ['已下单', '准备排货', '已排货'], never_submitted_excluded: neverSubmittedExcluded },
      };
    },
    async dashboardUnsubmitted() {
      // ★ 两栏都只看没归档的（`api/ui/dashboard.py:57`）—— 归档计划已经不在列表里了，
      //   再催人去提交它就是催一件做不到的事（后端会 409 plan_archived）
      return {
        never_submitted: live().filter((p) => p.state === null).map((p) => ({ plan_id: p.plan_id, title: p.title })),
        changed_since_submit: live().filter((p) => p.plan_id === 2)
          .map((p) => ({ plan_id: p.plan_id, title: p.title, since_rev: p.state_rev ?? 0 })),
      };
    },
    async listSellers() { return clone(sellers); },

    async getGrid(planId) { return clone(grid(planId)); },

    async getLeadTime() { return clone(lead); },
    // 改完把每张网格的 params 同步（界面靠它显示图注）；预测块不重算（见文件头）
    async putLeadTime(input) {
      const changed: LeadTimeResult['changed'] = [];
      const today = new Date().toISOString().slice(0, 10);
      if (input.make_days !== undefined && input.make_days !== lead.make_days) {
        changed.push({ segment: 'making', from: lead.make_days, to: input.make_days });
        lead = { ...lead, make_days: input.make_days, valid_from: { ...lead.valid_from, making: today } };
      }
      if (input.ship_days !== undefined && input.ship_days !== lead.ship_days) {
        changed.push({ segment: 'shipping', from: lead.ship_days, to: input.ship_days });
        lead = { ...lead, ship_days: input.ship_days, valid_from: { ...lead.valid_from, shipping: today } };
      }
      for (const g of grids.values()) {
        g.params = { ...g.params, make_days: lead.make_days, ship_days: lead.ship_days };
      }
      return { ...clone(lead), changed };
    },

    async putDemand(planId, sellerSku, sid, period, units) {
      writable(planId);
      const g = grid(planId);
      const cell = g.demand.find((d) => d.seller_sku === sellerSku && d.sid === sid && d.period === period);
      if (!cell) {
        throw new ApiError(404, 'cell_not_found', '这一格还没长出来（先认领这个 msku）',
          { plan_id: planId, seller_sku: sellerSku, sid, period });
      }
      if (units !== null && (!Number.isInteger(units) || units < 0)) {
        throw new ApiError(400, 'bad_units', 'expected_units 必须是 ≥0 的整数或 null',
          { field: 'expected_units', got: units });
      }
      cell.expected_units = units;
      cell.effective_units = units ?? cell.system_units;
      cell.basis = units !== null ? 'human' : cell.system_units !== null ? 'system' : 'unknown';
      return clone(cell);
    },

    async putPurchase(planId, sku, period, units) {
      writable(planId);
      const g = grid(planId);
      const cell = g.purchase.find((p) => p.sku === sku && p.period === period);
      if (!cell) {
        throw new ApiError(404, 'cell_not_found', '这一格还没长出来（先认领该货号下的 msku）',
          { plan_id: planId, sku, period });
      }
      if (units !== null && (!Number.isInteger(units) || units < 0)) {
        throw new ApiError(400, 'bad_units', 'planned_units 必须是 ≥0 的整数或 null',
          { field: 'planned_units', got: units });
      }
      cell.planned_units = units;
      return clone(cell);
    },

    async searchCatalog(q) {
      const limit = q.limit ?? catalog.limit;
      if (!q.q) return { need_query: true, truncated: false, limit, items: [], matched: 0 };
      const needle = q.q.toUpperCase();
      const items = catalog.items.filter((s) =>
        s.sku.toUpperCase().includes(needle) || s.name.includes(q.q!) ||
        s.mskus.some((m) => m.seller_sku.toUpperCase().includes(needle)));
      return { need_query: false, truncated: items.length > limit, limit, items: clone(items), matched: items.length };
    },

    async claim(planId, target) {
      writable(planId);
      const item = catalog.items.find((s) => s.mskus.some((m) => m.seller_sku === target.seller_sku && m.sid === target.sid));
      const msku = item?.mskus.find((m) => m.seller_sku === target.seller_sku && m.sid === target.sid);
      // ★ 与后端同码：api/ui/plans.py:131 用 unknown_msku，不是 msku_not_found
      if (!item || !msku) throw new ApiError(404, 'unknown_msku', '没有这个 msku', { ...target });
      // ★ 与后端同：本计划里已在认领中 → 幂等空操作（fix round 2 裁定）
      if (msku.claimed_by?.plan_id === planId) {
        return { claimed: { ...target, sku: item.sku }, already_claimed: true,
                 seeded: { demand_cells: 0, purchase_cells: 0 }, no_history: [] };
      }
      if (!msku.selectable && msku.claimed_by) {
        // ★ team-lead 09-22 裁定：认领 409 与目录端点的占用方不是同一个形状 ——
        //   这里嵌套在 claimed_by 里（api/ui/plans.py:153-156，backend 现已带 title），
        //   不是像目录那样把字段拍平在顶层。别再借用 ClaimHolder 那份拍平写法。
        throw new ApiError(409, 'msku_already_claimed', `${target.seller_sku} 已被占用`,
          { seller_sku: target.seller_sku, sid: target.sid,
            claimed_by: { plan_id: msku.claimed_by.plan_id, actor: msku.claimed_by.actor, title: msku.claimed_by.title } });
      }
      msku.selectable = false;
      msku.claimed_by = { plan_id: planId, title: plan(planId).title, actor: 'ops.zhang' };
      // ★ team-lead 09-22 裁定：种出的格子数与「没有销售历史」都要点名（api/ui/plans.py:163-186）。
      //   替身没有真的重新播种格子，用 plan 的 months 代表种出的月份数（与真实播种的行数一致）；
      //   no_history 从这张计划已有的 grid 里现查 —— 这个 msku 名下各月 system_units
      //   全为 null 才算「没历史」，不是拍脑袋写死的固定名单。
      const g = grids.get(planId);
      const mine = g?.demand.filter((d) => d.seller_sku === target.seller_sku && d.sid === target.sid) ?? [];
      // ★ 与后端同一个判据：认领时记进 system_basis 的「无销量取数源」是不适用，不是没历史
      const noSource = mine.map((d) => d.system_basis)
        .find((b): b is SystemBasisNoSource => b?.reason === 'not_applicable_no_sales_source');
      const noHistory: NoHistoryEntry[] = noSource
        ? [{ seller_sku: target.seller_sku, sid: target.sid, reason: 'not_applicable_no_sales_source', cause: noSource.cause }]
        : mine.length > 0 && mine.every((d) => d.system_units === null)
          ? [{ seller_sku: target.seller_sku, sid: target.sid, reason: 'no_sales_history' }]
          : [];
      const months = plan(planId).months;
      return {
        claimed: { ...target, sku: item.sku },
        seeded: { demand_cells: months, purchase_cells: months },
        no_history: noHistory,
        already_claimed: false,
      };
    },

    async releaseClaim(planId, sellerSku, sid) {
      writable(planId);
      const g = grid(planId);
      const cells = dropMsku(g, sellerSku, sid);
      // ★ 与后端同：货号从桥表认（这里是目录），不从格子认 —— 没有格子的 msku 也要判货号是否随之移出
      const sku = cells[0]?.sku ?? catalog.items.find((x) =>
        x.mskus.some((m) => m.seller_sku === sellerSku && m.sid === sid))?.sku;
      return {
        released: { seller_sku: sellerSku, sid },
        dropped_cells: cells.map((d) => ({ period: d.period, expected_units: d.expected_units })),
        dropped_purchase_cells: sku === undefined ? [] : settleSku(g, sku, [sid]),
      };
    },

    async removeSku(planId, sku) {
      writable(planId);
      const g = grid(planId);
      const keys = new Map(g.demand.filter((d) => d.sku === sku)
        .map((d) => [`${d.seller_sku}\u0000${d.sid}`, { seller_sku: d.seller_sku, sid: d.sid }]));
      const released = [...keys.values()].sort((a, b) =>
        a.seller_sku.localeCompare(b.seller_sku) || a.sid.localeCompare(b.sid));
      if (released.length === 0 && !g.purchase.some((p) => p.sku === sku)) {
        throw new ApiError(404, 'sku_not_in_plan', '这张计划里没有这个货号', { plan_id: planId, sku });
      }
      const dropped_cells = released.flatMap((m) => dropMsku(g, m.seller_sku, m.sid)
        .map((d) => ({ seller_sku: d.seller_sku, sid: d.sid, period: d.period, expected_units: d.expected_units })));
      return { sku, released, dropped_cells, dropped_purchase_cells: settleSku(g, sku, released.map((m) => m.sid)) };
    },

    // ★ task-23：替身不重跑逐日台账（见文件头）。这里如实报「没有一格变了」（unchanged 全数），
    //   只把「算不出速率」的 msku 按 claim() 同一个判据挑出来，不假装重算过。
    async recomputeForecast(planId) {
      writable(planId);
      const g = grid(planId);
      const keys = new Map(g.demand.map((d) => [`${d.seller_sku} ${d.sid}`,
        { seller_sku: d.seller_sku, sid: d.sid }]));
      const noRate: NoHistoryEntry[] = [];
      for (const { seller_sku, sid } of keys.values()) {
        const mine = g.demand.filter((d) => d.seller_sku === seller_sku && d.sid === sid);
        const noSource = mine.map((d) => d.system_basis)
          .find((b): b is SystemBasisNoSource => b?.reason === 'not_applicable_no_sales_source');
        if (noSource) {
          noRate.push({ seller_sku, sid, reason: 'not_applicable_no_sales_source', cause: noSource.cause });
        } else if (mine.every((d) => d.system_units === null)) {
          noRate.push({ seller_sku, sid, reason: 'no_sales_history' });
        }
      }
      return { recomputed: [], unchanged: g.demand.length, no_rate: noRate };
    },

    async submit(planId) {
      writable(planId);
      const g = grid(planId);
      const skipped: SkippedCell[] = [];
      const minted: { sku: string; period: string; total_units: number }[] = [];
      // ★ M2：判据的键照抄 `rules/submit.py` —— 后端是 `{c.sku for c in demand_cells}`，
      //   **只按货号**，不带月份。按月份多卡一道，会在「某个月没认领 msku」时
      //   报出后端根本不会报的 no_claimed_msku
      const skusWithClaim = new Set(g.demand.map((d) => d.sku));
      // ★ 后端按排序后的 purchase_cells 遍历、skipped 也排序输出（`rules/submit.py`）——
      //   顺序不同会让两种数据源的「逐条列出」一屏对不上
      const ordered = [...g.purchase].sort((a, b) =>
        a.sku.localeCompare(b.sku) || a.period.localeCompare(b.period));
      for (const pc of ordered) {
        if (!skusWithClaim.has(pc.sku)) {
          skipped.push({ sku: pc.sku, period: pc.period, reason: 'no_claimed_msku' }); continue;
        }
        // ★ R1：空 ≠ 0
        if (pc.planned_units === null) {
          skipped.push({ sku: pc.sku, period: pc.period, reason: 'empty_purchase' }); continue;
        }
        if (pc.planned_units === 0) {
          skipped.push({ sku: pc.sku, period: pc.period, reason: 'zero_purchase' }); continue;
        }
        minted.push({ sku: pc.sku, period: pc.period, total_units: pc.planned_units });
      }
      // ★ R2：按 (货号, 月) 对上一版的活记录。替身不追踪分店拆分与采购引用 ——
      //   split_frozen / left_completed / 409 lines_in_purchase 要测，用 stubBackend 给后端的形状
      const prev = liveLines.get(planId) ?? new Map<string, LiveLine>();
      const next = new Map<string, LiveLine>();
      const p = plan(planId);
      const rev = (p.state_rev ?? 0) + 1;
      const r: Pick<SubmitResult, 'kept' | 'replaced' | 'added' | 'removed'> = {
        kept: { count: 0, lines: [] }, replaced: { count: 0, lines: [] },
        added: { count: 0, lines: [] }, removed: { count: 0, lines: [] },
      };
      for (const m of minted) {
        const key = `${m.sku}/${m.period}`;
        const old = prev.get(key);
        if (old && old.total_units === m.total_units) {
          next.set(key, old);
          r.kept.lines.push({ sku: m.sku, period: m.period, line_id: old.line_id, state: '已提交', total_units: old.total_units });
          continue;
        }
        const line = { ...m, line_id: nextLineId++ };
        next.set(key, line);
        if (old) {
          r.replaced.lines.push({ sku: m.sku, period: m.period, old_line_id: old.line_id, new_line_id: line.line_id,
            total_units: { from: old.total_units, to: m.total_units }, demand_at_submit: { from: 0, to: 0 } });
        } else {
          r.added.lines.push({ sku: m.sku, period: m.period, line_id: line.line_id, total_units: m.total_units });
        }
      }
      for (const [key, old] of [...prev].sort(([a], [b]) => a.localeCompare(b))) {
        if (!next.has(key)) r.removed.lines.push({ sku: old.sku, period: old.period, line_id: old.line_id, total_units: old.total_units });
      }
      for (const k of ['kept', 'replaced', 'added', 'removed'] as const) r[k].count = r[k].lines.length;
      liveLines.set(planId, next);
      const lines = next.size;
      // ★ 空版本不占在流转位：占着的话这张计划从此再也提交不了
      const alive = lines > 0;
      p.state_rev = rev;
      // ★ M3：一条活记录都没有时后端木桶派生的 overall 是 NULL（即「未提交」）—— 不是「已撤销」
      p.state = alive ? '已提交' : null;
      inFlight.set(planId, alive ? rev : null);
      // ★ S-4：任一时刻只有一版在流转 —— 旧版在同一次再提交里让位
      const l = revs.get(planId) ?? { revs: [], in_flight_rev: null, current_rev: null };
      l.revs.forEach((x) => { x.in_flight = false; x.is_current = false; });
      l.revs.unshift({ rev, content_digest: `mock-${planId}-${rev}`, is_current: true, in_flight: alive,
                       submitted_by: 'ops.zhang', submitted_at: new Date().toISOString(), lines });
      l.in_flight_rev = alive ? rev : null;
      l.current_rev = rev;
      revs.set(planId, l);
      return { rev, lines, in_flight: alive, content_digest: `mock-${planId}-${rev}`, skipped, ...r,
               split_frozen: { count: 0, lines: [] }, left_completed: { count: 0, lines: [] } };
    },

    async listRevs(planId) {
      return clone(revs.get(planId) ?? { revs: [], in_flight_rev: null, current_rev: null });
    },
    async setCurrentRev(planId, rev) {
      writable(planId);
      const l = revs.get(planId);
      if (!l || !l.revs.some((r) => r.rev === rev)) throw new ApiError(404, 'rev_not_found', '没有这一版', { plan_id: planId, rev });
      l.revs.forEach((r) => { r.is_current = r.rev === rev; });
      l.current_rev = rev;
      return { current_rev: rev };
    },
    async diff(_planId, _from, _to) {
      return {
        added: [{ sku: 'DCC1800264G1', period: '2026-11', total_units: 300 }],
        removed: [],
        changed: [{ sku: 'DCC1800264G1', period: '2026-10',
                    total_units: { from: 500, to: 600 }, demand_at_submit: { from: 160, to: 180 } }],
      };
    },
    async cancelRev(planId, rev, reason) {
      writable(planId);
      if (reason.trim() === '') throw new ApiError(400, 'reason_required', '撤销必须填理由', { plan_id: planId, rev });
      const l = revs.get(planId);
      const target = l?.revs.find((r) => r.rev === rev);
      if (!l || !target) throw new ApiError(404, 'rev_not_found', '没有这一版', { plan_id: planId, rev });
      target.in_flight = false;
      l.in_flight_rev = null;
      inFlight.set(planId, null);
      liveLines.delete(planId);
      plan(planId).state = '已撤销';
      // ★ team-lead 09-22 裁定：skipped_terminal 是保证字段（api/ui/submit.py:213-214）——
      //   「这一版本来就只有 1 条」与「另外 3 条早已在终态」长得一模一样，必须给这个数。
      //   替身不追踪逐条记录的状态，固定给 0（fixture 里 revs-1 的记录都当作非终态在流转）。
      return { cancelled: Array.from({ length: target.lines }, (_, i) => i + 1), skipped_terminal: 0, reason };
    },

    async listPurchaseOrders(state) {
      const rows: PoSummary[] = [...pos.values()]
        .filter((p) => !p.discarded_at && (state === undefined || p.state === state))
        .sort((a, b) => b.po_id - a.po_id)
        .map((p) => ({
          po_id: p.po_id, custom_order_sn: p.custom_order_sn, supplier_id: p.supplier_id,
          supplier_name: purchaseFixture.options.suppliers.find((x) => x.supplier_id === p.supplier_id)?.name ?? p.supplier_id,
          state: p.state, order_sn: p.order_sn, is_void: p.is_void, lines: p.lines.length,
          units: p.lines.reduce((a, l) => a + l.units, 0), open_attention: 0,
          created_by: p.created_by, created_at: p.created_at,
        }));
      return { purchase_orders: rows };
    },
    async getPurchaseOrder(poId) { return withDerived(po(poId)); },
    async createPurchaseOrder(body) {
      const id = nextPo;
      const p: PoRow = { po_id: id, supplier_id: '', purchaser_id: 0, opt_uid: 0, qc_type: 0, wid: null,
        custom_order_sn: `SCM-PO-${id}`, order_sn: null, state: '草稿', placed_at: null, draft_version: 1,
        created_by: 'ops.zhang', is_void: false, discarded_at: null, lines: [], source_line_states: [],
        attention: [], preflight: [], confirm_token: '', created_at: new Date().toISOString() };
      if (!purchaseFixture.options.suppliers.some((x) => x.supplier_id === body.supplier_id) && body.supplier_id)
        throw poErr(400, 'unknown_supplier', '没有这个供应商（不在供应商镜像里）', { supplier_id: body.supplier_id });
      applyBody(p, body);
      nextPo += 1;
      pos.set(id, p);
      return { po_id: id };
    },
    async updatePurchaseOrder(poId, body) {
      const p = draft(poId);
      if (body.draft_version !== p.draft_version)
        throw poErr(409, 'stale_draft', '草稿已被别人改过，请刷新后再改',
                    { po_id: poId, current_version: p.draft_version, got: body.draft_version });
      applyBody(p, body);
      p.draft_version += 1;
      return { po_id: poId, draft_version: p.draft_version };
    },
    async deletePurchaseOrder(poId) { draft(poId); pos.delete(poId); },
    async getPreflight(poId) {
      const gates = withDerived(po(poId)).preflight;
      return { po_id: poId, ok: gates.every((g) => g.ok), gates };
    },
    async placePurchaseOrder(_poId, _token) { return notSimulated('placePurchaseOrder'); },
    async getTimeline(poId) {
      const p = po(poId);
      return { po_id: poId, conclusion: null, attempts: [], attention: [],
               events: p.state === '已下单'
                 ? [{ from: '草稿', to: '已下单', actor: p.created_by, reason: null, src: 'mock', at: p.placed_at ?? p.created_at }]
                 : [] };
    },
    async recheck(_poId) { return notSimulated('recheck'); },
    async registerExisting(_poId, _orderSn) { return notSimulated('registerExisting'); },
    async confirmAbsent(_poId, _reason) { return notSimulated('confirmAbsent'); },
    async acknowledgeMismatch(_poId, _reason) { return notSimulated('acknowledgeMismatch'); },
    async listAttention() { return { count: 0, items: [] }; },
    async getPurchaseOptions() { return clone(purchaseFixture.options) as PurchaseOptions; },
    async getPurchaseChecks() { return clone(purchaseFixture.checks) as PurchaseChecks; },
    async listCandidateLines({ plan_ids, sku }) {
      const all = planLines.map(candidate).filter((l) => !plan_ids?.length || plan_ids.includes(l.plan_id));
      const excluded = { state: 0, sku: 0, category: 0, period: 0, fully_confirmed: 0 };
      let lines = all;
      if (sku) { excluded.sku = lines.filter((l) => l.sku !== sku).length; lines = lines.filter((l) => l.sku === sku); }
      excluded.state = lines.filter((l) => !ORDERABLE.includes(l.state)).length;
      lines = lines.filter((l) => ORDERABLE.includes(l.state));
      excluded.fully_confirmed = lines.filter((l) => l.remaining <= 0).length;
      lines = lines.filter((l) => l.remaining > 0);
      return { lines, groups: {}, excluded };
    },
    async getLineTrace(lineId) {
      const l = planLines.find((x) => x.line_id === lineId);
      if (!l) throw poErr(404, 'line_not_found', '没有这条记录', { line_id: lineId });
      return {
        line_id: l.line_id, sku: l.sku, period: l.period, total_units: l.total_units, state: l.state,
        confirmed_units: l.confirmed_units, ordered_units: l.ordered_units,
        purchase_orders: [...pos.values()].filter((p) => !p.discarded_at).flatMap((p) => {
          const sources = p.lines.flatMap((ln) => ln.sources.filter((s) => s.line_id === lineId))
            .map((s) => ({ seller_id: s.seller_id, units: s.units }));
          return sources.length === 0 ? [] : [{ po_id: p.po_id, custom_order_sn: p.custom_order_sn,
            order_sn: p.order_sn, state: p.state, is_void: p.is_void, sources }];
        }),
      };
    },
  };
}
