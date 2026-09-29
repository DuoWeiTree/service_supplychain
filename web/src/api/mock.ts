import { ApiError, type SupplyChainApi } from './client';
import type {
  CatalogResult, CountKey, DroppedPurchaseCell, ForecastArrival, ForecastState, GridResponse, LeadTime,
  LeadTimeResult, NoHistoryEntry, PlanSummary, RevList, Seller, Sid, SkippedCell, SystemBasisNoSource,
} from './types';
import plansFixture from './fixtures/plans.json';
import gridFixture from './fixtures/grid-1.json';
import catalogFixture from './fixtures/catalog.json';
import revsFixture from './fixtures/revs-1.json';
import sellersFixture from './fixtures/sellers.json';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

const STALE = 'not_recomputed_in_mock' as const;

/** ★ OP-17：mock 不重跑逐日台账（那是后端 forecast/ 的活，照抄一份迟早分叉；docs/05 前端不承载判据）。
 *  改了之后该货号**依赖这次输入**的每一项如实标「未重算」、数变未知 —— 不留旧数，也不删条目
 *  （删了就把「没算进来」说成了「没有」）。
 *  - 两条路径都失效：库存链、断货日、分配（到货 / 未分配 —— 分配按缺口与期望销量比例，14 §3.1）
 *  - 只有采购路径失效：在制 / 在船 / 在途、未填采购月、超出推演窗（`forecast/plan_forecast.py` 由采购批次推出）
 *  ★ 不适用 / 已过去 / 没有 msku 的行，状态与输入无关，原样保留 —— 改成「未知」就是把不适用说成未知。 */
export function markForecastStale(g: GridResponse, sku: string, cause: 'demand' | 'purchase'): void {
  const hit = (r: { sku: string; state: ForecastState }) =>
    r.sku === sku && r.state !== 'not_applicable' && r.state !== 'elapsed' && r.state !== 'no_msku';
  const stale = { state: 'unknown' as const, reason: STALE,
                  opening: null, demand: null, inbound_planned: null, closing: null };
  const staleArrival = (x: ForecastArrival): ForecastArrival => ({
    date: x.date, order_period: x.order_period, units: null, pool: null, reason: STALE,
  });
  for (const r of g.forecast) {
    if (r.sku !== sku) continue;
    r.basis.arrivals = r.basis.arrivals.map(staleArrival);
    if (r.basis.bridge) {
      const b = r.basis.bridge;
      Object.assign(b, { arrivals: b.arrivals.map(staleArrival), arrival_units: null, stockout_date: null });
    }
    if (hit(r)) Object.assign(r, stale, { stockout_date: null, unknown_since: null });
  }
  for (const r of g.forecast_msku) if (r.sku === sku) r.first_stockout_date = null;
  for (const r of g.forecast_store) if (hit(r)) Object.assign(r, stale);
  for (const r of g.forecast_sku) {
    if (r.sku !== sku) continue;
    if (hit(r)) Object.assign(r, stale, { reason_from: null, stock_demand: null });
    if (cause === 'purchase') {
      Object.assign(r, { in_transit: null, unfilled_purchase: null });
      Object.assign(r.pipeline, { making: null, shipping: null });
    }
  }
  for (const u of g.unallocated) {
    if (u.sku !== sku) continue;
    Object.assign(u, { reason: STALE, units: null });
    if (u.scope === 'msku') { u.pool = null; delete u.rule; delete u.gap_part; delete u.weight_part; }
  }
  if (cause === 'purchase') {
    for (const b of g.beyond_horizon) if (b.sku === sku) Object.assign(b, { reason: STALE, units: null });
  }
}

export function createMockApi(): SupplyChainApi {
  // ★ fixture 里只有 plans：`excluded.archived` 是**按这次查询现算**的（见 listPlans），
  //   冻一个常量进去就会在加了归档计划之后继续报旧数，而那正是它要挡的事
  const list = { plans: clone(plansFixture.plans) as PlanSummary[] };
  const grids = new Map<number, GridResponse>([[1, clone(gridFixture) as GridResponse]]);
  const catalog = clone(catalogFixture) as CatalogResult;
  const revs = new Map<number, RevList>([[2, clone(revsFixture) as RevList]]);
  const sellers = (clone(sellersFixture) as { sellers: Seller[] }).sellers;
  const inFlight = new Map<number, number | null>([[1, null], [2, 2], [3, null], [4, null]]);
  // ★ 14 B12：全局配置，与 grid-1.json 的 params 出厂值一致（30/60/15）
  let lead: LeadTime = {
    route: 'oversea_sea', make_days: 30, ship_days: 60, anchor_day: 15,
    valid_from: { making: '2026-01-01', shipping: '2026-01-01' },
  };

  const plan = (id: number): PlanSummary => {
    const p = list.plans.find((x) => x.plan_id === id);
    if (!p) throw new ApiError(404, 'plan_not_found', '没有这张计划', { plan_id: id });
    return p;
  };
  /** ★ 归档后一律拒写（`api/ui/deps.py:82-95` 的 `ensure_writable`，后端每个写端点都挂着它）。
   *  mock 从前一处都没有：归档计划上的认领/填数/提交在 mock 下全成功、在真 API 下全 409，
   *  而两种数据源的唯一门禁读的是同一份 fixture，这条分叉永远照不到。
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
   *  货号没 msku 了，采购格与该货号的每一个预测块一起走（OP-17：走掉的货号不标「未重算」，是没了），
   *  删掉的采购格逐格回报。货号还有 msku → 照 OP-17 标「未重算」。 */
  const settleSku = (g: GridResponse, sku: string, sids: Iterable<Sid>): DroppedPurchaseCell[] => {
    for (const sid of sids) {
      if (!g.demand.some((d) => d.sku === sku && d.sid === sid)) {
        g.forecast_store = g.forecast_store.filter((r) => !(r.sku === sku && r.sid === sid));
      }
    }
    if (g.demand.some((d) => d.sku === sku)) { markForecastStale(g, sku, 'demand'); return []; }
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
      //   这两处 mock 从前都反着来，而归档语义的分叉恰好落在唯一那条门禁的视野之外。
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
    // ★ OP-17：mock 不重跑逐日台账 —— 改完把每张网格的 params 同步（界面靠它显示图注），
    //   再对网格里出现过的每个货号调 markForecastStale('purchase')（提前期只影响到货日，
    //   与改采购量同一条失效路径：在制/在船/在途、未填采购月、超出推演窗）
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
        const skus = new Set(g.purchase.map((p) => p.sku));
        for (const sku of skus) markForecastStale(g, sku, 'purchase');
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
      markForecastStale(g, cell.sku, 'demand');
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
      markForecastStale(g, sku, 'purchase');
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
      //   mock 没有真的重新播种格子，用 plan 的 months 代表种出的月份数（与真实播种的行数一致）；
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

    // ★ task-23：与 claim() 同一个理由（OP-17）——mock 不重跑逐日台账，那是后端
    //   forecast/ 的活，照抄一份迟早分叉。这里如实报「没有一格变了」（unchanged 全数），
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
      const held = inFlight.get(planId) ?? null;
      if (held !== null) {
        throw new ApiError(409, 'rev_in_flight', `rev ${held} 还在流转`, { in_flight_rev: held });
      }
      const g = grid(planId);
      const skipped: SkippedCell[] = [];
      let lines = 0;
      // ★ M2：判据的键照抄 `rules/submit.py:53` —— 后端是 `{c.sku for c in demand_cells}`，
      //   **只按货号**，不带月份。当前 fixture 每个货号三个月全有需求格，两者给同一答案，
      //   所以这是潜伏分叉不是现症；按月份多卡一道，会在「某个月没认领 msku」时
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
        if (pc.planned_units === null || pc.planned_units === 0) {
          skipped.push({ sku: pc.sku, period: pc.period, reason: 'zero_purchase' }); continue;
        }
        lines += 1;
      }
      const p = plan(planId);
      const rev = (p.state_rev ?? 0) + 1;
      // ★ 空版本不占在流转位：占着的话这张计划从此再也提交不了，而错误会说「有一版在流转」
      const alive = lines > 0;
      p.state_rev = rev;
      // ★ M3：铸出 0 条时后端一条 plan_line 都没有，木桶派生的 v_plan_overall_state.overall
      //   是 NULL（即「未提交」）—— 不是「已撤销」。写成已撤销会让两种数据源的首页计数不同
      p.state = alive ? '已提交' : null;
      inFlight.set(planId, alive ? rev : null);
      revs.set(planId, {
        revs: [{ rev, content_digest: `mock-${planId}-${rev}`, is_current: true, in_flight: alive,
                 submitted_by: 'ops.zhang', submitted_at: new Date().toISOString(), lines }],
        in_flight_rev: alive ? rev : null, current_rev: rev,
      });
      return { rev, lines, in_flight: alive, content_digest: `mock-${planId}-${rev}`, skipped };
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
      plan(planId).state = '已撤销';
      // ★ team-lead 09-22 裁定：skipped_terminal 是保证字段（api/ui/submit.py:213-214）——
      //   「这一版本来就只有 1 条」与「另外 3 条早已在终态」长得一模一样，必须给这个数。
      //   mock 不追踪逐条记录的状态，固定给 0（fixture 里 revs-1 的记录都当作非终态在流转）。
      return { cancelled: Array.from({ length: target.lines }, (_, i) => i + 1), skipped_terminal: 0, reason };
    },
  };
}
