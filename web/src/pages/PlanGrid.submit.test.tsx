import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { GridResponse, PlanSummary, Seller, SubmitResult } from '../api/types';

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
             system_units: 100, system_extrapolated: false, expected_units: 100,
             effective_units: 100, basis: 'human' }],
  purchase: [{ sku: 'SKU-1', period: '2026-10', planned_units: 500 },
             { sku: 'SKU-1', period: '2026-11', planned_units: null },
             { sku: 'SKU-1', period: '2026-12', planned_units: null }],
  inventory: ['2026-10', '2026-11', '2026-12'].map((period, n) => ({
    sku: 'SKU-1', sid: '11072', period,
    onhand: n === 0 ? 300 : 200, inbound: null as null, closing: n === 0 ? 200 : null,
    basis: { source: 'ch' as const, as_of: '2026-09-21', includes_plan_purchase: false as const,
             demand: n === 0 ? 100 : null, reason: 'no_seller_attribution' as const,
             closing_reason: n === 0 ? null : ('unknown_demand' as const),
             sku_level_in_transit: null, sources: [] },
  })),
  sku_pipeline: [],
};

beforeEach(() => { vi.resetModules(); });

async function renderGrid(submit: (...args: unknown[]) => Promise<SubmitResult>) {
  const { ApiError } = await import('../api/client');
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      getPlan: async () => PLAN,
      listSellers: async () => SELLERS,
      getGrid: async () => JSON.parse(JSON.stringify(GRID)) as GridResponse,
      putDemand: async () => GRID.demand[0]!,
      putPurchase: async () => GRID.purchase[0]!,
      releaseClaim: async () => ({ released: { seller_sku: '', sid: '' }, dropped_cells: [], stranded_purchase_cells: [] }),
      submit,
    },
  }));
  const { PlanGrid } = await import('./PlanGrid');
  return render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
}

const OK: SubmitResult = {
  rev: 1, lines: 1, in_flight: true, content_digest: 'abc',
  skipped: [{ sku: 'SKU-1', period: '2026-11', reason: 'zero_purchase' },
            { sku: 'SKU-1', period: '2026-12', reason: 'zero_purchase' }],
};

describe('提交', () => {
  it('★ 逐条列出被跳过的格子，并交代两边的数', async () => {
    await renderGrid(async () => OK);
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    const report = await screen.findByTestId('submit-report');
    expect(report).toHaveTextContent('rev 1');
    // ★ 铸出几条 + 跳过几条，两个数都在屏上 —— 只报一个等于藏起另一半
    expect(report).toHaveTextContent('铸出 1 条');
    expect(report).toHaveTextContent('跳过 2 条');

    const skipped = within(report).getByTestId('skipped-list');
    expect(within(skipped).getAllByRole('listitem')).toHaveLength(2);
    expect(skipped).toHaveTextContent('zero_purchase');
    expect(skipped).toHaveTextContent('2026-11');
    expect(skipped).toHaveTextContent('2026-12');
  });

  it('提交成功不自动跳走 —— skipped[] 只有这一次机会被看见', async () => {
    await renderGrid(async () => OK);
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    await screen.findByTestId('submit-report');
    expect(screen.getByRole('link', { name: '去版本' })).toHaveAttribute('href', '/plans/1/revs');
  });

  it('★ 空版本要说出来：铸出 0 条，且没有占在流转位', async () => {
    await renderGrid(async () => ({
      rev: 1, lines: 0, in_flight: false, content_digest: 'abc',
      skipped: [{ sku: 'SKU-1', period: '2026-10', reason: 'zero_purchase' },
                { sku: 'SKU-1', period: '2026-11', reason: 'zero_purchase' },
                { sku: 'SKU-1', period: '2026-12', reason: 'no_claimed_msku' }],
    }));
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    const empty = await screen.findByTestId('empty-rev');
    expect(empty).toHaveTextContent('没有占在流转位');
    expect(screen.getByTestId('submit-report')).toHaveTextContent('铸出 0 条');
    // ★ 两种跳过理由都要出现，不许只报第一种
    const skipped = screen.getByTestId('skipped-list');
    expect(skipped).toHaveTextContent('zero_purchase');
    expect(skipped).toHaveTextContent('no_claimed_msku');
  });

  it('★ 409 rev_in_flight 的面板点名旧版号，且给的下一步是去看那一版', async () => {
    const { ApiError } = await import('../api/client');
    await renderGrid(async () => {
      throw new ApiError(409, 'rev_in_flight', '先处理 rev 7', { in_flight_rev: 7 });
    });
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    const box = await screen.findByTestId('rev-in-flight');
    expect(box).toHaveTextContent('rev 7');
    expect(box).toHaveTextContent('409 rev_in_flight');
    // ★ 409 不是「你写错了」：下一步是去看那一版，不是改表单
    expect(within(box).getByRole('link', { name: '去看 rev 7' })).toHaveAttribute('href', '/plans/1/revs');
    expect(screen.queryByTestId('submit-report')).toBeNull();
  });

  it('★ 400 与 409 分支不同：400 落到表单错误区，不给「去看那一版」', async () => {
    const { ApiError } = await import('../api/client');
    await renderGrid(async () => {
      throw new ApiError(400, 'bad_request', '起始月必须是月初', { period_start: '2026-10-15' });
    });
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText('400 bad_request')).toBeInTheDocument();
    expect(screen.queryByTestId('rev-in-flight')).toBeNull();
  });

  it('★ 双击提交只发一次请求 —— 在飞时按钮置灰（Ruling C 护栏）', async () => {
    let resolveSubmit!: (v: SubmitResult) => void;
    const submit = vi.fn(() => new Promise<SubmitResult>((resolve) => { resolveSubmit = resolve; }));
    await renderGrid(submit);
    await screen.findByTestId('purchase-block');
    const btn = screen.getByRole('button', { name: '提交' });

    await userEvent.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());
    // ★ 飞行中再点一次：跳过 pointer-events 检查，绕开浏览器对 disabled 元素的天然限制
    await userEvent.click(btn, { pointerEventsCheck: 0 });
    expect(submit).toHaveBeenCalledTimes(1);

    resolveSubmit(OK);
    await screen.findByTestId('submit-report');
    await waitFor(() => expect(btn).not.toBeDisabled());
  });
});
