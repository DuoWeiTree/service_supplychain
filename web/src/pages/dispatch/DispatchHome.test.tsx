import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import list from '../../testing/fixtures/contract/sheet-list.json';
import plans from '../../testing/fixtures/contract/dispatch-plans.json';
import { ok, stubBackend, type Handler } from '../../testing/backendStub';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

function Sheet() { return <div>SHEET {useParams().sheetId}</div>; }

const PLAN = { plan_id: 5, title: '10 月计划', rev: 7, lines: 2, total: 380, confirmed: 140, ordered: 10,
  staged: 0, dispatched: 0, pending: 380, sheets: [], records: [] };

async function mount(r: Record<string, Handler>) {
  const calls = stubBackend({ 'GET /v1/attention': () => ok({ count: 0, items: [] }),
    'GET /v1/sheets': () => ok({ sheets: [] }), 'GET /v1/dispatch/plans': () => ok({ plans: [] }), ...r });
  const { ApiError } = await import('../../api/client');
  const { createHttpApi } = await import('../../api/http');
  vi.doMock('../../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  const { DispatchHome } = await import('./DispatchHome');
  render(
    <MemoryRouter initialEntries={['/dispatch']}>
      <Routes>
        <Route path="/dispatch" element={<DispatchHome />} />
        <Route path="/dispatch/sheets/:sheetId" element={<Sheet />} />
      </Routes>
    </MemoryRouter>,
  );
  await act(async () => {});
  return calls;
}

describe('DispatchHome', () => {
  it('第一屏是未完成的销售计划：需求 · 已组单 · 已下单 · 已排 · 还差', async () => {
    await mount({ 'GET /v1/dispatch/plans': () => ok({ plans: [PLAN] }) });
    const row = within(screen.getByTestId('dispatch-plans')).getByTestId('plan-5');
    for (const t of ['10 月计划', 'rev 7', '380', '140', '10']) expect(row).toHaveTextContent(t);
  });

  it('★ CA-D13：两个入口 —— 平台仓备货 · FBA / 海外仓备货，各带自己的去向建表', async () => {
    const calls = await mount({ 'GET /v1/dispatch/plans': () => ok({ plans: [PLAN] }),
      'POST /v1/sheets': () => ({ status: 201, json: { sheet_id: 7 } }) });
    await userEvent.click(within(screen.getByTestId('plan-5')).getByRole('button', { name: '海外仓备货' }));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ plan_id: 5, route: 'oversea' });
    expect(screen.getByText('SHEET 7')).toBeInTheDocument();
  });

  it('某个去向已有草稿：那个按钮变「接着 #N」，另一个去向照常新建', async () => {
    const calls = await mount({ 'GET /v1/dispatch/plans': () => ok({ plans: [{ ...PLAN, sheets: [{ sheet_id: 3, state: '草稿', route: 'fba' }] }] }) });
    const row = screen.getByTestId('plan-5');
    expect(within(row).getByRole('button', { name: '海外仓备货' })).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: '接着平台仓 · FBA #3' }));
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    expect(screen.getByText('SHEET 3')).toBeInTheDocument();
  });

  it('排货表列每张一个 chip；已满足的计划不给动作按钮', async () => {
    await mount({ 'GET /v1/dispatch/plans': () => ok({ plans: [
      { ...PLAN, sheets: [{ sheet_id: 3, state: '草稿', route: 'fba' }, { sheet_id: 2, state: '已确认', route: 'oversea' }] },
      { ...PLAN, plan_id: 6, pending: 0, sheets: [] }] }) });
    expect(screen.getByTestId('plan-5')).toHaveTextContent('#3 平台仓 · FBA 草稿#2 海外仓 已确认');
    const done = screen.getByTestId('plan-6');
    expect(done).toHaveTextContent('已满足');
    expect(within(done).queryByRole('button')).toBeNull();
  });

  it('列出全部排货表，带对应计划', async () => {
    await mount({ 'GET /v1/sheets': () => ok(list), 'GET /v1/dispatch/plans': () => ok(plans) });
    expect(screen.getByTestId('sheet-list')).toHaveTextContent('#1');
    expect(screen.getByTestId('sheet-list')).toHaveTextContent('t');
    expect(screen.getByTestId('sheet-list')).toHaveTextContent('平台仓 · FBA');
    expect(screen.getByTestId('sheet-list')).toHaveTextContent('海外仓');
  });

  it('没有未完成的计划', async () => {
    await mount({});
    expect(screen.getByText('没有未完成的销售计划')).toBeInTheDocument();
  });
});

describe('DispatchHome · 产品', () => {
  it('每个计划下列出每个产品：需求 · 已排未发 · 已投送 · 还差；已满足的写「已满足」', async () => {
    const recs = [
      { line_id: 13, sku: 'DCC1041294BW', sku_name: '软窝', period: '2026-10-01', state: '已确认', total: 180, staged: 3, dispatched: 0, pending: 177 },
      { line_id: 14, sku: 'DCC9', sku_name: '围栏', period: '2026-10-01', state: '已确认', total: 50, staged: 50, dispatched: 0, pending: 0 },
    ];
    await mount({ 'GET /v1/dispatch/plans': () => ok({ plans: [{ ...PLAN, records: recs }] }) });
    const r13 = screen.getByTestId('plan-rec-13');
    for (const t of ['DCC1041294BW', '软窝', '180', '177']) expect(r13).toHaveTextContent(t);
    expect(screen.getByTestId('plan-rec-14')).toHaveTextContent('已满足');
  });

  it('契约样本：后端真响应里的产品行能渲染', async () => {
    await mount({ 'GET /v1/dispatch/plans': () => ok(plans) });
    expect(screen.getByTestId('plan-rec-1')).toHaveTextContent('还差');
  });
});
