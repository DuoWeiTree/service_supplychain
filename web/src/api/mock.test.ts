import { beforeEach, describe, expect, it } from 'vitest';

import { createMockApi, markForecastStale } from './mock';
import { ApiError } from './client';
import type { CountKey, GridResponse } from './types';
import gridFixture from './fixtures/grid-1.json';

let api = createMockApi();
beforeEach(() => { api = createMockApi(); });

const firstMsku = async () => (await api.getGrid(1)).demand[0]!;

describe('mock 数据源', () => {
  it('月份一律 "YYYY-MM"，不是月初日期', async () => {
    const g = await api.getGrid(1);
    expect(g.periods.every((p) => /^\d{4}-\d{2}$/.test(p))).toBe(true);
  });

  it('putDemand(null) 存进去的是 null，不是 0；basis 退回 unknown 或 system', async () => {
    const d = await firstMsku();
    const cell = await api.putDemand(1, d.seller_sku, d.sid, d.period, null);
    expect(cell.expected_units).toBeNull();
    expect(['system', 'unknown']).toContain(cell.basis);
    const back = (await api.getGrid(1)).demand
      .find((x) => x.seller_sku === d.seller_sku && x.period === d.period)!;
    expect(back.expected_units).toBeNull();
  });

  // ★ Task 21：inventory[]/sku_pipeline[] 与 recompute() 一起退役（spec §6，阶段 A 的
  //   「在仓 − 期望」口径已被预测块取代）。recompute() 曾经专门保留 unknown_onhand 不被
  //   demand 编辑顺手覆盖成 unknown_demand —— 那条精细区分现在没有等价物了：
  //   markForecastStale()（OP-17，Task 9 引入）对同一 sku 的任何相关编辑一律改判成
  //   通用的 'not_recomputed_in_mock'（demand/onhand 都清空），不再区分「原本是哪种未知」。
  //   这不是本任务引入的回退 —— 是 mock 早就选择的「不重跑台账」简化（见上面 OP-17 注释），
  //   真正的成因区分（unknown_onhand ≠ unknown_demand）只在后端算，已由
  //   tests/test_api_grid.py 覆盖。故原 09-23 专项测试随 recompute() 一起删，不强行造等价物。

  it('★ forecast_store 的身份是「店铺 × 货号」—— 一格对应多个 msku，不是每个 msku 一格', async () => {
    const g = await api.getGrid(1);
    expect(g.forecast_store.every((r) => 'sku' in r && 'sid' in r && !('seller_sku' in r))).toBe(true);
    const keys = new Set(g.forecast_store.map((r) => `${r.sku}/${r.sid}/${r.period}`));
    expect(keys.size).toBe(g.forecast_store.length);        // ★ 每个 (货号,店铺,月) 只有一行
  });

  it('★ 任何一个 msku 未知 → 整格未知（不是把它当 0 再把别的 msku 加进来）', async () => {
    // ★ fixture 本身就种了这一形态（tests/test_grid_fixture.py 的 _build）：
    //   DCC1800264G1/11072 挂两个 msku —— MSKU-A（人填 130）与 MSKU-B（system_units
    //   被清空、demand 本就未知）。后端已经把整格算成未知，mock 原样透传，不必自己重算；
    //   若实现把 MSKU-B 的 null 当 0 跳过、只算 MSKU-A 的 130，这条断言会抓到。
    const g = await api.getGrid(1);
    const row = g.forecast_store.find(
      (r) => r.sku === 'DCC1800264G1' && r.sid === '11072' && r.period === '2026-10',
    )!;
    expect(row.state).toBe('unknown');
    expect(row.reason).toBe('unknown_demand');
    expect(row.closing).toBeNull();
  });

  it('★ 已有在途恒无店铺归属：inbound_planned 不因货号已有在途而非零，'
     + '总量只在货号层 forecast_sku.pipeline.ordered_open 里出现', async () => {
    const g = await api.getGrid(1);
    const known = g.forecast_store.filter((r) => r.inbound_planned !== null);
    expect(known.length).toBeGreaterThan(0);
    expect(known.every((r) => r.inbound_planned === 0)).toBe(true);
    const withPipeline = g.forecast_sku.filter((r) => r.pipeline.ordered_open > 0);
    expect(withPipeline.length).toBeGreaterThan(0);          // ★「有在途但没归属」这一形态必须在
  });

  it('★ 跨月是一条链：本月期初 = 上月期末（不是同一个在仓快照抄三遍）', async () => {
    const g0 = await api.getGrid(1);
    const groups = new Map<string, typeof g0.forecast_store>();
    for (const r of g0.forecast_store) {
      const k = `${r.sku}/${r.sid}`;
      groups.set(k, [...(groups.get(k) ?? []), r]);
    }
    const chain = [...groups.values()]
      .map((rows) => rows.sort((a, b) => a.period.localeCompare(b.period)))
      .find((rows) => rows.length >= 2 && rows[0]!.closing !== null
                      && rows[0]!.closing !== rows[0]!.opening);   // ★ 有消耗，两种口径才分得开
    // ★ fixture 里没有一个「有消耗」的月份时必须硬失败：那说明这条门禁什么都没测
    expect(chain, 'fixture 里没有 closing ≠ opening 的月份，链式口径无法证伪，请让后端补一个').toBeDefined();
    expect(chain![1]!.opening).toBe(chain![0]!.closing);
  });

  it('★ forecast_store.demand 与前端自己算的 Σ 是两个证人，必须一致', async () => {
    const g = await api.getGrid(1);
    for (const r of g.forecast_store) {
      if (r.state === 'not_applicable') continue;
      const mine = g.demand.filter((d) => d.sku === r.sku && d.sid === r.sid && d.period === r.period);
      const sum = mine.length === 0 || mine.some((d) => d.effective_units === null)
        ? null : mine.reduce((a, d) => a + (d.effective_units as number), 0);
      expect(r.demand).toBe(sum);
    }
  });

  it('★ OP-17：mock 不重跑台账 —— 改了期望销量，该货号的预测库存变「未知」，不留旧数', async () => {
    const api = createMockApi();
    await api.putDemand(1, 'MSKU-A', '11072', '2026-10', 999);
    const g = await api.getGrid(1);
    const sku = g.demand.find((d) => d.seller_sku === 'MSKU-A')!.sku;
    for (const key of ['forecast', 'forecast_store', 'forecast_sku'] as const) {
      const rows = (g[key] as { sku: string; state: string; reason: string | null; closing: number | null }[])
        .filter((r) => r.sku === sku);
      expect(rows.length).toBeGreaterThan(0);
      expect(rows.every((r) => r.state === 'unknown' && r.reason === 'not_recomputed_in_mock' && r.closing === null)).toBe(true);
    }
    const other = g.forecast_store.filter((r) => r.sku !== sku);
    expect(other.some((r) => r.reason !== 'not_recomputed_in_mock')).toBe(true);
  });

  it('★ OP-17：改计划采购同样让该货号的预测库存变「未知」；期初也不留旧数', async () => {
    const g0 = await api.getGrid(1);
    const pc = g0.purchase[0]!;
    await api.putPurchase(1, pc.sku, pc.period, 123);
    const g = await api.getGrid(1);
    const rows = g.forecast_store.filter((r) => r.sku === pc.sku && r.state !== 'not_applicable');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((r) => r.reason === 'not_recomputed_in_mock' && r.opening === null)).toBe(true);
    const sku = g.forecast_sku.filter((r) => r.sku === pc.sku);
    expect(sku.length).toBeGreaterThan(0);
    expect(sku.every((r) => r.reason === 'not_recomputed_in_mock' && r.closing === null)).toBe(true);
  });

  it('★ OP-17：「不适用」不随编辑变「未知」—— 它与输入无关，不适用 ≠ 未知', async () => {
    const g0 = await api.getGrid(1);
    const na = g0.forecast_store.find((r) => r.state === 'not_applicable')!;
    const edited = g0.demand.find((d) => d.sku === na.sku && d.sid !== na.sid)!;
    await api.putDemand(1, edited.seller_sku, edited.sid, edited.period, 5);
    const g = await api.getGrid(1);
    const rows = g.forecast_store.filter((r) => r.sku === na.sku);
    expect(rows.filter((r) => r.sid === na.sid).every((r) => r.state === 'not_applicable')).toBe(true);
    expect(rows.filter((r) => r.sid !== na.sid).every((r) => r.reason === 'not_recomputed_in_mock')).toBe(true);
    expect(g.forecast.filter((r) => r.sid === na.sid && r.sku === na.sku)
      .every((r) => r.state === 'not_applicable')).toBe(true);
  });

  it('新建计划的空网格也带预测块（params + 空数组），不缺键', async () => {
    const { plan_id } = await api.createPlan({ title: 't', period_start: '2026-10-01', months: 3 });
    const g = await api.getGrid(plan_id);
    expect(g.params.as_of).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    for (const k of ['forecast', 'forecast_msku', 'forecast_store', 'forecast_sku', 'beyond_horizon', 'unallocated'] as const) {
      expect(g[k]).toEqual([]);
    }
  });

  it('★ 占用撞了抛 409，占用方嵌在 claimed_by 里 —— 不是目录端点那种拍平写法', async () => {
    // ★ team-lead 09-22 裁定（找到 5）：真实后端 api/ui/plans.py:153-156 的 409 形状是
    //   {seller_sku, sid, claimed_by: {plan_id, actor, title} | null}，claimed_by 嵌套，
    //   不是像目录端点那样把 plan_id/title/actor 拍平在顶层。原文用 toMatchObject 只
    //   断言了两个键的部分匹配，两种形状都能蒙混过关；换成 toEqual 精确匹配整个 fields。
    const err = await api.claim(1, { seller_sku: 'MSKU-C', sid: '11094' }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).error).toBe('msku_already_claimed');
    expect((err as ApiError).fields).toEqual({
      seller_sku: 'MSKU-C', sid: '11094',
      claimed_by: { plan_id: 2, actor: 'ops.li', title: '2026 Q3 补货计划' },
    });
  });

  it('★ 提交逐条列 skipped[]，理由取 S-14 的两个值；铸出与跳过两个数都给', async () => {
    const r = await api.submit(1);
    expect(r.rev).toBe(1);
    expect(r.skipped.map((s) => s.reason)).toContain('zero_purchase');
    // ★ 被丢掉的那一侧要对得上：铸出 + 跳过 = 参与评估的（货号 × 月）格子数
    const g = await api.getGrid(1);
    expect(r.lines + r.skipped.length).toBe(g.purchase.length);
  });

  it('★ 已有在流转的版本 → 再提交 409 rev_in_flight，点名旧版号', async () => {
    const err = await api.submit(2).catch((e) => e);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).error).toBe('rev_in_flight');
    expect((err as ApiError).fields).toMatchObject({ in_flight_rev: 2 });
  });

  it('搜索目录：不给条件 → need_query=true 且 items 为空，与「查不到」不同形', async () => {
    const none = await api.searchCatalog({});
    expect(none.need_query).toBe(true);
    expect(none.items).toEqual([]);
    const miss = await api.searchCatalog({ q: 'ZZZZ' });
    expect(miss.need_query).toBe(false);
    expect(miss.matched).toBe(0);
  });

  it('撤销版本不填理由 → 400 reason_required', async () => {
    const err = await api.cancelRev(2, 2, '   ').catch((e) => e);
    expect((err as ApiError).status).toBe(400);
    expect((err as ApiError).error).toBe('reason_required');
  });

  it('★ 撤销版本要带 skipped_terminal（team-lead 09-22 裁定，找到 3）', async () => {
    const r = await api.cancelRev(2, 2, '试用期结束');
    expect(r.skipped_terminal).toBe(0);
    expect(r.cancelled.length).toBeGreaterThan(0);
  });

  it('★ 认领成功要带 seeded 与 no_history（team-lead 09-22 裁定，找到 2）', async () => {
    // plan 1 是唯一带着真实 grid-1.json 网格的 mock 计划，no_history 就从这张网格现查。
    // MSKU-A@11072：grid-1.json 里三个月 system_units 都有数（135/130/135，
    // base_rate × 天数重固化后的真数——F16），有历史可估 ⇒ no_history 必须是空数组，不是随手塞一条
    const withHistory = await api.claim(1, { seller_sku: 'MSKU-A', sid: '11072' });
    expect(withHistory.seeded).toEqual({ demand_cells: 3, purchase_cells: 3 });
    expect(withHistory.no_history).toEqual([]);

    // MSKU-B@11072：grid-1.json 里三个月 system_units 全是 null，
    // 没历史可估 ⇒ 必须点名，不能让「没历史」悄悄长得跟「预估是 0」一样
    const noHistory = await api.claim(1, { seller_sku: 'MSKU-B', sid: '11072' });
    expect(noHistory.no_history).toEqual([
      { seller_sku: 'MSKU-B', sid: '11072', reason: 'no_sales_history' },
    ]);

    // MSKU-W@90001（沃尔玛）：system_units 同样全 null，但 system_basis 记着「无销量取数源」
    // ⇒ 不适用，不是没历史 —— 与后端 claim() 同一个答案
    const noSource = await api.claim(1, { seller_sku: 'MSKU-W', sid: '90001' });
    expect(noSource.no_history).toEqual([
      { seller_sku: 'MSKU-W', sid: '90001', reason: 'not_applicable_no_sales_source', cause: 'non_amazon_platform' },
    ]);
  });

  // ★ I2（终审）：mock 与真 API 在「归档」这件事上语义相反，而唯一那条门禁看不见 ——
  //   plans.json 四张计划的 archived_at 全是 null，两种数据源在这条分叉上永远同答案。
  //   现在 fixture 里有一张归档计划（plan 5），下面四条各钉一侧。
  describe('归档：与 api/ui 同口径', () => {
    const ARCHIVED = 5;

    it('★ 不给 archived 时**排除**已归档，并把挡掉的那一侧数出来', async () => {
      const { plans, excluded } = await api.listPlans({});
      expect(plans.map((p) => p.plan_id)).not.toContain(ARCHIVED);
      // ★ 不是冻在 fixture 里的常量：加一张归档计划这个数就得跟着动
      expect(excluded.archived).toBe(1);
    });

    it('★ archived=true 是**全集**（含未归档），不是「只给归档的」；此时 excluded 为 0', async () => {
      const { plans, excluded } = await api.listPlans({ archived: true });
      expect(plans.map((p) => p.plan_id)).toContain(ARCHIVED);
      expect(plans.length).toBeGreaterThan(1);
      expect(excluded.archived).toBe(0);
    });

    it('★ 两个看板都只数没归档的（dashboard.py 的 archived_at IS NULL）', async () => {
      const { counts } = await api.dashboardPlans();
      // fixture 里没归档的「已提交」只有 plan 2；归档的 plan 5 也是已提交 —— 数进去就是 2
      expect(counts['已提交']).toBe(1);
      const board = await api.dashboardUnsubmitted();
      expect(board.never_submitted.map((p) => p.plan_id)).not.toContain(ARCHIVED);
    });

    it('★ 归档计划上的**每一个**写动作都 409 plan_archived，点名 archived_at', async () => {
      const writes: [string, Promise<unknown>][] = [
        ['claim', api.claim(ARCHIVED, { seller_sku: 'MSKU-A', sid: '11072' })],
        ['releaseClaim', api.releaseClaim(ARCHIVED, 'MSKU-A', '11072')],
        ['putDemand', api.putDemand(ARCHIVED, 'MSKU-A', '11072', '2026-10', 1)],
        ['putPurchase', api.putPurchase(ARCHIVED, 'DCC1800264G1', '2026-10', 1)],
        ['submit', api.submit(ARCHIVED)],
        ['setCurrentRev', api.setCurrentRev(ARCHIVED, 1)],
        ['cancelRev', api.cancelRev(ARCHIVED, 1, '理由')],
      ];
      for (const [name, p] of writes) {
        const err = await p.catch((e: unknown) => e);
        expect(err, name).toBeInstanceOf(ApiError);
        expect((err as ApiError).status, name).toBe(409);
        expect((err as ApiError).error, name).toBe('plan_archived');
        expect((err as ApiError).fields, name).toEqual({
          plan_id: ARCHIVED, archived_at: '2026-08-30T10:00:00+08:00',
        });
      }
    });

    it('★ 读不受影响：归档计划照样拿得到抬头（404 与 409 是两件事）', async () => {
      const p = await api.getPlan(ARCHIVED);
      expect(p.archived_at).not.toBeNull();
    });
  });

  it('★ 看板计数带全部 9 个桶（team-lead 09-22 裁定）：手列的宇宙会漏掉第 N+1 种状态', async () => {
    // ★ 与 api/ui/dashboard.py:18-31 同源：两个派生桶 + plan_line_state_rank（004 迁移，
    //   按 rank）+ 旁路终态已撤销。少一个键，Task 3 首页渲染「阶段 A 够不着」的桶时就会打洞。
    const want: CountKey[] = ['进行中', '已提交未确认', '已提交', '已确认', '已下单', '准备排货', '已排货', '已完结', '已撤销'];
    const { counts } = await api.dashboardPlans();
    expect(Object.keys(counts).sort()).toEqual([...want].sort());
    expect(Object.values(counts).every((n) => typeof n === 'number')).toBe(true);
    // ★ fixture 里 plan3=已撤销、plan4=已完结 —— 两个桶必须非零，否则这条门禁在假装测
    expect(counts['已撤销']).toBeGreaterThan(0);
    expect(counts['已完结']).toBeGreaterThan(0);
  });
});

describe('★ OP-17 复核 R1：依赖计划采购 / 期望销量的每一项都如实标「未重算」，不留旧数、不静默丢', () => {
  const base = (): GridResponse => JSON.parse(JSON.stringify(gridFixture)) as GridResponse;
  const SKU = 'DCC1800264G1';
  /** fixture 里没有到货与未分配 —— 这里合成一份，检验标记本身 */
  const withArrivals = (): GridResponse => {
    const g = base();
    const row = g.forecast.find((r) => r.sku === SKU && r.state === 'ok')!;
    row.basis.arrivals = [{ date: '2026-12-10', units: 40, order_period: '2026-10', pool: 500,
                            rule: 'x', gap_part: 40, weight_part: 0 }];
    g.unallocated = [
      { scope: 'batch', sku: SKU, order_period: '2026-11', arrive: '2027-01-13', units: 300,
        reason: 'gap_unknown', unknown: [['MSKU-B', '11072']] },
      { scope: 'batch', sku: 'A4P-TOY-002', order_period: '2026-11', arrive: '2027-01-13', units: 9,
        reason: 'gap_unknown', unknown: [] },
    ];
    return g;
  };

  it('改采购后：在途 / 在制 / 在船 / 未填采购 → null（未知），不是旧数', () => {
    const g = withArrivals();
    markForecastStale(g, SKU, 'purchase');
    const rows = g.forecast_sku.filter((r) => r.sku === SKU);
    expect(rows.length).toBeGreaterThan(0);
    for (const r of rows) {
      expect(r.in_transit).toBeNull();
      expect(r.pipeline.making).toBeNull();
      expect(r.pipeline.shipping).toBeNull();
      expect(r.unfilled_purchase).toBeNull();
      expect(r.reason).toBe('not_recomputed_in_mock');
    }
    // 别的货号不动
    expect(g.forecast_sku.filter((r) => r.sku !== SKU).every((r) => r.in_transit !== null)).toBe(true);
  });

  it('改采购后：超出推演窗 / 未分配 / 到货依据 都留着但标「未重算」、件数未知', () => {
    const g = withArrivals();
    const beyondBefore = g.beyond_horizon.filter((b) => b.sku === SKU).length;
    expect(beyondBefore).toBeGreaterThan(0);
    markForecastStale(g, SKU, 'purchase');
    const beyond = g.beyond_horizon.filter((b) => b.sku === SKU);
    expect(beyond).toHaveLength(beyondBefore);                       // ★ 不静默丢
    expect(beyond.every((b) => b.reason === 'not_recomputed_in_mock' && b.units === null)).toBe(true);
    const un = g.unallocated.filter((u) => u.sku === SKU);
    expect(un).toHaveLength(1);
    expect(un.every((u) => u.reason === 'not_recomputed_in_mock' && u.units === null)).toBe(true);
    expect(g.unallocated.find((u) => u.sku !== SKU)!.reason).toBe('gap_unknown');
    const arr = g.forecast.filter((r) => r.sku === SKU).flatMap((r) => r.basis.arrivals);
    expect(arr).toHaveLength(1);
    expect(arr.every((a) => a.reason === 'not_recomputed_in_mock' && a.units === null && a.pool === null)).toBe(true);
    for (const r of g.forecast.filter((x) => x.sku === SKU && x.basis.bridge)) {
      expect(r.basis.bridge!.arrival_units).toBeNull();
      expect(r.basis.bridge!.stockout_date).toBeNull();
    }
  });

  it('改期望销量：分配（到货 / 未分配）跟着失效；在途与超窗只看采购，照旧是当前数', () => {
    const g = withArrivals();
    const transit = g.forecast_sku.filter((r) => r.sku === SKU).map((r) => r.in_transit);
    markForecastStale(g, SKU, 'demand');
    expect(g.forecast_sku.filter((r) => r.sku === SKU).map((r) => r.in_transit)).toEqual(transit);
    expect(g.beyond_horizon.filter((b) => b.sku === SKU).every((b) => b.reason === 'arrives_after_horizon')).toBe(true);
    expect(g.unallocated.find((u) => u.sku === SKU)!.reason).toBe('not_recomputed_in_mock');
    expect(g.forecast.filter((r) => r.sku === SKU).flatMap((r) => r.basis.arrivals)
      .every((a) => a.reason === 'not_recomputed_in_mock')).toBe(true);
  });

  it('标了未知的行不留旧的 unknown_since', () => {
    const g = base();
    markForecastStale(g, SKU, 'demand');
    const rows = g.forecast.filter((r) => r.sku === SKU && r.reason === 'not_recomputed_in_mock');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((r) => r.unknown_since === null)).toBe(true);
  });

  it('createMockApi.putPurchase 走的是采购那条路径', async () => {
    const api = createMockApi();
    await api.putPurchase(1, SKU, '2026-10', 1);
    const g = await api.getGrid(1);
    expect(g.forecast_sku.filter((r) => r.sku === SKU).every((r) => r.in_transit === null)).toBe(true);
  });

  it('mock 提前期：可读可改；改了之后所有预测库存如实变「未知」（OP-17）', async () => {
    const api = createMockApi();
    expect(await api.getLeadTime()).toMatchObject({ make_days: 30, ship_days: 60, anchor_day: 15 });
    const r = await api.putLeadTime({ make_days: 10 });
    expect(r.changed).toEqual([{ segment: 'making', from: 30, to: 10 }]);
    const g = await api.getGrid(1);
    expect(g.params.make_days).toBe(10);
    expect(g.forecast_store.every((x) => x.reason === 'not_recomputed_in_mock' || x.state === 'not_applicable')).toBe(true);
  });
});
