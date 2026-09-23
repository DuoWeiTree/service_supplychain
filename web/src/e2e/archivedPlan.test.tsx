import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import gridFixture from '../api/fixtures/grid-1.json';
import plansFixture from '../api/fixtures/plans.json';
import sellersFixture from '../api/fixtures/sellers.json';

/** ★ I1（终审）：`getPlan` 从前走 `plans({})`，后端按默认值 `archived=false` 排除已归档
 *  （`api/ui/plans.py:69`），于是 find 落空、前端抛 404「没有这张计划」——
 *  而后端专门把这两件事分开过（`deps.py:88`）。运营打开一张归档计划，
 *  屏上说的是「没有这张计划」，而它就在那儿。
 *
 *  这条走的是**真 http.ts**（fetch 桩），不是 mock：拼查询串的那一行就在 http.ts 里。 */

const ARCHIVED = plansFixture.plans.find((p) => p.archived_at !== null)!;

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

function stubFetch(seen: string[]) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    seen.push(`${method} ${url}`);
    if (method === 'PUT') {
      // ★ 归档后一律拒写（`api/ui/deps.py:93`），点名 archived_at —— 不是 404
      return new Response(
        JSON.stringify({ error: 'plan_archived', hint: '计划已归档，不接受写入',
                         plan_id: ARCHIVED.plan_id, archived_at: ARCHIVED.archived_at }),
        { status: 409, headers: { 'content-type': 'application/json' } });
    }
    // ★ 桩照后端的口径答：archived=true 才给全集，不给就排除已归档 —— 桩要是无条件
    //   把归档计划一起返回，这条测试就证明不了 http.ts 真的把参数发出去了
    const body = url.includes('/grid') ? gridFixture
      : url.includes('/sellers') ? sellersFixture
      : url.includes('/plans')
        ? { plans: url.includes('archived=true')
              ? plansFixture.plans
              : plansFixture.plans.filter((p) => p.archived_at === null),
            excluded: { archived: url.includes('archived=true') ? 0 : 1 } }
        : null;
    if (body === null) throw new Error(`桩没有覆盖这个调用：${url}`);
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
  });
}

async function renderArchived(seen: string[]) {
  stubFetch(seen);
  const { ApiError } = await import('../api/client');
  const { createHttpApi } = await import('../api/http');
  vi.doMock('../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  const { PlanGrid } = await import('../pages/PlanGrid');
  return render(
    <MemoryRouter initialEntries={[`/plans/${ARCHIVED.plan_id}`]}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
}

describe('I1 · 已归档的计划打得开，说得出「已归档」', () => {
  it('★ 归档计划不塌成「没有这张计划」，抬头照常出、并标出归档日期', async () => {
    const seen: string[] = [];
    await renderArchived(seen);

    expect(await screen.findByRole('heading', { name: ARCHIVED.title })).toBeInTheDocument();
    const notice = screen.getByTestId('archived-notice');
    expect(notice).toHaveTextContent('这张计划已归档');
    expect(notice).toHaveTextContent(ARCHIVED.archived_at!.slice(0, 10));
    // ★ 404 那条路必须一个字都不出现
    expect(screen.queryByText('没有这张计划')).toBeNull();
    expect(screen.queryByText('404 plan_not_found')).toBeNull();
    // ★ 参数真的发出去了 —— 桩只在带 archived=true 时才给这张计划
    expect(seen.some((c) => c.includes('/plans?archived=true'))).toBe(true);
  });

  it('★ 在归档计划上填数 → 后端 409 plan_archived 原样上屏，点名 archived_at', async () => {
    const seen: string[] = [];
    await renderArchived(seen);
    const block = await screen.findByTestId('block-11072-A4P-TOY-002');
    await userEvent.click(within(block).getByRole('button', { name: /^展开 / }));

    const cell = within(block).getByTestId('cell-MSKU-D-11072-2026-10');
    const input = within(cell).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, '42');
    await userEvent.tab();

    expect(await screen.findByText('409 plan_archived')).toBeInTheDocument();
    // ★ 断言钉在 ErrorDetail 本身：同一句话 toast 上也有一份，不点名范围就会被它托住
    const detail = screen.getByRole('alert');
    expect(within(detail).getByText('计划已归档，不接受写入')).toBeInTheDocument();
    expect(within(detail).getByText(ARCHIVED.archived_at!)).toBeInTheDocument();
  });
});
