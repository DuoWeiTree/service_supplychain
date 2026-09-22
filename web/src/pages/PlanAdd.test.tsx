import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
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

async function renderAdd(opts: { failOn?: string; failNoHolder?: string } = {}) {
  const { ApiError } = await import('../api/client');
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      searchCatalog: async (q: { q?: string }) =>
        !q.q ? { need_query: true, truncated: false, limit: 50, items: [] }
             // ★ 故意不带 matched：判空不许依赖这个可选字段
             : q.q === 'ZZZZ' ? { need_query: false, truncated: false, limit: 50, items: [] }
             : JSON.parse(JSON.stringify(CATALOG)) as CatalogResult,
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

const search = async (q: string) => {
  await userEvent.type(screen.getByLabelText('搜货号'), q);
  await userEvent.click(screen.getByRole('button', { name: '搜索' }));
};

describe('批量添加', () => {
  it('★ 还没搜 ≠ 搜了没有：两种空屏的字不一样', async () => {
    await renderAdd();
    expect(await screen.findByTestId('need-query')).toBeInTheDocument();
    expect(screen.queryByTestId('no-hit')).toBeNull();

    await search('ZZZZ');
    expect(await screen.findByTestId('no-hit')).toBeInTheDocument();
    expect(screen.queryByTestId('need-query')).toBeNull();
  });

  it('★ 被占用的行留在表里标出来并点名占用方', async () => {
    await renderAdd();
    await search('SKU-1');
    const row = await screen.findByTestId('msku-MSKU-C');
    expect(row).toHaveClass('off');
    expect(within(row).getByText('2026 Q3 补货计划')).toBeInTheDocument();
    expect(within(row).getByText('ops.li')).toBeInTheDocument();
    expect(within(row).getByRole('checkbox')).toBeDisabled();
  });

  it('★ 认领是 msku 级：勾一个不会把同货号的另一个也勾上', async () => {
    await renderAdd();
    await search('SKU-1');
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A')).getByRole('checkbox'));
    expect(within(screen.getByTestId('msku-MSKU-W')).getByRole('checkbox')).not.toBeChecked();
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
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A')).getByRole('checkbox'));
    await userEvent.click(within(screen.getByTestId('msku-MSKU-W')).getByRole('checkbox'));
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
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A')).getByRole('checkbox'));
    await userEvent.click(within(screen.getByTestId('msku-MSKU-W')).getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    const report = await screen.findByTestId('claim-report');
    expect(report).toHaveTextContent('已添加 1');
    expect(report).toHaveTextContent('被拒 1');
  });

  it('★ 409 的 claimed_by 为 null 时只给 hint，不许现造一个占用方', async () => {
    await renderAdd({ failNoHolder: 'MSKU-A' });
    await search('SKU-1');
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A')).getByRole('checkbox'));
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
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A')).getByRole('checkbox'));
    await userEvent.click(within(screen.getByTestId('msku-MSKU-W')).getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: '添加' }));

    await screen.findByTestId('claim-report');
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('已添加 2 个 msku');
    expect(status).toHaveTextContent('1 个没有销售历史，系统预估为空，需要人填');
    // ★ 页面文案不许出现西式中点 —— 这正是 team-lead 的裁定
    expect(status.textContent).not.toContain('·');

    expect(await screen.findByTestId('no-history-MSKU-A')).toBeInTheDocument();
    expect(screen.queryByTestId('no-history-MSKU-W')).toBeNull();

    // 标记要留到会话结束：重新搜索刷新结果之后仍然看得见
    await search('SKU-1');
    expect(await screen.findByTestId('no-history-MSKU-A')).toBeInTheDocument();
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
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A')).getByRole('checkbox'));
    await userEvent.click(within(screen.getByTestId('msku-MSKU-W')).getByRole('checkbox'));

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

    // ★ 两行共用同一个 seller_sku，data-testid 因而也相同 —— 用 findAllByTestId 各自取出
    const rows = await screen.findAllByTestId('msku-MSKU-DUAL');
    expect(rows).toHaveLength(2);
    const [rowA, rowB] = rows as [HTMLElement, HTMLElement];
    await userEvent.click(within(rowA).getByRole('checkbox'));
    expect(within(rowB).getByRole('checkbox')).not.toBeChecked();

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
        claim: async () => { throw new Error('本用例不应调用 claim'); },
      },
    }));
    const { PlanAdd } = await import('./PlanAdd');
    render(
      <MemoryRouter initialEntries={['/plans/1/add']}>
        <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes>
      </MemoryRouter>,
    );
    await search('SKU-1');

    expect(await screen.findByText('目录镜像过期，稍后重试')).toBeInTheDocument();
    expect(screen.getByText('503 mirror_stale')).toBeInTheDocument();
    // ★ 报错 ≠ 查不到：不许把请求失败悄悄显示成「没有命中的货号」
    expect(screen.queryByTestId('no-hit')).toBeNull();
  });
});
