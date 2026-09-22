import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { PlanDiff, RevList } from '../api/types';

const REVS: RevList = {
  revs: [
    { rev: 2, content_digest: '3b7e5d1c', is_current: true, in_flight: true,
      submitted_by: 'ops.zhang', submitted_at: '2026-09-20T10:31:00+08:00', lines: 3 },
    { rev: 1, content_digest: '8f1c2a0b', is_current: false, in_flight: false,
      submitted_by: 'ops.zhang', submitted_at: '2026-09-19T14:03:00+08:00', lines: 2 },
  ],
  in_flight_rev: 2, current_rev: 2,
};
const DIFF: PlanDiff = {
  added: [{ sku: 'SKU-1', period: '2026-11', total_units: 300 }],
  removed: [],
  changed: [{ sku: 'SKU-1', period: '2026-10',
              total_units: { from: 500, to: 600 }, demand_at_submit: { from: 160, to: 180 } }],
};

beforeEach(() => { vi.resetModules(); });

async function renderRevs() {
  const { ApiError } = await import('../api/client');
  const state: RevList = JSON.parse(JSON.stringify(REVS));
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      listRevs: async () => JSON.parse(JSON.stringify(state)) as RevList,
      setCurrentRev: async (_p: number, rev: number) => {
        state.revs.forEach((r) => { r.is_current = r.rev === rev; });
        state.current_rev = rev;          // ★ in_flight_rev 故意不动：它由采购侧决定
        return { current_rev: rev };
      },
      diff: async () => DIFF,
      cancelRev: async (_p: number, rev: number, reason: string) => {
        if (reason.trim() === '') throw new ApiError(400, 'reason_required', '撤销必须填理由', { rev });
        return { cancelled: [101, 102, 103], skipped_terminal: 0, reason };
      },
    },
  }));
  const { PlanRevs } = await import('./PlanRevs');
  const utils = render(
    <MemoryRouter initialEntries={['/plans/2/revs']}>
      <Routes><Route path="/plans/:planId/revs" element={<PlanRevs />} /></Routes>
    </MemoryRouter>,
  );
  // ★ 初次 load() 的落地要等在这里。renderRevs 是 async 函数 —— 调用方 `await` 它的
  //   那一下就是一次微任务跳变，listRevs 的 resolve 正好落在 act() 之外，于是每条用它的
  //   测试都甩出一条 act() 警告。findBy* 自带 act 包裹，把那次更新收进来。
  await screen.findByTestId('rev-2');
  return utils;
}

describe('版本编辑', () => {
  it('★ 流转中与当前使用分开显示 —— 两个标记不是一回事', async () => {
    await renderRevs();
    const row2 = await screen.findByTestId('rev-2');
    expect(within(row2).getByText('流转中')).toBeInTheDocument();
    expect(within(row2).getByText('当前使用')).toBeInTheDocument();
    const row1 = screen.getByTestId('rev-1');
    expect(within(row1).queryByText('流转中')).toBeNull();
    expect(within(row1).queryByText('当前使用')).toBeNull();
  });

  it('设为当前使用：标记搬过去，而流转中留在原地', async () => {
    await renderRevs();
    await screen.findByTestId('rev-1');
    await userEvent.click(within(screen.getByTestId('rev-1')).getByRole('button', { name: '设为当前使用' }));
    expect(await within(screen.getByTestId('rev-1')).findByText('当前使用')).toBeInTheDocument();
    expect(within(screen.getByTestId('rev-2')).queryByText('当前使用')).toBeNull();
    expect(within(screen.getByTestId('rev-2')).getByText('流转中')).toBeInTheDocument();
  });

  it('★ 版本差异：新增 / 改动分在两处，改动给两个数不给增减', async () => {
    await renderRevs();
    await screen.findByTestId('rev-2');
    await userEvent.selectOptions(screen.getByLabelText('比较自'), '1');
    await userEvent.click(screen.getByRole('button', { name: '比较' }));

    const changed = within(await screen.findByTestId('diff-changed')).getByTestId('changed-SKU-1-2026-10');
    expect(changed).toHaveTextContent('500');
    expect(changed).toHaveTextContent('600');
    expect(changed).not.toHaveTextContent('+100');      // ★ 不合成增减
    const added = within(screen.getByTestId('diff-added')).getByTestId('added-SKU-1-2026-11');
    expect(added).toHaveTextContent('300');
    // ★ 三个数组都有自己的区块，空的也要说「无」，不许整块消失
    expect(screen.getByTestId('diff-removed')).toHaveTextContent('无');
  });

  it('★ 撤销理由必填：空理由拿到 400 reason_required 并点名', async () => {
    await renderRevs();
    await screen.findByTestId('rev-2');
    await userEvent.click(within(screen.getByTestId('rev-2')).getByRole('button', { name: '撤销' }));
    await userEvent.click(screen.getByRole('button', { name: '确认撤销' }));
    expect(await screen.findByText('400 reason_required')).toBeInTheDocument();
  });

  it('填了理由就撤得掉，并交代撤了哪几条记录', async () => {
    await renderRevs();
    await screen.findByTestId('rev-2');
    await userEvent.click(within(screen.getByTestId('rev-2')).getByRole('button', { name: '撤销' }));
    await userEvent.type(screen.getByLabelText('理由'), '需求口径变了');
    await userEvent.click(screen.getByRole('button', { name: '确认撤销' }));
    const report = await screen.findByTestId('cancel-report');
    expect(report).toHaveTextContent('撤销 3 条记录');
    expect(report).toHaveTextContent('需求口径变了');    // ★ 理由要回显：它被记在每条记录上
    // 撤销后弹窗关闭且列表已重新加载 —— 这一步同时把 doCancel 的收尾异步更新收进 act()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  // ★ Ruling 2（team-lead 09-22）：cancelled.length 与 skipped_terminal 必须分开点名 ——
  //   「这一版本来就只有 1 条」与「另外 3 条早已在终态」长得一模一样，不点名就分不清。
  it('★ skipped_terminal 与撤销条数分开点名', async () => {
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        listRevs: async () => JSON.parse(JSON.stringify(REVS)) as RevList,
        setCurrentRev: async (_p: number, rev: number) => ({ current_rev: rev }),
        diff: async () => DIFF,
        cancelRev: async (_p: number, _rev: number, reason: string) => {
          if (reason.trim() === '') throw new ApiError(400, 'reason_required', '撤销必须填理由', {});
          return { cancelled: [201], skipped_terminal: 3, reason };
        },
      },
    }));
    const { PlanRevs } = await import('./PlanRevs');
    render(
      <MemoryRouter initialEntries={['/plans/2/revs']}>
        <Routes><Route path="/plans/:planId/revs" element={<PlanRevs />} /></Routes>
      </MemoryRouter>,
    );
    await screen.findByTestId('rev-2');
    await userEvent.click(within(screen.getByTestId('rev-2')).getByRole('button', { name: '撤销' }));
    await userEvent.type(screen.getByLabelText('理由'), '清理历史');
    await userEvent.click(screen.getByRole('button', { name: '确认撤销' }));
    const report = await screen.findByTestId('cancel-report');
    expect(report).toHaveTextContent('撤销 1 条记录');
    expect(report).toHaveTextContent('另有 3 条已在终态，未动');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  // ★ review finding 1：与 PlanGrid.tsx/PlanAdd.tsx 同一护栏模式，同一手法验证 ——
  //   用同步的 fireEvent.click 连打两次（不像 userEvent 那样在两次点击之间等待微任务落定），
  //   逼出竞态：真正的护栏必须在函数入口同步早退，不能只靠 UI 层「看起来」禁用了。
  it('★ 设为当前使用在飞时禁用并防重入：双击只发一次请求', async () => {
    const { ApiError } = await import('../api/client');
    const calls: number[] = [];
    const state: RevList = JSON.parse(JSON.stringify(REVS));
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        listRevs: async () => JSON.parse(JSON.stringify(state)) as RevList,
        setCurrentRev: async (_p: number, rev: number) => {
          calls.push(rev);
          state.current_rev = rev;
          return { current_rev: rev };
        },
        diff: async () => DIFF,
        cancelRev: async () => ({ cancelled: [], skipped_terminal: 0, reason: '' }),
      },
    }));
    const { PlanRevs } = await import('./PlanRevs');
    render(
      <MemoryRouter initialEntries={['/plans/2/revs']}>
        <Routes><Route path="/plans/:planId/revs" element={<PlanRevs />} /></Routes>
      </MemoryRouter>,
    );
    const button = within(await screen.findByTestId('rev-1')).getByRole('button', { name: '设为当前使用' });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(within(screen.getByTestId('rev-1')).getByText('当前使用')).toBeInTheDocument());
    expect(calls).toHaveLength(1);
  });

  it('★ 确认撤销在飞时禁用并防重入：双击只发一次请求', async () => {
    const { ApiError } = await import('../api/client');
    const calls: number[] = [];
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        listRevs: async () => JSON.parse(JSON.stringify(REVS)) as RevList,
        setCurrentRev: async (_p: number, rev: number) => ({ current_rev: rev }),
        diff: async () => DIFF,
        cancelRev: async (_p: number, rev: number, reason: string) => {
          calls.push(rev);
          return { cancelled: [101], skipped_terminal: 0, reason };
        },
      },
    }));
    const { PlanRevs } = await import('./PlanRevs');
    render(
      <MemoryRouter initialEntries={['/plans/2/revs']}>
        <Routes><Route path="/plans/:planId/revs" element={<PlanRevs />} /></Routes>
      </MemoryRouter>,
    );
    await screen.findByTestId('rev-2');
    await userEvent.click(within(screen.getByTestId('rev-2')).getByRole('button', { name: '撤销' }));
    await userEvent.type(screen.getByLabelText('理由'), '双击测试');
    const confirm = screen.getByRole('button', { name: '确认撤销' });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await screen.findByTestId('cancel-report');
    expect(calls).toHaveLength(1);
  });

  // ★ I4（终审）：current_rev 为 null 时原来发的是 to=0 —— 后端对不存在的 rev 不报错，
  //   plan_line 里没有 rev 0 的行，于是三块全是「无」，与「这两版一模一样」长得完全一样。
  it('★ 没有当前使用的版本：比较禁用、不发请求，并说出下一步', async () => {
    const { ApiError } = await import('../api/client');
    const diffCalls: number[][] = [];
    const NO_CURRENT: RevList = { ...JSON.parse(JSON.stringify(REVS)) as RevList, current_rev: null };
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        listRevs: async () => JSON.parse(JSON.stringify(NO_CURRENT)) as RevList,
        setCurrentRev: async (_p: number, rev: number) => ({ current_rev: rev }),
        diff: async (_p: number, from: number, to: number) => { diffCalls.push([from, to]); return DIFF; },
        cancelRev: async () => ({ cancelled: [], skipped_terminal: 0, reason: '' }),
      },
    }));
    const { PlanRevs } = await import('./PlanRevs');
    render(
      <MemoryRouter initialEntries={['/plans/2/revs']}>
        <Routes><Route path="/plans/:planId/revs" element={<PlanRevs />} /></Routes>
      </MemoryRouter>,
    );
    await screen.findByTestId('rev-2');
    expect(screen.getByTestId('no-current-rev')).toHaveTextContent('还没有当前使用的版本，先设一版');

    // 选了「比较自」也照样禁用 —— 缺的是另一端，不是这一端
    await userEvent.selectOptions(screen.getByLabelText('比较自'), '1');
    expect(screen.getByRole('button', { name: '比较' })).toBeDisabled();

    // ★ 这一条只能证到「点不动、没发请求」为止：按钮置灰后 jsdom 本来就不派发 click，
    //   所以它托着一半。真正硬的那半在源码形状上 —— `?? 0` 已经整个不存在了，
    //   compare(to) 的另一端必须由调用方给一个真实的 rev，没有兜底值可落。
    fireEvent.click(screen.getByRole('button', { name: '比较' }));
    await waitFor(() => expect(screen.queryByTestId('diff-changed')).toBeNull());
    expect(diffCalls).toEqual([]);
  });

  // ★ review finding 4：404 rev_not_found 走的是与 400 同一条 catch → <ErrorDetail> 路径，
  //   之前没有测试直接点名过这一支
  it('★ setCurrentRev 拿到 404 rev_not_found 时用 ErrorDetail 点名', async () => {
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({
      ApiError,
      api: {
        listRevs: async () => JSON.parse(JSON.stringify(REVS)) as RevList,
        setCurrentRev: async () => { throw new ApiError(404, 'rev_not_found', '没有这一版', { rev: 1 }); },
        diff: async () => DIFF,
        cancelRev: async () => ({ cancelled: [], skipped_terminal: 0, reason: '' }),
      },
    }));
    const { PlanRevs } = await import('./PlanRevs');
    render(
      <MemoryRouter initialEntries={['/plans/2/revs']}>
        <Routes><Route path="/plans/:planId/revs" element={<PlanRevs />} /></Routes>
      </MemoryRouter>,
    );
    await userEvent.click(within(await screen.findByTestId('rev-1')).getByRole('button', { name: '设为当前使用' }));
    expect(await screen.findByText('404 rev_not_found')).toBeInTheDocument();
    expect(screen.getByText('没有这一版')).toBeInTheDocument();
  });
});
