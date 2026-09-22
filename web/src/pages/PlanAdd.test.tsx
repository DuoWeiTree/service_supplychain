import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
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

async function renderAdd(opts: { failOn?: string } = {}) {
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
          throw new ApiError(409, 'msku_already_claimed', `${t.seller_sku} 已被占用`,
            { plan_id: 2, title: '2026 Q3 补货计划', actor: 'ops.li' });
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
    expect(report).toHaveTextContent('成功 1');
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
    expect(report).toHaveTextContent('成功 1');
    expect(report).toHaveTextContent('被拒 1');
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
    expect(status).toHaveTextContent('已加入 2 个 msku');
    expect(status).toHaveTextContent('1 个没有销售历史，系统预估为空，需要人填');
    // ★ 页面文案不许出现西式中点 —— 这正是 team-lead 的裁定
    expect(status.textContent).not.toContain('·');

    expect(await screen.findByTestId('no-history-MSKU-A')).toBeInTheDocument();
    expect(screen.queryByTestId('no-history-MSKU-W')).toBeNull();

    // 标记要留到会话结束：重新搜索刷新结果之后仍然看得见
    await search('SKU-1');
    expect(await screen.findByTestId('no-history-MSKU-A')).toBeInTheDocument();
  });
});
