import { ApiError, type SupplyChainApi } from './client';
import type {
  CatalogResult, CountKey, GridResponse, PlanList, PlanSummary, RevList, Seller, SkippedCell,
} from './types';
import plansFixture from './fixtures/plans.json';
import gridFixture from './fixtures/grid-1.json';
import catalogFixture from './fixtures/catalog.json';
import revsFixture from './fixtures/revs-1.json';
import sellersFixture from './fixtures/sellers.json';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

export function createMockApi(): SupplyChainApi {
  const list = clone(plansFixture) as PlanList;
  const grids = new Map<number, GridResponse>([[1, clone(gridFixture) as GridResponse]]);
  const catalog = clone(catalogFixture) as CatalogResult;
  const revs = new Map<number, RevList>([[2, clone(revsFixture) as RevList]]);
  const sellers = (clone(sellersFixture) as { sellers: Seller[] }).sellers;
  const inFlight = new Map<number, number | null>([[1, null], [2, 2], [3, null], [4, null]]);

  const plan = (id: number): PlanSummary => {
    const p = list.plans.find((x) => x.plan_id === id);
    if (!p) throw new ApiError(404, 'plan_not_found', '没有这张计划', { plan_id: id });
    return p;
  };
  const grid = (id: number): GridResponse => {
    const g = grids.get(id);
    if (!g) throw new ApiError(404, 'plan_not_found', '这张计划还没有网格', { plan_id: id });
    return g;
  };

  /** ★ 一格的库存 = 该**店铺 × 货号**的在仓 − 该店该货号各 msku 的生效期望销量之和。
   *  任何一个 msku 未知 ⇒ 整格未知（M-8 向后传染）。落回 0 就是把「算不出来」说成「没货」。
   *  ★ 跨月是一条链：本月期初 = 上月期末（后端接口形状块 `onhand` 那一行写死了这一点）。 */
  function recompute(g: GridResponse, sku: string, sid: string): void {
    const rows = g.inventory
      .filter((i) => i.sku === sku && i.sid === sid)
      .sort((a, b) => a.period.localeCompare(b.period));
    let carried: number | null = rows[0]?.onhand ?? null;
    for (const row of rows) {
      if (row.basis.closing_reason === 'not_applicable') {
        // ★ 不适用：不是 0，也不参与链。reason 不动 —— 它恒为 no_seller_attribution
        row.onhand = null; row.basis.demand = null; row.closing = null; continue;
      }
      const mine = g.demand.filter((d) => d.sku === sku && d.sid === sid && d.period === row.period);
      const demand = mine.length === 0 || mine.some((d) => d.effective_units === null)
        ? null : mine.reduce((a, d) => a + (d.effective_units as number), 0);
      row.onhand = carried;
      row.basis.demand = demand;
      row.closing = carried === null || demand === null ? null : carried - demand;
      // ★ 只改 closing_reason；reason 是另一件事，改它就会把「在途没归属」这条信息抹掉
      row.basis.closing_reason = row.closing === null ? 'unknown_demand' : null;
      carried = row.closing;                      // ★ 本月期末 = 下月期初（链）
    }
  }

  return {
    async listPlans(q) {
      const plans = list.plans.filter((p) =>
        (q.state === undefined || p.state === q.state) &&
        (q.owner === undefined || p.owner_actor === q.owner) &&
        (q.archived === undefined || (p.archived_at !== null) === q.archived));
      return { plans, excluded: list.excluded };
    },
    async getPlan(planId) { return clone(plan(planId)); },
    async createPlan(input) {
      const plan_id = Math.max(...list.plans.map((p) => p.plan_id)) + 1;
      list.plans.push({
        plan_id, title: input.title, period_start: input.period_start, months: input.months,
        owner_actor: 'ops.zhang', archived_at: null, state: null, state_rev: null,
      });
      inFlight.set(plan_id, null);
      grids.set(plan_id, { plan_id, periods: [], demand: [], purchase: [], inventory: [], sku_pipeline: [] });
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
      for (const p of list.plans) {
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
      return {
        never_submitted: list.plans.filter((p) => p.state === null).map((p) => ({ plan_id: p.plan_id, title: p.title })),
        changed_since_submit: list.plans.filter((p) => p.plan_id === 2)
          .map((p) => ({ plan_id: p.plan_id, title: p.title, since_rev: p.state_rev ?? 0 })),
      };
    },
    async listSellers() { return clone(sellers); },

    async getGrid(planId) { return clone(grid(planId)); },

    async putDemand(planId, sellerSku, sid, period, units) {
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
      recompute(g, cell.sku, sid);
      return clone(cell);
    },

    async putPurchase(planId, sku, period, units) {
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
      const item = catalog.items.find((s) => s.mskus.some((m) => m.seller_sku === target.seller_sku && m.sid === target.sid));
      const msku = item?.mskus.find((m) => m.seller_sku === target.seller_sku && m.sid === target.sid);
      // ★ 与后端同码：api/ui/plans.py:131 用 unknown_msku，不是 msku_not_found
      if (!item || !msku) throw new ApiError(404, 'unknown_msku', '没有这个 msku', { ...target });
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
      const noHistory = mine.length > 0 && mine.every((d) => d.system_units === null)
        ? [{ seller_sku: target.seller_sku, sid: target.sid, reason: 'no_sales_history' as const }]
        : [];
      const months = plan(planId).months;
      return {
        claimed: { ...target, sku: item.sku },
        seeded: { demand_cells: months, purchase_cells: months },
        no_history: noHistory,
      };
    },

    async releaseClaim(_planId, sellerSku, sid) {
      const msku = catalog.items.flatMap((s) => s.mskus).find((m) => m.seller_sku === sellerSku && m.sid === sid);
      if (msku) { msku.selectable = true; msku.claimed_by = null; }   // ★ 释放不删行
      return { released: { seller_sku: sellerSku, sid }, dropped_cells: [], stranded_purchase_cells: [] };
    },

    async submit(planId) {
      const held = inFlight.get(planId) ?? null;
      if (held !== null) {
        throw new ApiError(409, 'rev_in_flight', `rev ${held} 还在流转`, { in_flight_rev: held });
      }
      const g = grid(planId);
      const skipped: SkippedCell[] = [];
      let lines = 0;
      for (const pc of g.purchase) {
        const claimed = g.demand.some((d) => d.sku === pc.sku && d.period === pc.period);
        if (!claimed) { skipped.push({ sku: pc.sku, period: pc.period, reason: 'no_claimed_msku' }); continue; }
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
      p.state = alive ? '已提交' : '已撤销';
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
