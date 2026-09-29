import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { GridResponse, PlanSummary, RecomputeResult, Seller } from '../api/types';

const PLAN: PlanSummary = {
  plan_id: 1, title: '2026 Q4 销售计划', period_start: '2026-10-01', months: 3,
  owner_actor: 'ops.zhang', archived_at: null, state: null, state_rev: null,
};
const SELLERS: Seller[] = [
  { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
];
const GRID: GridResponse = {
  plan_id: 1, periods: ['2026-10', '2026-11', '2026-12'],
  demand: [{ seller_sku: 'MSKU-A', sid: '11072', sku: 'SKU-1', period: '2026-10',
             system_units: null, system_extrapolated: false, expected_units: null,
             effective_units: null, basis: 'unknown', system_basis: null }],
  purchase: [{ sku: 'SKU-1', period: '2026-10', planned_units: 500 }],
  params: { as_of: '2026-09-21', route: 'oversea_sea', make_days: 30, ship_days: 60,
            anchor_day: 15, embargo_days: 7, in_transit_as_of: '2026-09-21' },
  forecast: [],
  forecast_msku: [],
  forecast_store: [],
  forecast_sku: [],
  beyond_horizon: [],
  unallocated: [],
};

beforeEach(() => { vi.resetModules(); });

async function renderGrid(recomputeForecast: (...args: unknown[]) => Promise<RecomputeResult>) {
  const { ApiError } = await import('../api/client');
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      getPlan: async () => PLAN,
      listSellers: async () => SELLERS,
      getGrid: async () => JSON.parse(JSON.stringify(GRID)) as GridResponse,
      putDemand: async () => GRID.demand[0]!,
      putPurchase: async () => GRID.purchase[0]!,
      releaseClaim: async () => ({ released: { seller_sku: '', sid: '' }, dropped_cells: [], dropped_purchase_cells: [] }),
      submit: async () => ({ rev: 1, lines: 0, in_flight: false, content_digest: 'x', skipped: [] }),
      recomputeForecast,
      getLeadTime: async () => ({ route: 'oversea_sea', make_days: 30, ship_days: 60, anchor_day: 15,
        valid_from: { making: '2026-01-01', shipping: '2026-01-01' } }),
    },
  }));
  const { PlanGrid } = await import('./PlanGrid');
  return render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
}

describe('重算预测', () => {
  it('★ 成功提示点名「N 格更新 · M 格算不出速率」，不是「重算完成」', async () => {
    const result: RecomputeResult = {
      recomputed: [{ seller_sku: 'MSKU-A', sid: '11072', period: '2026-10', before: null, after: 130 }],
      unchanged: 2,
      no_rate: [],
    };
    await renderGrid(async () => result);
    await screen.findByTestId('plan-sheet');
    await userEvent.click(screen.getByRole('button', { name: '重算预测' }));

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('1 格更新');
    expect(status).toHaveTextContent('0 个 msku 没有速率');
    expect(status.textContent).not.toContain('重算完成');
  });

  it('★ 算不出速率的 msku 逐条点名，不是合成一个数字', async () => {
    const result: RecomputeResult = {
      recomputed: [],
      unchanged: 3,
      no_rate: [{ seller_sku: 'MSKU-A', sid: '11072', reason: 'no_sales_history' }],
    };
    await renderGrid(async () => result);
    await screen.findByTestId('plan-sheet');
    await userEvent.click(screen.getByRole('button', { name: '重算预测' }));

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('0 格更新');
    expect(status).toHaveTextContent('1 个 msku 没有速率');

    const panel = await screen.findByTestId('recompute-no-rate');
    expect(panel).toHaveTextContent('MSKU-A');
    expect(panel).toHaveTextContent('11072');
    expect(panel).toHaveTextContent('还没有销量历史');
    expect(panel.textContent).not.toContain('no_sales_history');  // ★ reason 码是给程序看的，不给运营看
  });

  it('★ 负责人 09-29：没有销量历史不出警告色 —— 重算是成功的，那只是数据的形状', async () => {
    const result: RecomputeResult = {
      recomputed: [{ seller_sku: 'MSKU-B', sid: '11072', period: '2026-10', before: null, after: 31 }],
      unchanged: 0,
      no_rate: [
        { seller_sku: 'MSKU-A', sid: '11072', reason: 'no_sales_history' },
        { seller_sku: 'MSKU-C', sid: '11098', reason: 'not_applicable_no_sales_source',
          cause: 'store_absent_from_order_report' },
      ],
    };
    await renderGrid(async () => result);
    await screen.findByTestId('plan-sheet');
    await userEvent.click(screen.getByRole('button', { name: '重算预测' }));

    const status = await screen.findByRole('status');
    // ★ 判据是**类名**不是文案：警告色由 toast--warn 决定，断文案断不到颜色
    expect(status.querySelector('.toast--warn')).toBeNull();
    expect(status.querySelector('.toast--fail')).toBeNull();
    expect(status.querySelector('.toast--ok')).not.toBeNull();
    // ★ 不出警告 ≠ 不说话：计数与名单都还在
    expect(status).toHaveTextContent('2 个 msku 没有速率');
    const panel = await screen.findByTestId('recompute-no-rate');
    expect(panel).toHaveTextContent('还没有销量历史');
    expect(panel).toHaveTextContent('不适用：这家店在订单报表里没有数据');
  });

  it('★ 认不出的成因仍然要显眼 —— 降噪名单只用来降噪，不用来盖章', async () => {
    const result = {
      recomputed: [], unchanged: 0,
      // ★ 故意喂一个类型里还没有的 reason：形态识别必漏第 N+1 种
      no_rate: [{ seller_sku: 'MSKU-Z', sid: '11072', reason: 'some_new_reason_nobody_declared' }],
    } as unknown as RecomputeResult;
    await renderGrid(async () => result);
    await screen.findByTestId('plan-sheet');
    await userEvent.click(screen.getByRole('button', { name: '重算预测' }));

    const status = await screen.findByRole('status');
    expect(status.querySelector('.toast--warn')).not.toBeNull();
    const panel = await screen.findByTestId('recompute-no-rate');
    expect(panel).toHaveTextContent('认不出的成因：some_new_reason_nobody_declared');
  });

  it('★ 归档计划上的 409 原样上屏（ensure_writable 既有形状）', async () => {
    const { ApiError } = await import('../api/client');
    await renderGrid(async () => {
      throw new ApiError(409, 'plan_archived', '计划已归档，不接受写入', { plan_id: 1, archived_at: '2026-09-01' });
    });
    await screen.findByTestId('plan-sheet');
    await userEvent.click(screen.getByRole('button', { name: '重算预测' }));
    expect(await screen.findByText('409 plan_archived')).toBeInTheDocument();
  });

  it('★ 双击重算只发一次请求 —— 在飞时按钮置灰', async () => {
    let resolveRecompute!: (v: RecomputeResult) => void;
    const recomputeForecast = vi.fn(() => new Promise<RecomputeResult>((resolve) => { resolveRecompute = resolve; }));
    await renderGrid(recomputeForecast);
    await screen.findByTestId('plan-sheet');
    const btn = screen.getByRole('button', { name: '重算预测' });

    await userEvent.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());
    await userEvent.click(btn, { pointerEventsCheck: 0 });
    expect(recomputeForecast).toHaveBeenCalledTimes(1);

    resolveRecompute({ recomputed: [], unchanged: 0, no_rate: [] });
    await screen.findByRole('status');
    await waitFor(() => expect(btn).not.toBeDisabled());
  });
});
