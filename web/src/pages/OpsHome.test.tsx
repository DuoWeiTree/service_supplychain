import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { OpsHome } from './OpsHome';
import type { CreatePlanInput } from '../api/types';

const renderHome = () => render(<MemoryRouter><OpsHome /></MemoryRouter>);

afterEach(() => { vi.resetModules(); vi.doUnmock('../api'); });

describe('运营首页', () => {
  it('只渲染够得着的三态，其余★不渲染（不是 0）', async () => {
    renderHome();
    const counts = await screen.findByTestId('counts');
    // ★ 穷尽相等，不是举例排除 —— 举例排除拦不住"已确认/已完结"这类漏在名单外的态悄悄混进来
    const labels = Array.from(counts.querySelectorAll('.kpi__d')).map((el) => el.textContent);
    expect(new Set(labels)).toEqual(new Set(['未提交', '已提交', '已撤销']));
    expect(counts.textContent).not.toContain('0');
  });

  it('计划列表里 state===null 的行渲染"未提交"芯片，不是"已撤销"或空白', async () => {
    renderHome();
    const table = await screen.findByTestId('plan-list');
    const row = within(table).getByText('2026 Q4 销售计划').closest('tr');
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText('未提交')).toHaveClass('chip', 'chip--dim');
    expect(within(row as HTMLElement).queryByText('已撤销')).toBeNull();
  });

  it('★ 接口说哪些态够不着，屏上就一个都不许有 —— 名单由接口给，不在前端硬编码', async () => {
    renderHome();
    const counts = await screen.findByTestId('counts');
    const unreachable = JSON.parse(screen.getByTestId('unreachable').textContent!) as string[];
    expect(unreachable.length).toBeGreaterThan(0);          // ★ 空名单等于这条断言没跑
    for (const s of unreachable) expect(counts.textContent).not.toContain(s);
  });

  it('★ 完结的计划不显示，但被挡掉的两侧都要有个数', async () => {
    renderHome();
    const table = await screen.findByTestId('plan-list');
    expect(within(table).queryByText('2026 Q1 首发计划')).toBeNull();
    expect(screen.getByTestId('hidden-rows')).toHaveTextContent('已完结 1 张');
    expect(screen.getByTestId('hidden-rows')).toHaveTextContent('已归档 2 张');
  });

  it('两栏：未提交 / 提交后又改过（第二栏点名是跟哪一版比的）', async () => {
    renderHome();
    expect(await screen.findByTestId('never-submitted')).toHaveTextContent('2026 Q4 销售计划');
    const changed = screen.getByTestId('changed-since-submit');
    expect(changed).toHaveTextContent('2026 Q3 补货计划');
    expect(changed).toHaveTextContent('rev 2');
  });

  it('按状态筛选，列表跟着变', async () => {
    renderHome();
    await screen.findByTestId('plan-list');
    await userEvent.selectOptions(screen.getByLabelText('状态'), '已撤销');
    const table = screen.getByTestId('plan-list');
    expect(within(table).getByText('2026 Q2 清库计划')).toBeInTheDocument();
    expect(within(table).queryByText('2026 Q4 销售计划')).toBeNull();
  });

  // ★ M14（终审）：原断言打在 `nav-to` 这个只为测试存在的隐藏 span 上 ——
  //   接上真 `<Routes>` 后 OpsHome 整个卸载，它在生产里永远不会渲染，
  //   于是那条断言证明的是「组件设过一个 state 变量」，不是「导航发生了」。
  //   证人必须在现场：改成在 `<Routes>` 下断言真的走到了目标页。
  it('新建销售计划 → 走到网格', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<OpsHome />} />
          <Route path="/plans/:planId" element={<h1>网格</h1>} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByTestId('plan-list');
    await userEvent.click(screen.getByRole('button', { name: '新建销售计划' }));
    await userEvent.type(screen.getByLabelText('标题'), '2027 Q1 销售计划');
    await userEvent.click(screen.getByRole('button', { name: '新建' }));
    expect(await screen.findByRole('heading', { name: '网格' })).toBeInTheDocument();
  });

  // ★ I6（终审）：三个端点各记各的错。合成一个 err 的写法会在 dashboardPlans 挂掉时
  //   把已经成功返回的计划列表与两栏待办一起换成一个错误块
  it('★ 看板计数挂了，计划列表与两栏待办照常出数（I6）', async () => {
    vi.resetModules();
    const { ApiError } = await import('../api/client');
    const { createMockApi } = await import('../api/mock');
    const real = createMockApi();
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        ...real,
        dashboardPlans: async () => { throw new ApiError(503, 'dashboard_unavailable', '看板取不到'); },
      },
    }));
    const { OpsHome: Fresh } = await import('./OpsHome');
    render(<MemoryRouter><Fresh /></MemoryRouter>);

    expect(await screen.findByTestId('plan-list')).toHaveTextContent('2026 Q4 销售计划');
    expect(screen.getByTestId('never-submitted')).toHaveTextContent('2026 Q4 销售计划');
    expect(screen.getByText('503 dashboard_unavailable')).toBeInTheDocument();
    // ★ 取不到的那个计数给「未知」，不落回 0 —— 「一张都没有」与「取不到」不许同形
    const submitted = within(screen.getByTestId('counts')).getByText('已提交').closest('.kpi')!;
    expect(within(submitted as HTMLElement).getByText('—')).toBeInTheDocument();
  });

  // ★ I3（终审）：create 是分支里仅剩的第三个无护栏写动作。阶段 A 没有删计划的
  //   界面入口（S-27），双击建出的第二张同名计划只能去归档 —— 所以护栏要在入口早退，
  //   不是只置灰按钮。与 PlanAdd/PlanRevs 同一手法：同步 fireEvent 连打两次。
  it('★ 双击「新建」只建一张计划（I3）', async () => {
    vi.resetModules();
    const { ApiError } = await import('../api/client');
    const created: CreatePlanInput[] = [];
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        listPlans: async () => ({ plans: [], excluded: { archived: 0 } }),
        dashboardUnsubmitted: async () => ({ never_submitted: [], changed_since_submit: [] }),
        dashboardPlans: async () => ({
          counts: {}, scope_note: { unreachable_in_stage_a: [], never_submitted_excluded: 0 },
        }),
        createPlan: async (input: CreatePlanInput) => {
          created.push(input);
          return { plan_id: 9 };
        },
      },
    }));
    const { OpsHome: Fresh } = await import('./OpsHome');
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<Fresh />} />
          <Route path="/plans/:planId" element={<h1>网格</h1>} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByTestId('plan-list');
    await userEvent.click(screen.getByRole('button', { name: '新建销售计划' }));
    await userEvent.type(screen.getByLabelText('标题'), '2027 Q1 销售计划');
    const submit = screen.getByRole('button', { name: '新建' });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(await screen.findByRole('heading', { name: '网格' })).toBeInTheDocument();
    expect(created).toHaveLength(1);
  });
});
