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
             effective_units: 100, basis: 'human', system_basis: null }],
  purchase: [{ sku: 'SKU-1', period: '2026-10', planned_units: 500 },
             { sku: 'SKU-1', period: '2026-11', planned_units: null },
             { sku: 'SKU-1', period: '2026-12', planned_units: null }],
  params: { as_of: '2026-09-21', route: 'oversea_sea', make_days: 30, ship_days: 60,
            anchor_day: 15, embargo_days: 7, in_transit_as_of: '2026-09-21' },
  forecast: [],
  forecast_msku: [],
  forecast_store: ['2026-10', '2026-11', '2026-12'].map((period, n) => ({
    sku: 'SKU-1', sid: '11072', period,
    state: n === 0 ? 'ok' as const : 'unknown' as const,
    reason: n === 0 ? null : 'unknown_demand' as const,
    demand: n === 0 ? 100 : null, system: null, system_not_applicable: false,
    opening: n === 0 ? 300 : 200, inbound_planned: 0, closing: n === 0 ? 200 : null,
  })),
  forecast_sku: [],
  beyond_horizon: [],
  unallocated: [],
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
      releaseClaim: async () => ({ released: { seller_sku: '', sid: '' }, dropped_cells: [], dropped_purchase_cells: [] }),
      submit,
      // ★ Task 20：PlanGrid 挂载即调 getLeadTime（LeadTimeBar）—— 与本测试无关，给固定值
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

const none = { count: 0, lines: [] };
const result = (over: Partial<SubmitResult>): SubmitResult => ({
  rev: 1, lines: 1, in_flight: true, content_digest: 'abc', skipped: [],
  kept: none, replaced: none, added: { count: 1, lines: [{ sku: 'SKU-1', period: '2026-10', line_id: 11, total_units: 500 }] },
  removed: none, split_frozen: none, left_completed: none,
  ...over,
});

const OK = result({
  skipped: [{ sku: 'SKU-1', period: '2026-11', reason: 'empty_purchase' },
            { sku: 'SKU-1', period: '2026-12', reason: 'empty_purchase' }],
});

async function submitWith(r: SubmitResult) {
  await renderGrid(async () => r);
  await screen.findByTestId('plan-sheet');
  await userEvent.click(screen.getByRole('button', { name: '提交' }));
  return screen.findByTestId('submit-report');
}

describe('提交', () => {
  it('★ R1：采购量为空 与 采购量为 0 各一行灰字计数，明细折叠；红色只给 no_claimed_msku', async () => {
    const report = await submitWith(result({
      skipped: [{ sku: 'SKU-1', period: '2026-11', reason: 'empty_purchase' },
                { sku: 'SKU-1', period: '2026-12', reason: 'empty_purchase' },
                { sku: 'SKU-2', period: '2026-10', reason: 'zero_purchase' },
                { sku: 'SKU-3', period: '2026-10', reason: 'no_claimed_msku' }],
    }));
    const empty = within(report).getByTestId('skip-empty_purchase');
    const zero = within(report).getByTestId('skip-zero_purchase');
    expect(empty).toHaveTextContent('采购量为空 2 个');
    expect(zero).toHaveTextContent('采购量为 0 1 个');
    // ★ 空 ≠ 0：两行各自成数，不许合成一行
    expect(empty).not.toHaveTextContent('采购量为 0');
    for (const el of [empty, zero]) {
      expect(el.closest('.flash--bad, .dropline')).toBeNull();
      const fold = el.querySelector('details')!;
      expect(fold).not.toBeNull();
      expect(fold.open).toBe(false);
    }
    expect(within(empty).getByText('2026-11')).toBeInTheDocument();
    expect(within(zero).getByText('SKU-2')).toBeInTheDocument();

    // 红色：只有铸不出的那一条
    const red = within(report).getByTestId('skip-no_claimed_msku');
    expect(red.querySelectorAll('.dropline')).toHaveLength(1);
    expect(red).toHaveTextContent('SKU-3');
    expect(report.querySelectorAll('.dropline')).toHaveLength(1);
    expect(document.body.textContent).not.toContain('空或');
  });

  it('★ R1：没有铸不出的格子时不出红块、toast 不是警告', async () => {
    const report = await submitWith(OK);
    expect(within(report).queryByTestId('skip-no_claimed_msku')).toBeNull();
    expect(within(report).queryByTestId('skip-zero_purchase')).toBeNull();
    expect(report.querySelector('.dropline, .flash--bad')).toBeNull();
    const toast = await screen.findByRole('status');
    await waitFor(() => expect(toast.querySelector('.toast--ok')).not.toBeNull());
    expect(toast.querySelector('.toast--warn')).toBeNull();
  });

  it('★ D5：保留 替换 新增 去掉 四个数各自成列；替换与去掉的明细折叠', async () => {
    const report = await submitWith(result({
      rev: 3, lines: 3,
      kept: { count: 1, lines: [{ sku: 'SKU-1', period: '2026-10', line_id: 7, state: '已提交', total_units: 200 }] },
      replaced: { count: 1, lines: [{ sku: 'SKU-2', period: '2026-10', old_line_id: 8, new_line_id: 21,
        total_units: { from: 150, to: 180 }, demand_at_submit: { from: 90, to: 90 } }] },
      added: { count: 1, lines: [{ sku: 'SKU-4', period: '2026-11', line_id: 22, total_units: 60 }] },
      removed: { count: 2, lines: [{ sku: 'SKU-5', period: '2026-10', line_id: 9, total_units: 40 },
                                   { sku: 'SKU-6', period: '2026-10', line_id: 10, total_units: 30 }] },
    }));
    expect(within(report).getByTestId('count-kept')).toHaveTextContent('保留 1');
    expect(within(report).getByTestId('count-replaced')).toHaveTextContent('替换 1');
    expect(within(report).getByTestId('count-added')).toHaveTextContent('新增 1');
    expect(within(report).getByTestId('count-removed')).toHaveTextContent('去掉 2');
    expect(report.textContent).not.toContain(' · ');

    const rep = within(report).getByTestId('replaced-detail');
    expect(rep.tagName).toBe('DETAILS');
    expect((rep as HTMLDetailsElement).open).toBe(false);
    expect(rep).toHaveTextContent('SKU-2');
    expect(rep).toHaveTextContent('150 → 180');
    const rm = within(report).getByTestId('removed-detail');
    expect(rm.tagName).toBe('DETAILS');
    expect(within(rm).getAllByRole('listitem')).toHaveLength(2);
    // 没有的就不出折叠：空折叠是噪音
    expect(within(report).queryByTestId('split_frozen-detail')).toBeNull();
    expect(within(report).queryByTestId('left_completed-detail')).toBeNull();
  });

  it('★ 裁定 A/B：split_frozen 逐店列旧拆分 → 新拆分；left_completed 点名并折叠', async () => {
    const report = await submitWith(result({
      kept: { count: 1, lines: [{ sku: 'SKU-1', period: '2026-10', line_id: 7, state: '已确认', total_units: 200 }] },
      split_frozen: { count: 1, lines: [{ sku: 'SKU-1', period: '2026-10', line_id: 7,
        split: { from: { '11072': 120, '11094': 80 }, to: { '11072': 100, '11094': null } } }] },
      left_completed: { count: 1, lines: [{ sku: 'SKU-9', period: '2026-09', line_id: 3, rev: 1, total_units: 70 }] },
    }));
    const sf = within(report).getByTestId('split_frozen');
    expect(sf).toHaveTextContent('1');
    expect(sf.closest('.flash--bad')).toBeNull();
    const sfd = within(report).getByTestId('split_frozen-detail');
    expect((sfd as HTMLDetailsElement).open).toBe(false);
    expect(sfd).toHaveTextContent('A4Pet-US 120 → 100');
    // ★ 新拆分里这家店没数：说「未知」，不是 0
    expect(sfd).toHaveTextContent('11094 80 → 未知');

    const lc = within(report).getByTestId('left_completed-detail');
    expect(lc).toHaveTextContent('SKU-9');
    expect(lc).toHaveTextContent('rev 1');
  });

  it('★ 结果页给去采购的路；N 从响应推不准，只给「去采购」', async () => {
    const report = await submitWith(OK);
    expect(within(report).getByRole('link', { name: '去采购' })).toHaveAttribute('href', '/purchase');
    expect(within(report).getByRole('link', { name: '去版本' })).toHaveAttribute('href', '/plans/1/revs');
  });

  it('★ 空版本要说出来：这一版 0 条，且没有占在流转位', async () => {
    const report = await submitWith(result({
      lines: 0, in_flight: false, added: none,
      skipped: [{ sku: 'SKU-1', period: '2026-10', reason: 'zero_purchase' },
                { sku: 'SKU-1', period: '2026-11', reason: 'empty_purchase' },
                { sku: 'SKU-1', period: '2026-12', reason: 'no_claimed_msku' }],
    }));
    expect(screen.getByTestId('empty-rev')).toHaveTextContent('没有占在流转位');
    expect(report).toHaveTextContent('这一版 0 条');
    // ★ 三种理由都要出现，不许只报第一种
    expect(within(report).getByTestId('skip-zero_purchase')).toBeInTheDocument();
    expect(within(report).getByTestId('skip-empty_purchase')).toBeInTheDocument();
    expect(within(report).getByTestId('skip-no_claimed_msku')).toBeInTheDocument();
  });

  it('★ 409 lines_in_purchase：逐条点名，采购单号可点，处置按条说', async () => {
    const { ApiError } = await import('../api/client');
    await renderGrid(async () => {
      throw new ApiError(409, 'lines_in_purchase', '要改动的记录已进采购：先从采购单里移掉，或保持该货号·月不变', {
        plan_id: 1,
        lines: [
          { sku: 'SKU-1', period: '2026-10', line_id: 7, state: '已提交', change: 'replace', po_ids: [5, 6] },
          { sku: 'SKU-2', period: '2026-11', line_id: 8, state: '已确认', change: 'remove', po_ids: [] },
          { sku: 'SKU-3', period: '2026-12', line_id: 9, state: '已完结', change: 'replace', po_ids: [4] },
        ],
      });
    });
    await screen.findByTestId('plan-sheet');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    const box = await screen.findByTestId('lines-in-purchase');
    expect(box).toHaveTextContent('409 lines_in_purchase');
    const rows = within(box).getAllByRole('listitem');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('SKU-1');
    expect(rows[0]).toHaveTextContent('2026-10');
    expect(rows[0]).toHaveTextContent('已提交');
    expect(rows[0]).toHaveTextContent('改了量');
    expect(within(rows[0]!).getByRole('link', { name: '采购单 5' })).toHaveAttribute('href', '/purchase/5');
    expect(within(rows[0]!).getByRole('link', { name: '采购单 6' })).toHaveAttribute('href', '/purchase/6');
    expect(rows[0]).toHaveTextContent('改回原来的量，或先从采购单里移掉这条记录');
    expect(rows[1]).toHaveTextContent('不再包含');
    expect(rows[1]).toHaveTextContent('把采购量填回原来的数');
    expect(rows[1]).not.toHaveTextContent('移掉');
    // ★ 已完结：从采购单移掉不适用
    expect(rows[2]).toHaveTextContent('已完结');
    expect(rows[2]).not.toHaveTextContent('移掉');
    expect(rows[2]).toHaveTextContent('改回原来的量');
    expect(screen.queryByTestId('submit-report')).toBeNull();
    expect(box.textContent).not.toContain(' · ');
  });

  it('★ R2：再提交不再被「先撤销」拦 —— 409 rev_in_flight 不再给「去看那一版」', async () => {
    const { ApiError } = await import('../api/client');
    await renderGrid(async () => {
      throw new ApiError(409, 'rev_in_flight', '同一版号已被并发提交占用', {});
    });
    await screen.findByTestId('plan-sheet');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText('409 rev_in_flight')).toBeInTheDocument();
    expect(screen.queryByTestId('rev-in-flight')).toBeNull();
    expect(screen.queryByText(/还在流转/)).toBeNull();
    expect(screen.queryByRole('link', { name: /去看 rev/ })).toBeNull();
  });

  it('★ 400 落到表单错误区', async () => {
    const { ApiError } = await import('../api/client');
    await renderGrid(async () => {
      throw new ApiError(400, 'bad_request', '起始月必须是月初', { period_start: '2026-10-15' });
    });
    await screen.findByTestId('plan-sheet');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText('400 bad_request')).toBeInTheDocument();
    expect(screen.queryByTestId('lines-in-purchase')).toBeNull();
  });

  it('★ 双击提交只发一次请求 —— 在飞时按钮置灰（Ruling C 护栏）', async () => {
    let resolveSubmit!: (v: SubmitResult) => void;
    const submit = vi.fn(() => new Promise<SubmitResult>((resolve) => { resolveSubmit = resolve; }));
    await renderGrid(submit);
    await screen.findByTestId('plan-sheet');
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
