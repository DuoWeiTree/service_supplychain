import { beforeEach, describe, expect, it } from 'vitest';
import { createMockApi } from './mock';
import { ApiError } from './client';
import type { CountKey } from './types';

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

  it('★ 库存的身份是「店铺 × 货号」—— 一格对应多个 msku，不是每个 msku 一格', async () => {
    const g = await api.getGrid(1);
    expect(g.inventory.every((i) => 'sku' in i && 'sid' in i && !('seller_sku' in i))).toBe(true);
    const keys = new Set(g.inventory.map((i) => `${i.sku}/${i.sid}/${i.period}`));
    expect(keys.size).toBe(g.inventory.length);           // ★ 每个 (货号,店铺,月) 只有一行
  });

  it('★ 任何一个 msku 未知 → 整格未知（不是把它当 0 再把别的 msku 加进来）', async () => {
    // ★ 与 brief 原文不同：不用 firstMsku()（demand[0] = A4P-TOY-002/11072 的 MSKU-D，
    //   system_units=7，清空后按契约退回系统预估 7，格子仍是「已知」，测不出这条判据）。
    //   改用 DCC1800264G1/11072/2026-10 —— 它天生挂两个 msku：MSKU-A（人填 130，系统 100）
    //   与 MSKU-B（系统也是 null，本就未知）。清空 MSKU-A 后落回系统 100（不是 null），
    //   但整格必须仍是未知 —— 若实现把 MSKU-B 的 null 当 0 跳过、只加 MSKU-A 的 100，
    //   这条断言才会抓到。
    const g0 = await api.getGrid(1);
    const known = g0.demand.find(
      (d) => d.sku === 'DCC1800264G1' && d.sid === '11072' && d.period === '2026-10',
    )!;
    expect(known.seller_sku).toBe('MSKU-A');
    await api.putDemand(1, known.seller_sku, known.sid, known.period, null);
    const g = await api.getGrid(1);
    const cell = g.inventory.find((i) => i.sku === 'DCC1800264G1' && i.sid === '11072' && i.period === '2026-10')!;
    expect(cell.closing).toBeNull();
    expect(cell.basis.closing_reason).toBe('unknown_demand');
    // ★ reason 是另一件事，不许被顺手改掉 —— 改了就把「在途没归属」这条信息抹掉了
    expect(cell.basis.reason).toBe('no_seller_attribution');
  });

  it('★ inbound 恒 null（不是 0）；在途总量只在 basis 与 sku_pipeline 里出现', async () => {
    const g = await api.getGrid(1);
    expect(g.inventory.every((i) => i.inbound === null)).toBe(true);
    expect(g.inventory.every((i) => i.basis.includes_plan_purchase === false)).toBe(true);
    expect(g.sku_pipeline.every((r) => r.no_seller_attribution === true)).toBe(true);
    // ★ 在途一件都没有并进任何一格库存
    for (const i of g.inventory) {
      const t = i.basis.sku_level_in_transit;
      if (t !== null && i.onhand !== null && i.closing !== null) expect(i.closing).not.toBe(i.onhand + t);
    }
  });

  it('★ 跨月是一条链：本月期初 = 上月期末（不是同一个在仓快照抄三遍）', async () => {
    const g0 = await api.getGrid(1);
    const groups = new Map<string, typeof g0.inventory>();
    for (const i of g0.inventory) {
      const k = `${i.sku}/${i.sid}`;
      groups.set(k, [...(groups.get(k) ?? []), i]);
    }
    const chain = [...groups.values()]
      .map((rows) => rows.sort((a, b) => a.period.localeCompare(b.period)))
      .find((rows) => rows.length >= 2 && rows[0]!.closing !== null
                      && rows[0]!.closing !== rows[0]!.onhand);   // ★ 有消耗，两种口径才分得开
    // ★ fixture 里没有一个「有消耗」的月份时必须硬失败：那说明这条门禁什么都没测
    expect(chain, 'fixture 里没有 closing ≠ onhand 的月份，链式口径无法证伪，请让后端补一个').toBeDefined();
    expect(chain![1]!.onhand).toBe(chain![0]!.closing);
  });

  it('★ basis.demand 与前端自己算的 Σ 是两个证人，必须一致', async () => {
    const g = await api.getGrid(1);
    for (const i of g.inventory) {
      if (i.basis.closing_reason === 'not_applicable') continue;
      const mine = g.demand.filter((d) => d.sku === i.sku && d.sid === i.sid && d.period === i.period);
      const sum = mine.length === 0 || mine.some((d) => d.effective_units === null)
        ? null : mine.reduce((a, d) => a + (d.effective_units as number), 0);
      expect(i.basis.demand).toBe(sum);
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
    // MSKU-A@11072：grid-1.json 里三个月 system_units 都有数（100/120/90），
    // 有历史可估 ⇒ no_history 必须是空数组，不是随手塞一条
    const withHistory = await api.claim(1, { seller_sku: 'MSKU-A', sid: '11072' });
    expect(withHistory.seeded).toEqual({ demand_cells: 3, purchase_cells: 3 });
    expect(withHistory.no_history).toEqual([]);

    // MSKU-B@11072：grid-1.json 里三个月 system_units 全是 null，
    // 没历史可估 ⇒ 必须点名，不能让「没历史」悄悄长得跟「预估是 0」一样
    const noHistory = await api.claim(1, { seller_sku: 'MSKU-B', sid: '11072' });
    expect(noHistory.no_history).toEqual([
      { seller_sku: 'MSKU-B', sid: '11072', reason: 'no_sales_history' },
    ]);
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
