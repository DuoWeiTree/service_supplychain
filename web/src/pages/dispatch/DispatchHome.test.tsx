import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import list from '../../testing/fixtures/contract/sheet-list.json';
import plans from '../../testing/fixtures/contract/dispatch-plans.json';
import { fail, ok, stubBackend, type Handler } from '../../testing/backendStub';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.doUnmock('../../api'); vi.restoreAllMocks(); });

function Sheet() { return <div>SHEET {useParams().sheetId}</div>; }

const SH = { sheet_id: 0, state: '草稿', route: 'fba', in_flight: 0, attention: 0, drift: [], to_lock: 0, to_submit: 0,
  packing_missing: 0, logistics_missing: 0, consignments: 1, all_submitted: false,
  archived: 0, all_archived: false };
const sh = (o: Record<string, unknown>) => ({ ...SH, ...o });
const rec = (o: Record<string, unknown>) => ({ line_id: 1, sku: 'DCC1045014AI', sku_name: '围栏', period: '2026-10-01',
  state: '已确认', total: 200, confirmed: 0, ordered: 0, staged: 5, dispatched: 0, pending: 195, ...o });
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
const plansOf = (...ps: unknown[]) => ({ 'GET /v1/dispatch/plans': () => ok({ plans: ps }) });

describe('DispatchHome · 计划抬头（DL-D1 · DL-D5）', () => {
  it('抬头：计划 rev · N 个货号 · M 条记录 · 需求 · 已组单 · 已下单 · 已排未发 · 已投送 · 还差；区块标题带个数', async () => {
    await mount(plansOf({ ...PLAN, records: [rec({ line_id: 1 }), rec({ line_id: 2, period: '2026-11-01' })] }));
    const head = screen.getByTestId('plan-5');
    for (const t of ['10 月计划', 'rev 7', '1 个货号 · 2 条记录', '需求380', '已组单140', '已下单10', '已排未发0', '已投送0']) {
      expect(head.textContent!.replace(/\s/g, '')).toContain(t.replace(/\s/g, ''));
    }
    expect(screen.getByTestId('plan-left-5')).toHaveTextContent('380');
    expect(screen.getByRole('heading', { name: /未完成的销售计划/ })).toHaveTextContent('· 1 个');
  });

  it('默认全部收起；点标题展开，键盘 Enter 再收起', async () => {
    await mount(plansOf({ ...PLAN, records: [rec({})] }));
    const t = within(screen.getByTestId('plan-5')).getByRole('button', { name: /10 月计划/ });
    expect(t).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('pivot-5')).toBeNull();
    await userEvent.click(t);
    expect(t).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('pivot-5')).toBeInTheDocument();
    t.focus();
    await userEvent.keyboard('{Enter}');
    expect(screen.queryByTestId('pivot-5')).toBeNull();
  });

  it('★ 点抬头空白处也展开；点抬头里的动作按钮只做它的事，不展开（Review Focus）', async () => {
    await mount({ ...plansOf({ ...PLAN, records: [rec({})] }),
      'POST /v1/sheets': () => fail(409, 'plan_archived', '计划已归档') });
    await userEvent.click(within(screen.getByTestId('plan-5')).getByText('1 个货号 · 2 条记录'));
    expect(screen.getByTestId('pivot-5')).toBeInTheDocument();
    await userEvent.click(within(screen.getByTestId('plan-5')).getByText('1 个货号 · 2 条记录'));
    expect(screen.queryByTestId('pivot-5')).toBeNull();
    await userEvent.click(within(screen.getByTestId('plan-5')).getByRole('button', { name: '海外仓备货' }));
    expect(screen.getByText('计划已归档')).toBeInTheDocument();
    expect(screen.queryByTestId('pivot-5')).toBeNull();
  });

  it('同一批里点两下：展开再收起（切换从上一个状态算，不从渲染时的快照算）', async () => {
    await mount(plansOf({ ...PLAN, records: [rec({})] }));
    const t = within(screen.getByTestId('plan-5')).getByRole('button', { name: /10 月计划/ });
    act(() => { fireEvent.click(t); fireEvent.click(t); });
    expect(t).toHaveAttribute('aria-expanded', 'false');
  });

  it('还差 0 写「已满足」，不给动作按钮', async () => {
    await mount(plansOf({ ...PLAN, pending: 0, sheets: [sh({ sheet_id: 3 })] }));
    expect(screen.getByTestId('plan-left-5')).toHaveTextContent('已满足');
    expect(within(screen.getByTestId('plan-5')).queryByRole('button', { name: /备货|接着/ })).toBeNull();
  });
});

describe('DispatchHome · 动作（CA-D13 · 评审 P1）', () => {
  it('两个入口各带自己的去向建表', async () => {
    const calls = await mount({ ...plansOf(PLAN), 'POST /v1/sheets': () => ({ status: 201, json: { sheet_id: 7 } }) });
    await userEvent.click(within(screen.getByTestId('plan-5')).getByRole('button', { name: '海外仓备货' }));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ plan_id: 5, route: 'oversea' });
    expect(screen.getByText('SHEET 7')).toBeInTheDocument();
  });

  it('已有草稿：「接着…#N」是主按钮且不建表；没有草稿的去向是次按钮；两者横排', async () => {
    const calls = await mount(plansOf({ ...PLAN, sheets: [sh({ sheet_id: 3, route: 'fba' })] }));
    const row = screen.getByTestId('plan-5');
    const cont = within(row).getByRole('button', { name: '接着平台仓 · FBA #3' });
    const neu = within(row).getByRole('button', { name: '海外仓备货' });
    expect(cont).toHaveClass('btn--primary');
    expect(neu).not.toHaveClass('btn--primary');
    expect(cont.parentElement).toBe(neu.parentElement);
    await userEvent.click(cont);
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    expect(screen.getByText('SHEET 3')).toBeInTheDocument();
  });
});

describe('DispatchHome · 排货表 chip（DL-D3 · D7 · D8）', () => {
  it('chip 写「#N 去向 下一步」，色按词表；「已确认」不单独出现', async () => {
    await mount(plansOf({ ...PLAN, sheets: [sh({ sheet_id: 11, state: '已确认', to_submit: 1 }),
      sh({ sheet_id: 12, route: 'oversea', logistics_missing: 1 }), sh({ sheet_id: 13, route: null })] }));
    expect(screen.getByTestId('sheet-chip-11')).toHaveTextContent('#11 平台仓 · FBA 提交领星（1 张）');
    expect(screen.getByTestId('sheet-chip-11')).toHaveClass('chip--ours');
    expect(screen.getByTestId('sheet-chip-12')).toHaveTextContent('#12 海外仓 还没选物流方式');
    expect(screen.getByTestId('sheet-chip-12')).toHaveClass('chip--warn');
    expect(screen.getByTestId('sheet-chip-13')).toHaveTextContent('#13 旧版混合表：只能取消');
    expect(screen.getByTestId('plan-5')).not.toHaveTextContent('已确认');
  });

  it('多条命中写 +N', async () => {
    await mount(plansOf({ ...PLAN, sheets: [sh({ sheet_id: 11, state: '已确认', in_flight: 1, to_submit: 1 })] }));
    expect(screen.getByTestId('sheet-chip-11')).toHaveTextContent('#11 平台仓 · FBA 正在提交…+1');
  });

  it('★ 一条都不命中：写表状态并在控制台留痕（Review Focus · DL-Q6）', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    await mount(plansOf({ ...PLAN, sheets: [sh({ sheet_id: 14, state: '已确认' })] }));
    expect(screen.getByTestId('sheet-chip-14')).toHaveTextContent('#14 平台仓 · FBA 已确认');
    expect(warn).toHaveBeenCalledWith('[dispatch] next step unmatched', expect.stringContaining('"sheet_id":14'));
  });
});

describe('DispatchHome · 透视小表（DL-D2 · D6 · D10）', () => {
  it('月份只列有记录的月；格子「还差 /需求」，0 写已满足，没有记录写「—」；悬停给全量；行按还差合计降序', async () => {
    await mount(plansOf({ ...PLAN, records: [
      rec({ line_id: 1, sku: 'DCC1045014AI', period: '2026-10-01', total: 200, pending: 195 }),
      rec({ line_id: 2, sku: 'DCC1041294BW', sku_name: '软窝', period: '2026-11-01', total: 180, pending: 97 }),
      rec({ line_id: 3, sku: 'DCC1041294BW', sku_name: '软窝', period: '2026-10-01', total: 50, pending: 0 })] }));
    await userEvent.click(within(screen.getByTestId('plan-5')).getByRole('button', { name: /10 月计划/ }));
    const t = screen.getByTestId('pivot-5');
    expect(within(t).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['货号', '品名', '2026-10', '2026-11', '还差']);
    expect(within(t).getAllByRole('row').slice(1).map((r) => r.getAttribute('data-testid')))
      .toEqual(['pivot-row-5-DCC1045014AI', 'pivot-row-5-DCC1041294BW']);
    const c = screen.getByTestId('cell-5-DCC1045014AI-2026-10');
    expect(c).toHaveTextContent('195/200');
    expect(c).toHaveClass('r');
    expect(c).toHaveAttribute('title', 'DCC1045014AI 2026-10：还差 195 · 需求 200 · 已组单 0 · 已下单 0 · 已排未发 5 · 已投送 0');
    expect(screen.getByTestId('cell-5-DCC1045014AI-2026-11')).toHaveTextContent('—');
    expect(screen.getByTestId('cell-5-DCC1041294BW-2026-10')).toHaveTextContent('已满足');
  });

  it('契约样本：后端真响应里的记录能渲染成格子', async () => {
    await mount({ 'GET /v1/dispatch/plans': () => ok(plans) });
    const p = plans.plans[0]!;
    await userEvent.click(within(screen.getByTestId(`plan-${p.plan_id}`)).getByRole('button', { expanded: false }));
    const r = p.records[0]!;
    expect(screen.getByTestId(`cell-${p.plan_id}-${r.sku}-${r.period.slice(0, 7)}`)).toHaveTextContent(`${r.pending}/${r.total}`);
  });
});

describe('DispatchHome · 搜索（DL-D10）', () => {
  const two = plansOf({ ...PLAN, records: [rec({ sku: 'DCC1045014AI' })] },
    { ...PLAN, plan_id: 6, title: '测试销售计划2', records: [rec({ sku: 'DCC9' })] });

  it('★ 货号命中：只留命中的计划、自动展开、命中行高亮；大小写不影响；清空后收回（Review Focus）', async () => {
    await mount(two);
    const box = screen.getByRole('searchbox', { name: '搜计划名或货号' });
    await userEvent.type(box, 'dcc1045');
    expect(screen.queryByTestId('plan-6')).toBeNull();
    expect(screen.getByTestId('pivot-row-5-DCC1045014AI')).toHaveClass('is-on');
    await userEvent.clear(box);
    expect(screen.getByTestId('plan-6')).toBeInTheDocument();
    expect(screen.queryByTestId('pivot-5')).toBeNull();
  });

  it('计划名命中：列出但不展开；都没命中写「没有匹配的计划或货号」', async () => {
    await mount(two);
    const box = screen.getByRole('searchbox', { name: '搜计划名或货号' });
    await userEvent.type(box, '测试销售计划2');
    expect(screen.queryByTestId('plan-5')).toBeNull();
    expect(screen.getByTestId('plan-6')).toBeInTheDocument();
    expect(screen.queryByTestId('pivot-6')).toBeNull();
    await userEvent.clear(box);
    await userEvent.type(box, 'zzz');
    expect(screen.getByText('没有匹配的计划或货号')).toBeInTheDocument();
  });
});

describe('DispatchHome · 响应形状不符（spec §6：两块各自点名，互不拖垮）', () => {
  it('★ 某张表缺 drift：计划区点名缺什么、控制台留痕；全部排货表照常', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { drift: _d, ...noDrift } = sh({ sheet_id: 3 });
    await mount({ ...plansOf({ ...PLAN, sheets: [noDrift] }), 'GET /v1/sheets': () => ok(list) });
    expect(screen.getByRole('alert')).toHaveTextContent('排货表 #3 缺字段 drift —— 后端版本与页面不一致');
    expect(screen.queryByTestId('plan-5')).toBeNull();
    expect(screen.getByTestId('all-sheets')).toBeInTheDocument();
    expect(err).toHaveBeenCalledWith('[dispatch] plans shape mismatch', expect.stringContaining('排货表 #3 缺字段 drift'));
  });
});

describe('DispatchHome · 全部排货表（DL-D9）', () => {
  it('进折叠区、默认收起，标题带张数与已取消张数；表有 table 基类、数字右对齐', async () => {
    const s0 = list.sheets[0]!;
    await mount({ 'GET /v1/sheets': () => ok({ sheets: [s0, { ...s0, sheet_id: 9, state: '已取消' }] }) });
    const fold = screen.getByTestId('all-sheets');
    expect(fold).not.toHaveAttribute('open');
    expect(fold).toHaveTextContent('全部排货表（2 张，含已取消 1）');
    const t = screen.getByTestId('sheet-list');
    expect(t).toHaveClass('table');
    expect(within(t).getByRole('columnheader', { name: '件数' })).toHaveClass('r');
    expect(t).toHaveTextContent(`#${s0.sheet_id}`);
  });

  it('没有未完成的计划', async () => {
    await mount({});
    expect(screen.getByText('没有未完成的销售计划')).toBeInTheDocument();
  });
});
