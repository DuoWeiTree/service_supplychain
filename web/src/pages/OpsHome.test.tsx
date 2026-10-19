import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

// ★ 产品的 api 只打真后端（PB-D17）；这一文件的默认后端是测试替身，单条用例再 doMock 覆盖
vi.mock('../api', async () => {
  const { ApiError } = await import('../api/client');
  const { createMemoryBackend } = await import('../testing/memoryBackend');
  return { ApiError, api: createMemoryBackend() };
});
import { OpsHome } from './OpsHome';
import type { CreatePlanInput } from '../api/types';

const renderHome = () => render(<MemoryRouter><OpsHome /></MemoryRouter>);

afterEach(() => { vi.resetModules(); vi.doUnmock('../api'); });

describe('运营首页', () => {
  it('★ UX-D13：KPI 按接口给的全集减去够不着的渲染 —— 「已确认」「已完结」都在，不是写死三态', async () => {
    renderHome();
    const counts = await screen.findByTestId('counts');
    await within(counts).findByText('已确认');
    // ★ 穷尽相等，不是举例排除 —— 举例排除拦不住漏在名单外的态
    const labels = Array.from(counts.querySelectorAll('.kpi__d')).map((el) => el.textContent);
    expect(labels).toEqual(['提交后又改过', '未提交', '进行中', '已提交未确认', '已提交', '已确认', '已完结', '已撤销']);
  });

  it('★ UX-D13：状态筛选里有「已确认」；选「已完结」列出已完结的那张，而不是一张都筛不出', async () => {
    renderHome();
    await screen.findByTestId('plan-list');
    const opts = Array.from((screen.getByLabelText('状态') as HTMLSelectElement).options).map((o) => o.value);
    expect(opts).toEqual(['', 'todo', '未提交', '已提交', '已确认', '已完结', '已撤销']);
    await userEvent.selectOptions(screen.getByLabelText('状态'), '已完结');
    expect(within(screen.getByTestId('plan-list')).getByText('2026 Q1 首发计划')).toBeInTheDocument();
  });

  it('★ UX-D11：每行一个「下一步」chip，要动手的排前 —— 改过 → 未提交 → 其余', async () => {
    renderHome();
    const table = await screen.findByTestId('plan-list');
    await within(table).findByText('提交后又改过');
    const titles = Array.from(table.querySelectorAll('tbody tr')).map((tr) => tr.querySelector('[data-testid=plan-title]')?.textContent);
    expect(titles).toEqual(['2026 Q3 补货计划', '2026 Q4 销售计划', '2026 Q2 清库计划']);
    const changed = within(table).getByText('2026 Q3 补货计划').closest('tr') as HTMLElement;
    expect(within(changed).getByText('提交后又改过')).toHaveClass('chip', 'chip--ours');
    // ★ 一个数要能回答它是关于什么的：人改了几格、跟哪一版比
    expect(changed).toHaveTextContent('人改了 2 格（比 rev 2）');
    const never = within(table).getByText('2026 Q4 销售计划').closest('tr') as HTMLElement;
    expect(within(never).getAllByText('未提交')[0]).toHaveClass('chip', 'chip--ours');
    const cancelled = within(table).getByText('2026 Q2 清库计划').closest('tr') as HTMLElement;
    expect(within(cancelled).getByTestId('next')).toBeEmptyDOMElement();
  });

  it('点「提交后又改过」计数 = 只看要动手的', async () => {
    renderHome();
    await screen.findByTestId('plan-list');
    await userEvent.click(await screen.findByRole('button', { name: /提交后又改过/ }));
    const table = screen.getByTestId('plan-list');
    expect(within(table).getByText('2026 Q3 补货计划')).toBeInTheDocument();
    expect(within(table).queryByText('2026 Q4 销售计划')).toBeNull();
  });

  it('★ 列表没取到时底行写「正在取…」，不写「已完结 0 张」（空 ≠ 0）', async () => {
    vi.resetModules();
    const { ApiError } = await import('../api/client');
    const { createMemoryBackend } = await import('../testing/memoryBackend');
    const real = createMemoryBackend();
    vi.doMock('../api', () => ({ ApiError, api: { ...real, listPlans: () => new Promise(() => {}) } }));
    const { OpsHome: Fresh } = await import('./OpsHome');
    render(<MemoryRouter><Fresh /></MemoryRouter>);
    const hidden = await screen.findByTestId('hidden-rows');
    expect(hidden).toHaveTextContent('正在取…');
    expect(hidden.textContent).not.toMatch(/\d/);
  });

  it('计划列表里 state===null 的行渲染"未提交"芯片，不是"已撤销"或空白', async () => {
    renderHome();
    const table = await screen.findByTestId('plan-list');
    const row = within(table).getByText('2026 Q4 销售计划').closest('tr');
    expect(row).not.toBeNull();
    // 状态列是灰 chip；「下一步」列另有一个主色的（UX-D11）
    const chips = within(row as HTMLElement).getAllByText('未提交');
    expect(chips.map((c) => c.className)).toEqual(['chip chip--ours', 'chip chip--dim']);
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
    // ★ I2：这个数由替身按本次查询现算（fixture 里 1 张归档），不再是冻死的常量 2
    expect(screen.getByTestId('hidden-rows')).toHaveTextContent('已归档 1 张');
    expect(within(table).queryByText('2025 Q4 封存计划')).toBeNull();
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
    const { createMemoryBackend } = await import('../testing/memoryBackend');
    const real = createMemoryBackend();
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
    expect(within(screen.getByTestId('plan-list')).getAllByText('未提交')[0]).toHaveClass('chip--ours');
    expect(screen.getByText('503 dashboard_unavailable')).toBeInTheDocument();
    // ★ 取不到的那个计数给「未知」，不落回 0 —— 「一张都没有」与「取不到」不许同形
    //   全集也来自这个端点 —— 取不到就不知道有哪些态，写一格「未知」，不手列一份顶上
    const counts = screen.getByTestId('counts');
    expect(within(counts).queryByText('已提交')).toBeNull();
    const unknown = within(counts).getByText('各状态计数').closest('.kpi')!;
    expect(within(unknown as HTMLElement).getByText('—')).toBeInTheDocument();
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
          counts: {}, scope_note: { unreachable_in_stage_a: [], derived_buckets: [], never_submitted_excluded: 0 },
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
