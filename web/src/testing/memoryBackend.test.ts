import { beforeEach, describe, expect, it } from 'vitest';

import { createMemoryBackend } from './memoryBackend';
import { ApiError } from '../api/client';
import type { CountKey, GridResponse, Seller } from '../api/types';
import gridFixture from './fixtures/grid-1.json';
import sellersFixture from './fixtures/sellers.json';
import { buildGridModel } from '../pages/planGridModel';

let api = createMemoryBackend();
beforeEach(() => { api = createMemoryBackend(); });

const firstMsku = async () => (await api.getGrid(1)).demand[0]!;

describe('memoryBackend（测试替身）', () => {
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

  it('★ forecast_store 的身份是「店铺 × 货号」—— 一格对应多个 msku，不是每个 msku 一格', async () => {
    const g = await api.getGrid(1);
    expect(g.forecast_store.every((r) => 'sku' in r && 'sid' in r && !('seller_sku' in r))).toBe(true);
    const keys = new Set(g.forecast_store.map((r) => `${r.sku}/${r.sid}/${r.period}`));
    expect(keys.size).toBe(g.forecast_store.length);        // ★ 每个 (货号,店铺,月) 只有一行
  });

  it('★ 任何一个 msku 未知 → 整格未知（不是把它当 0 再把别的 msku 加进来）', async () => {
    // ★ fixture 本身就种了这一形态（tests/test_grid_fixture.py 的 _build）：
    //   DCC1800264G1/11072 挂两个 msku —— MSKU-A（人填 130）与 MSKU-B（system_units
    //   被清空、demand 本就未知）。后端已经把整格算成未知，替身原样透传，不必自己重算；
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

  it('★ 预测块是固定样本：编辑期望 / 采购不改它（重算是后端 forecast/ 的活，替身不照抄）', async () => {
    const before = (await api.getGrid(1)).forecast_store;
    await api.putDemand(1, 'MSKU-A', '11072', '2026-10', 999);
    const g0 = await api.getGrid(1);
    await api.putPurchase(1, g0.purchase[0]!.sku, g0.purchase[0]!.period, 123);
    expect((await api.getGrid(1)).forecast_store).toEqual(before);
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
    // plan 1 是唯一带着真实 grid-1.json 网格的替身计划，no_history 就从这张网格现查。
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

  // ★ I2（终审）：替身与真 API 在「归档」这件事上语义相反，而唯一那条门禁看不见 ——
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
        ['removeSku', api.removeSku(ARCHIVED, 'DCC1800264G1')],
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

describe('提前期', () => {
  it('可读可改；改了之后每张网格的 params 同步（界面靠它显示图注）', async () => {
    const api = createMemoryBackend();
    expect(await api.getLeadTime()).toMatchObject({ make_days: 30, ship_days: 60, anchor_day: 15 });
    const r = await api.putLeadTime({ make_days: 10 });
    expect(r.changed).toEqual([{ segment: 'making', from: 30, to: 10 }]);
    expect((await api.getGrid(1)).params.make_days).toBe(10);
  });
});

describe('★ Task 22（业主裁定 2026-09-27）：货号没有 msku 了，货号也移出 —— 与 api/ui/plans.py 同口径', () => {
  /** 该货号在网格的每一个块里还剩几行 —— 「消失」要每个块都数，漏一个就是半个货号 */
  const leftOf = (g: GridResponse, sku: string) => ({
    demand: g.demand.filter((r) => r.sku === sku).length,
    purchase: g.purchase.filter((r) => r.sku === sku).length,
    forecast: g.forecast.filter((r) => r.sku === sku).length,
    forecast_msku: g.forecast_msku.filter((r) => r.sku === sku).length,
    forecast_store: g.forecast_store.filter((r) => r.sku === sku).length,
    forecast_sku: g.forecast_sku.filter((r) => r.sku === sku).length,
    beyond_horizon: g.beyond_horizon.filter((r) => r.sku === sku).length,
    unallocated: g.unallocated.filter((r) => r.sku === sku).length,
  });
  const GONE = { demand: 0, purchase: 0, forecast: 0, forecast_msku: 0, forecast_store: 0,
                 forecast_sku: 0, beyond_horizon: 0, unallocated: 0 };
  const sellers = sellersFixture.sellers as Seller[];

  it('移出货号的最后一个 msku：采购格随之删掉并逐格回报，货号从网格里消失', async () => {
    const r = await api.releaseClaim(1, 'MSKU-E', '11072');
    expect(r.dropped_purchase_cells).toEqual(['2026-10', '2026-11', '2026-12'].map((period) => (
      { sku: 'A4P-TOY-003', period, planned_units: null })));
    const g = await api.getGrid(1);
    expect(leftOf(g, 'A4P-TOY-003')).toEqual(GONE);
    // ★ 删干净了才不会冒出「有库存没销量」之类的孤儿 —— 两个真相
    expect(buildGridModel(g, sellers).orphans).toEqual([]);
  });

  it('不是最后一个：采购格一格不动，只走这个 msku；这家店没 msku 了，店铺行也走', async () => {
    const r = await api.releaseClaim(1, 'MSKU-D', '11072');
    expect(r.dropped_purchase_cells).toEqual([]);
    expect(r.dropped_cells.map((c) => c.period)).toEqual(['2026-10', '2026-11', '2026-12']);
    const g = await api.getGrid(1);
    expect(g.purchase.filter((p) => p.sku === 'A4P-TOY-002')).toHaveLength(3);
    expect(g.demand.filter((d) => d.seller_sku === 'MSKU-D')).toEqual([]);
    expect(g.forecast_store.filter((s) => s.sku === 'A4P-TOY-002' && s.sid === '11072')).toEqual([]);
    expect(buildGridModel(g, sellers).orphans).toEqual([]);
  });

  it('removeSku：跨店释放全部 msku、期望与采购格一并删掉，人填过的数带值回报', async () => {
    const r = await api.removeSku(1, 'DCC1800264G1');
    expect(r.sku).toBe('DCC1800264G1');
    expect(r.released).toEqual([
      { seller_sku: 'MSKU-A', sid: '11072' }, { seller_sku: 'MSKU-B', sid: '11072' },
      { seller_sku: 'MSKU-C', sid: '11094' }]);
    expect(r.dropped_cells).toHaveLength(9);
    expect(r.dropped_cells).toContainEqual({ seller_sku: 'MSKU-A', sid: '11072', period: '2026-10', expected_units: 130 });
    expect(r.dropped_purchase_cells).toContainEqual({ sku: 'DCC1800264G1', period: '2026-10', planned_units: 500 });
    expect(r.dropped_purchase_cells).toHaveLength(3);
    const g = await api.getGrid(1);
    expect(leftOf(g, 'DCC1800264G1')).toEqual(GONE);
    expect(g.purchase.filter((p) => p.sku === 'A4P-TOY-002')).toHaveLength(3);   // 别的货号不动
    expect(buildGridModel(g, sellers).orphans).toEqual([]);
  });

  it('removeSku：计划里没有这个货号 → 404 sku_not_in_plan', async () => {
    const err = await api.removeSku(1, 'NOPE').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).error).toBe('sku_not_in_plan');
  });

  describe('采购（只读 + 草稿增删改；不下单）', () => {
    const body = (over: Record<string, unknown> = {}, units = 10, seller = '11072', lineId = 101) => ({
      supplier_id: 'S-1001', purchaser_id: 111, opt_uid: 10925905, qc_type: 1 as const,
      lines: [{ sku: 'A4P-TOY-002', wid: 101, units, price: '12.5',
                sources: [{ line_id: lineId, seller_id: seller, units }] }],
      ...over,
    });
    const code = async (p: Promise<unknown>) => {
      const e = await p.catch((x: unknown) => x);
      expect(e).toBeInstanceOf(ApiError);
      return e as ApiError;
    };

    it('读：列表按状态筛、详情带 preflight / confirm_token、候选行带 orderable 过滤', async () => {
      expect((await api.listPurchaseOrders('已下单')).purchase_orders.map((p) => p.po_id)).toEqual([2]);
      expect((await api.listPurchaseOrders()).purchase_orders).toHaveLength(2);
      const po = await api.getPurchaseOrder(1);
      expect(po.preflight.length).toBeGreaterThan(0);
      expect(po.confirm_token).not.toBe('');
      const c = await api.listCandidateLines({});
      expect(c.lines.map((l) => l.line_id)).toEqual([101, 103]);   // 102 已全确认；被丢的一侧要有数
      expect(c.excluded.fully_confirmed).toBe(1);
    });

    it('草稿增删改：创建 → draft_version 递增 → 删除', async () => {
      const { po_id } = await api.createPurchaseOrder(body());
      expect((await api.getPurchaseOrder(po_id)).draft_version).toBe(1);
      const r = await api.updatePurchaseOrder(po_id, { ...body({}, 12), draft_version: 1 });
      expect(r).toEqual({ po_id, draft_version: 2 });
      await api.deletePurchaseOrder(po_id);
      expect((await code(api.getPurchaseOrder(po_id))).error).toBe('po_not_found');
    });

    it('★ over_seller_cap：形状同后端（line_id/seller_id/cap/used/requested）', async () => {
      // 100 件、期望 40:20 ⇒ 最大余数法 67 / 33；11072 已被 PO1 占 30
      const e = await code(api.createPurchaseOrder(body({}, 38, '11072')));
      expect([e.status, e.error]).toEqual([409, 'over_seller_cap']);
      expect(e.fields).toEqual({ line_id: 101, seller_id: '11072', cap: 67, used: 30, requested: 38 });
      await api.createPurchaseOrder(body({}, 37, '11072'));   // 恰在上限上不拒
    });

    it('★ stale_draft：draft_version 不对 ⇒ 409 点名 current_version / got', async () => {
      const e = await code(api.updatePurchaseOrder(1, { ...body(), draft_version: 9 }));
      expect([e.status, e.error]).toEqual([409, 'stale_draft']);
      expect(e.fields).toMatchObject({ po_id: 1, current_version: 1, got: 9 });
    });

    it('已下单的单不可改不可删（po_frozen）', async () => {
      expect((await code(api.deletePurchaseOrder(2))).error).toBe('po_frozen');
    });

    it('★ 下单与四个转人工动作不模拟领星：调用即抛测试错误（不是 ApiError，不冒充后端答复）', async () => {
      const calls = [
        () => api.placePurchaseOrder(1, 't'), () => api.recheck(1), () => api.registerExisting(1, 'PO1'),
        () => api.confirmAbsent(1, 'r'), () => api.acknowledgeMismatch(1, 'r'),
      ];
      for (const c of calls) {
        const e: unknown = await c().then(() => null, (x: unknown) => x);
        expect(e).toBeInstanceOf(Error);
        expect(e).not.toBeInstanceOf(ApiError);
        expect((e as Error).message).toMatch(/memoryBackend 不模拟领星.*stubBackend/);
      }
      expect((await api.getPurchaseOrder(1)).state).toBe('草稿');   // 没有半截状态
    });
  });
});

