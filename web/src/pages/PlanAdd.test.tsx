import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { CatalogResult } from '../api/types';

const CATALOG: CatalogResult = {
  need_query: false, truncated: false, limit: 50,
  items: [
    { sku: 'SKU-1', name: '猫爬架',
      mskus: [
        { seller_sku: 'MSKU-A', sid: '11072', seller_name: 'A4Pet-US', selectable: true, claimed_by: null },
        { seller_sku: 'MSKU-C', sid: '11094', seller_name: 'A4Pet-BS-UK', selectable: false,
          claimed_by: { plan_id: 2, title: '2026 Q3 补货计划', actor: 'ops.li' } },
        { seller_sku: 'MSKU-W', sid: '90001', seller_name: 'A4Pet-WM', selectable: true, claimed_by: null },
      ],
      unbuildable_sellers: [{ sid: '30112', reason: 'no_channel_code' }],
      claimed_by: { plan_id: 2, title: '2026 Q3 补货计划' },
      claimed_by_plans: [{ plan_id: 2, title: '2026 Q3 补货计划' }] },
    { sku: 'SKU-2', name: '逗猫棒',
      mskus: [{ seller_sku: 'MSKU-B', sid: '11072', seller_name: 'A4Pet-US', selectable: true, claimed_by: null }],
      unbuildable_sellers: [], claimed_by: null, claimed_by_plans: [] },
  ],
};

beforeEach(() => { vi.resetModules(); });

async function renderAdd(opts: { failOn?: string; failNoHolder?: string; recent?: unknown } = {}) {
  const { ApiError } = await import('../api/client');
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      searchCatalog: async (q: { q?: string }) =>
        !q.q ? { need_query: true, truncated: false, limit: 50, items: [] }
             // ★ 故意不带 matched：判空不许依赖这个可选字段
             : q.q === 'ZZZZ' ? { need_query: false, truncated: false, limit: 50, items: [] }
             : JSON.parse(JSON.stringify(CATALOG)) as CatalogResult,
      recentCatalog: async () => opts.recent ?? EMPTY_RECENT,
      claim: async (_p: number, t: { seller_sku: string }) => {
        if (t.seller_sku === opts.failOn) {
          // ★ 真实契约只有一种形状（api/ui/plans.py:156-160；mock.ts 自 Task 2 起也是这样抛）：
          //   claimed_by 嵌套在 fields 里，不是打平的 title/actor
          throw new ApiError(409, 'msku_already_claimed', `${t.seller_sku} 已被占用`,
            { seller_sku: t.seller_sku, sid: '11072',
              claimed_by: { plan_id: 2, title: '2026 Q3 补货计划', actor: 'ops.li' } });
        }
        if (t.seller_sku === opts.failNoHolder) {
          // ★ 负例：claimed_by 为 null 时只给 hint，不许现造一个占用方
          throw new ApiError(409, 'msku_already_claimed', `${t.seller_sku} 已被占用`,
            { seller_sku: t.seller_sku, sid: '11072', claimed_by: null });
        }
        return { claimed: { seller_sku: t.seller_sku, sid: '11072', sku: 'SKU-1' } };
      },
    },
  }));
  const { PlanAdd } = await import('./PlanAdd');
  return render(
    <MemoryRouter initialEntries={['/plans/1/add']}>
      <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes>
    </MemoryRouter>,
  );
}

const EMPTY_RECENT = { items: [], limit: 50, truncated: false, unmapped_claims: 0 };

/** 输入 → 等下拉出现这个货号 → 点它。选中后该货号进「已选货号」区 */
const search = async (q: string, sku = q) => {
  const box = screen.getByRole('combobox', { name: '搜货号' });
  await userEvent.clear(box);
  await userEvent.type(box, q);
  await userEvent.click(await screen.findByRole('option', { name: new RegExp(sku) }));
};

describe('批量添加', () => {
  it('★ 还没选 ≠ 搜了没有：两种空屏的字不一样', async () => {
    await renderAdd();
    expect(await screen.findByTestId('need-query')).toBeInTheDocument();
    expect(screen.queryByTestId('no-hit')).toBeNull();

    await userEvent.type(screen.getByRole('combobox', { name: '搜货号' }), 'ZZZZ');
    expect(await screen.findByTestId('no-hit')).toHaveTextContent('没有命中的货号');
  });

  it('★ 被占用的行留在表里标出来并点名占用方', async () => {
    await renderAdd();
    await search('SKU-1');
    const row = await screen.findByTestId('msku-MSKU-C-11094');
    expect(row).toHaveClass('off');
    expect(within(row).getByText('2026 Q3 补货计划')).toBeInTheDocument();
    expect(within(row).getByText('ops.li')).toBeInTheDocument();
    expect(within(row).getByRole('checkbox')).toBeDisabled();
  });

  it('选中货号 → 可认领的 msku 默认全勾上，被占用的不勾', async () => {
    await renderAdd();
    await search('SKU-1');
    expect(within(await screen.findByTestId('msku-MSKU-A-11072')).getByRole('checkbox')).toBeChecked();
    expect(within(screen.getByTestId('msku-MSKU-W-90001')).getByRole('checkbox')).toBeChecked();
    expect(within(screen.getByTestId('msku-MSKU-C-11094')).getByRole('checkbox')).not.toBeChecked();
    expect(screen.getByTestId('picked')).toHaveTextContent('已选 2');
  });

  it('★ 认领是 msku 级：取消一个不会把同货号的另一个也取消', async () => {
    await renderAdd();
    await search('SKU-1');
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A-11072')).getByRole('checkbox'));
    expect(within(screen.getByTestId('msku-MSKU-W-90001')).getByRole('checkbox')).toBeChecked();
    expect(screen.getByTestId('picked')).toHaveTextContent('已选 1');
  });

  it('★ 建不出格子的店铺点名（阶段 A 后端恒空 ⇒ 这里用注入数据把这条分支跑一遍）', async () => {
    await renderAdd();
    await search('SKU-1');
    const box = await screen.findByTestId('unbuildable');
    expect(box).toHaveTextContent('30112');
    expect(box).toHaveTextContent('no_channel_code');
  });

  it('添加：成功与被拒各自逐条列出，两个数都给', async () => {
    await renderAdd({ failOn: 'MSKU-W' });
    await search('SKU-1');
    // 选中货号即默认勾上可认领的两个（A、W）
    await screen.findByTestId('msku-MSKU-A-11072');
    await userEvent.click(screen.getByRole('button', { name: '添加' }));

    const report = await screen.findByTestId('claim-report');
    expect(report).toHaveTextContent('已添加 1');
    expect(report).toHaveTextContent('被拒 1');
    // ★ 被拒的那一条要说出是被谁占着 —— 只说「被拒 1」等于没说
    expect(within(report).getByText(/2026 Q3 补货计划/)).toBeInTheDocument();
    expect(within(report).getAllByRole('listitem')).toHaveLength(2);
  });

  it('★ 一条被拒不阻断其余 —— 修一条报一条，人就开始绕（原则五）', async () => {
    await renderAdd({ failOn: 'MSKU-A' });
    await search('SKU-1');
    // 选中货号即默认勾上可认领的两个（A、W）
    await screen.findByTestId('msku-MSKU-A-11072');
    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    const report = await screen.findByTestId('claim-report');
    expect(report).toHaveTextContent('已添加 1');
    expect(report).toHaveTextContent('被拒 1');
  });

  it('★ 409 的 claimed_by 为 null 时只给 hint，不许现造一个占用方', async () => {
    await renderAdd({ failNoHolder: 'MSKU-A' });
    await search('SKU-1');
    await screen.findByTestId('msku-MSKU-A-11072');
    await userEvent.click(screen.getByRole('button', { name: '添加' }));

    const report = await screen.findByTestId('claim-report');
    expect(report).toHaveTextContent('被拒 1');
    expect(within(report).getByText('MSKU-A 已被占用')).toBeInTheDocument();
    expect(within(report).queryByText('计划')).toBeNull();
    expect(within(report).queryByText('操作人')).toBeNull();
  });

  // ★ Ruling 2（team-lead 2026-09-22）：没有销售历史 ≠ 预估 0（api/ui/plans.py:163-170 的
  // no_history 就是为点名这件事而生）。认领成功之后必须把它交代清楚：一句 toast + 行内标记，
  // 且标记要留到本次会话结束 —— 不是闪一下就没了，运营翻页回来还得看得见。
  it('★ 没有销售历史的 msku 认领成功后要点名：toast 报数 + 行内标记留到本次会话结束', async () => {
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        searchCatalog: async (q: { q?: string }) =>
          !q.q ? { need_query: true, truncated: false, limit: 50, items: [] }
               : JSON.parse(JSON.stringify(CATALOG)) as CatalogResult,
        recentCatalog: async () => EMPTY_RECENT,
        // ★ 形状照抄 api/ui/plans.py:169-170：{seller_sku, sid, reason: 'no_sales_history'}
        claim: async (_p: number, t: { seller_sku: string; sid: string }) => ({
          claimed: { seller_sku: t.seller_sku, sid: t.sid, sku: 'SKU-1' },
          seeded: { demand_cells: 3, purchase_cells: 3 },
          no_history: t.seller_sku === 'MSKU-A'
            ? [{ seller_sku: 'MSKU-A', sid: t.sid, reason: 'no_sales_history' }]
            : [],
        }),
      },
    }));
    const { PlanAdd } = await import('./PlanAdd');
    render(
      <MemoryRouter initialEntries={['/plans/1/add']}>
        <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes>
      </MemoryRouter>,
    );
    await search('SKU-1');
    // 选中货号即默认勾上可认领的两个（A、W）
    await screen.findByTestId('msku-MSKU-A-11072');
    await userEvent.click(screen.getByRole('button', { name: '添加' }));

    await screen.findByTestId('claim-report');
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('已添加 2 个 msku');
    expect(status).toHaveTextContent('1 个没有销售历史，系统预估为空，需要人填');
    // ★ 页面文案不许出现西式中点 —— 这正是 team-lead 的裁定
    expect(status.textContent).not.toContain('·');

    expect(await screen.findByTestId('no-history-MSKU-A-11072')).toBeInTheDocument();
    expect(screen.queryByTestId('no-history-MSKU-W-90001')).toBeNull();

    // 标记要留到会话结束：重新搜索刷新结果之后仍然看得见
    await search('SKU-1');
    expect(await screen.findByTestId('no-history-MSKU-A-11072')).toBeInTheDocument();
  });

  // ★ Finding 1（review）：add() 在认领循环跑完之前 picked 不清空，按钮也没有「在飞」态 ——
  // 双击会用同一份 picked 快照并发跑两次 add()，向同一批 msku 发出两倍的 claim 请求。
  // 用同步的 fireEvent.click 连打两次（不像 userEvent 那样在两次点击之间等待微任务落定），
  // 逼出这条竞态：真正的护栏必须在 add() 入口同步早退，不能只靠 UI 层"看起来"禁用了。
  it('★ 添加在飞时禁用并防重入：双击只产生一份认领批次', async () => {
    const { ApiError } = await import('../api/client');
    const claimCalls: { seller_sku: string; sid: string }[] = [];
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        searchCatalog: async (q: { q?: string }) =>
          !q.q ? { need_query: true, truncated: false, limit: 50, items: [] }
               : JSON.parse(JSON.stringify(CATALOG)) as CatalogResult,
        recentCatalog: async () => EMPTY_RECENT,
        claim: async (_p: number, t: { seller_sku: string; sid: string }) => {
          claimCalls.push({ seller_sku: t.seller_sku, sid: t.sid });
          return { claimed: { seller_sku: t.seller_sku, sid: t.sid, sku: 'SKU-1' },
                   seeded: { demand_cells: 0, purchase_cells: 0 }, no_history: [] };
        },
      },
    }));
    const { PlanAdd } = await import('./PlanAdd');
    render(
      <MemoryRouter initialEntries={['/plans/1/add']}>
        <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes>
      </MemoryRouter>,
    );
    await search('SKU-1');
    // 选中货号即默认勾上可认领的两个（A、W）
    await screen.findByTestId('msku-MSKU-A-11072');

    const button = screen.getByRole('button', { name: '添加' });
    fireEvent.click(button);
    fireEvent.click(button);

    await screen.findByTestId('claim-report');
    // ★ 两个 msku、一次批次 —— 不是两个 msku × 两次点击 = 四次
    expect(claimCalls).toHaveLength(2);
    expect(screen.getAllByTestId('claim-report')).toHaveLength(1);
  });

  // ★ Finding 3（review）：brief fixture 里 SKU-1 的三个 msku 互不同名（MSKU-A/C/W），
  // 从未出现"同一 seller_sku、不同 sid"这个 CLAUDE.md 明确点名的真实场景（同一 msku 字符串
  // 在不同店铺下是不同 listing）。这里单独造一份 fixture 补上，钉住 key() 真的按 sid 分开。
  it('★ 同一 seller_sku 在不同 sid 下是不同 listing：勾一行不影响另一行的认领', async () => {
    const DUAL_STORE: CatalogResult = {
      need_query: false, truncated: false, limit: 50,
      items: [
        { sku: 'SKU-9', name: '猫抓板',
          mskus: [
            { seller_sku: 'MSKU-DUAL', sid: '11072', seller_name: 'A4Pet-US', selectable: true, claimed_by: null },
            { seller_sku: 'MSKU-DUAL', sid: '11094', seller_name: 'A4Pet-BS-UK', selectable: true, claimed_by: null },
          ],
          unbuildable_sellers: [], claimed_by: null, claimed_by_plans: [] },
      ],
    };
    const { ApiError } = await import('../api/client');
    const claimCalls: { seller_sku: string; sid: string }[] = [];
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        searchCatalog: async (q: { q?: string }) =>
          !q.q ? { need_query: true, truncated: false, limit: 50, items: [] }
               : JSON.parse(JSON.stringify(DUAL_STORE)) as CatalogResult,
        recentCatalog: async () => EMPTY_RECENT,
        claim: async (_p: number, t: { seller_sku: string; sid: string }) => {
          claimCalls.push({ seller_sku: t.seller_sku, sid: t.sid });
          return { claimed: { seller_sku: t.seller_sku, sid: t.sid, sku: 'SKU-9' },
                   seeded: { demand_cells: 0, purchase_cells: 0 }, no_history: [] };
        },
      },
    }));
    const { PlanAdd } = await import('./PlanAdd');
    render(
      <MemoryRouter initialEntries={['/plans/1/add']}>
        <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes>
      </MemoryRouter>,
    );
    await search('SKU-9');

    // ★ 两行共用同一个 seller_sku，但 testid 带 sid ⇒ 各自有独立、可精确定位的身份
    const rowA = await screen.findByTestId('msku-MSKU-DUAL-11072');
    const rowB = screen.getByTestId('msku-MSKU-DUAL-11094');
    await userEvent.click(within(rowB).getByRole('checkbox'));
    expect(within(rowA).getByRole('checkbox')).toBeChecked();

    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    await screen.findByTestId('claim-report');

    expect(claimCalls).toHaveLength(1);
    expect(claimCalls[0]).toEqual({ seller_sku: 'MSKU-DUAL', sid: '11072' });
  });

  // ★ Finding（review「其它核对」）：search() 的 catch 只设置 err state，result 保持不变，
  // 理论上不会回退成"查不到"；但此前没有测试真的让 searchCatalog 抛错去验证这条路径。
  it('★ 搜索报错走 ErrorDetail，不回退成「查不到」', async () => {
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        searchCatalog: async () => {
          throw new ApiError(503, 'mirror_stale', '目录镜像过期，稍后重试');
        },
        recentCatalog: async () => EMPTY_RECENT,
        claim: async () => { throw new Error('本用例不应调用 claim'); },
      },
    }));
    const { PlanAdd } = await import('./PlanAdd');
    render(
      <MemoryRouter initialEntries={['/plans/1/add']}>
        <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes>
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByRole('combobox', { name: '搜货号' }), 'SKU-1');

    expect(await screen.findByText('目录镜像过期，稍后重试')).toBeInTheDocument();
    expect(screen.getByText('503 mirror_stale')).toBeInTheDocument();
    // ★ 报错 ≠ 查不到：不许把请求失败悄悄显示成「没有命中的货号」
    expect(screen.queryByTestId('no-hit')).toBeNull();
  });
});

describe('输入提示', () => {
  const RECENT = {
    items: [{ ...JSON.parse(JSON.stringify(CATALOG.items[1])), last_claimed_at: '2026-10-14T08:00:00+00:00' },
            { ...JSON.parse(JSON.stringify(CATALOG.items[0])), last_claimed_at: '2026-10-01T08:00:00+00:00' }],
    limit: 50, truncated: false, unmapped_claims: 0,
  };

  it('空输入聚焦 → 列以前计划里用过的，按后端给的顺序', async () => {
    await renderAdd({ recent: RECENT });
    await userEvent.click(screen.getByRole('combobox', { name: '搜货号' }));
    const list = await screen.findByRole('listbox', { name: '以前计划里用过的' });
    expect(within(list).getAllByRole('option').map((o) => o.textContent)).toEqual([
      expect.stringContaining('SKU-2'), expect.stringContaining('SKU-1'),
    ]);
    // 可认领数 / 总数：SKU-1 三个 msku 里一个被占
    expect(within(list).getAllByRole('option')[1]).toHaveTextContent('可认领 2/3');
  });

  it('★ 没有用过的货号要说出来，不是一个空下拉', async () => {
    await renderAdd();
    await userEvent.click(screen.getByRole('combobox', { name: '搜货号' }));
    expect(await screen.findByTestId('recent-empty'))
      .toHaveTextContent('还没有计划用过的货号，输入货号、品名或 msku 搜索');
  });

  it('★ 对不上货号的认领记录要点数', async () => {
    await renderAdd({ recent: { ...RECENT, unmapped_claims: 3 } });
    await userEvent.click(screen.getByRole('combobox', { name: '搜货号' }));
    expect(await screen.findByTestId('recent-unmapped')).toHaveTextContent('另有 3 条认领记录对不上货号');
  });

  it('全部被占用的货号照样列出，写「已被占用」', async () => {
    const held = JSON.parse(JSON.stringify(CATALOG.items[0]));
    held.mskus.forEach((m: { selectable: boolean }) => { m.selectable = false; });
    await renderAdd({ recent: { ...RECENT, items: [{ ...held, last_claimed_at: '2026-10-01T00:00:00Z' }] } });
    await userEvent.click(screen.getByRole('combobox', { name: '搜货号' }));
    expect(await screen.findByRole('option', { name: /SKU-1/ })).toHaveTextContent('已被占用');
  });

  it('键盘：↓ 选中第二个，Enter 加入，输入框清空；Esc 关下拉', async () => {
    await renderAdd({ recent: RECENT });
    const box = screen.getByRole('combobox', { name: '搜货号' });
    await userEvent.type(box, 'SKU');
    // ★ 限定在本下拉里找：顶栏「操作人」<select> 的 option 也是 option
    const list = await screen.findByRole('listbox', { name: '命中的货号' });
    await userEvent.keyboard('{ArrowDown}{ArrowDown}');
    expect(within(list).getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{Enter}');
    expect(await screen.findByTestId('chosen-SKU-2')).toBeInTheDocument();
    expect(box).toHaveValue('');
    await userEvent.type(box, 'SKU');
    await screen.findByRole('listbox', { name: '命中的货号' });
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('已选的货号在下拉里标「已选」，再选一次不重复', async () => {
    await renderAdd();
    await search('SKU', 'SKU-1');
    await search('SKU', 'SKU-1');
    expect(screen.getAllByTestId('chosen-SKU-1')).toHaveLength(1);
    await userEvent.type(screen.getByRole('combobox', { name: '搜货号' }), 'SKU');
    expect(await screen.findByRole('option', { name: /SKU-1/ })).toHaveTextContent('已选');
  });

  it('反选：跨所有已选货号翻转，被占用的不参与', async () => {
    await renderAdd();
    await search('SKU', 'SKU-1');
    await search('SKU', 'SKU-2');
    await userEvent.click(within(screen.getByTestId('msku-MSKU-A-11072')).getByRole('checkbox'));
    expect(screen.getByTestId('picked')).toHaveTextContent('已选 2');   // W、B
    await userEvent.click(screen.getByRole('button', { name: '反选' }));
    expect(screen.getByTestId('picked')).toHaveTextContent('已选 1');   // 只剩 A
    expect(within(screen.getByTestId('msku-MSKU-A-11072')).getByRole('checkbox')).toBeChecked();
    expect(within(screen.getByTestId('msku-MSKU-W-90001')).getByRole('checkbox')).not.toBeChecked();
    expect(within(screen.getByTestId('msku-MSKU-B-11072')).getByRole('checkbox')).not.toBeChecked();
    expect(within(screen.getByTestId('msku-MSKU-C-11094')).getByRole('checkbox')).not.toBeChecked();
  });

  it('还没选货号时反选不可点', async () => {
    await renderAdd();
    expect(screen.getByRole('button', { name: '反选' })).toBeDisabled();
  });

  it('移出已选货号：它的勾选一起去掉', async () => {
    await renderAdd();
    await search('SKU', 'SKU-1');
    expect(screen.getByTestId('picked')).toHaveTextContent('已选 2');
    await userEvent.click(within(screen.getByTestId('chosen-SKU-1')).getByRole('button', { name: '不选这个货号' }));
    expect(screen.queryByTestId('chosen-SKU-1')).toBeNull();
    expect(screen.getByTestId('picked')).toHaveTextContent('已选 0');
  });

  it('命中超过下拉上限 → 写命中数，让人继续输入', async () => {
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        searchCatalog: async (q: { q?: string; limit?: number }) =>
          ({ ...JSON.parse(JSON.stringify(CATALOG)), truncated: true, matched: 87, limit: q.limit }),
        recentCatalog: async () => EMPTY_RECENT,
        claim: async () => { throw new Error('本用例不应调用 claim'); },
      },
    }));
    const { PlanAdd } = await import('./PlanAdd');
    render(<MemoryRouter initialEntries={['/plans/1/add']}>
      <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes></MemoryRouter>);
    await userEvent.type(screen.getByRole('combobox', { name: '搜货号' }), 'S');
    expect(await screen.findByTestId('suggest-truncated')).toHaveTextContent('命中 87 个，只列前 20，继续输入缩小范围');
  });

  it('★ 先发的请求后回来，不许覆盖后发的结果', async () => {
    const { ApiError } = await import('../api/client');
    let releaseSlow: () => void = () => {};
    const slow = new Promise<void>((r) => { releaseSlow = r; });
    const asked: string[] = [];
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        searchCatalog: async (q: { q?: string }) => {
          asked.push(q.q ?? '');
          const one = (i: number) => ({ need_query: false, truncated: false, limit: 20,
            items: [JSON.parse(JSON.stringify(CATALOG.items[i]))] });
          if (q.q === 'S') { await slow; return one(0); }
          return one(1);
        },
        recentCatalog: async () => EMPTY_RECENT,
        claim: async () => { throw new Error('本用例不应调用 claim'); },
      },
    }));
    const { PlanAdd } = await import('./PlanAdd');
    render(<MemoryRouter initialEntries={['/plans/1/add']}>
      <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes></MemoryRouter>);
    const box = screen.getByRole('combobox', { name: '搜货号' });
    await userEvent.type(box, 'S');
    await waitFor(() => expect(asked).toContain('S'));
    await userEvent.type(box, 'K');
    expect(await screen.findByRole('option', { name: /SKU-2/ })).toBeInTheDocument();
    releaseSlow();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole('option', { name: /SKU-1/ })).toBeNull();
    expect(screen.getByRole('option', { name: /SKU-2/ })).toBeInTheDocument();
  });
});
