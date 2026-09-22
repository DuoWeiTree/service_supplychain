import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import plansFixture from '../api/fixtures/plans.json';
import type { CountKey, PlanSummary } from '../api/types';

/** 判据⑥ 的第二屏（终审建议 2）。
 *
 *  ★ 为什么补这一屏：PlanGrid 那条门禁读的是同一份 grid fixture，两侧喂的字节一样，
 *    所以它只门禁 http.ts 的拆包与 mock 的**读**路径。而归档语义的分叉（I2）落在
 *    `GET /plans` 与两个看板上 —— 首页是唯一读它们的屏，门禁不到这里就照不到那条分叉。
 *
 *  ★ 桩按**后端**的规则答（`api/ui/plans.py:69` 的 archived 默认值、
 *    `api/ui/dashboard.py:30`/`:57` 的 archived_at IS NULL），不是照 mock 抄一遍：
 *    mock 要是跟后端不一致，红的就是这一条。
 *
 *  ⚠ **但有一段不是这样，它是循环的**：`changed_since_submit` 两侧都写死 `plan_id === 2`
 *    （桩见下面的 `backendBody`，mock 见 `mock.ts` 的 dashboardUnsubmitted）。真后端
 *    （`api/ui/dashboard.py` 的 content_digest 比对）跟这个常量毫无关系，所以这一段
 *    **不证明任何事**，只是为了让这一屏渲染得出来。载重的是另外四段：
 *    `GET /plans` 的归档分叉、`excluded.archived` 现算、两个看板只数没归档的。 */

const PLANS = plansFixture.plans as PlanSummary[];
const RANKED: CountKey[] = ['已提交', '已确认', '已下单', '准备排货', '已排货', '已完结'];

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

function backendBody(url: string): unknown {
  // ★ 看板只看没归档的
  const live = PLANS.filter((p) => p.archived_at === null);
  if (url.includes('/dashboard/plans')) {
    const counted: CountKey[] = ['进行中', '已提交未确认', ...RANKED, '已撤销'];
    const counts = Object.fromEntries(counted.map((k) => [k, 0])) as Record<CountKey, number>;
    let neverSubmitted = 0;
    for (const p of live) {
      if (p.state === null) { neverSubmitted += 1; continue; }
      counts[p.state] += 1;
      if (p.state !== '已完结' && p.state !== '已撤销') counts['进行中'] += 1;
      if (p.state === '已提交') counts['已提交未确认'] += 1;
    }
    return { counts, scope_note: {
      unreachable_in_stage_a: ['已下单', '准备排货', '已排货'],
      never_submitted_excluded: neverSubmitted,
    } };
  }
  if (url.includes('/dashboard/unsubmitted')) {
    return {
      never_submitted: live.filter((p) => p.state === null).map((p) => ({ plan_id: p.plan_id, title: p.title })),
      // ⚠ 循环的那一段：`plan_id === 2` 是 mock 的老约定，这里照抄了它，两侧同一个常量
      //   ⇒ 这一行不载重。真后端按 content_digest 比，与这个数无关。
      changed_since_submit: live.filter((p) => p.plan_id === 2)
        .map((p) => ({ plan_id: p.plan_id, title: p.title, since_rev: p.state_rev ?? 0 })),
    };
  }
  if (url.includes('/plans')) {
    // ★ archived 不给 ⇒ 排除已归档；给 true ⇒ 全集，且 excluded 归 0
    const want = url.includes('archived=true');
    return {
      plans: want ? PLANS : live,
      excluded: { archived: want ? 0 : PLANS.length - live.length },
    };
  }
  throw new Error(`桩没有覆盖这个调用：${url}`);
}

async function homeRegions(source: 'mock' | 'api'): Promise<Record<string, string>> {
  vi.resetModules();
  const { ApiError } = await import('../api/client');
  if (source === 'api') {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => new Response(
      JSON.stringify(backendBody(String(input))),
      { status: 200, headers: { 'content-type': 'application/json' } }));
    const { createHttpApi } = await import('../api/http');
    vi.doMock('../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  } else {
    const { createMockApi } = await import('../api/mock');
    vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
  }
  const { OpsHome } = await import('../pages/OpsHome');
  const { unmount } = render(<MemoryRouter><OpsHome /></MemoryRouter>);
  await screen.findByTestId('plan-list');
  const pick = (id: string) => (screen.getByTestId(id).textContent ?? '').replace(/\s+/g, ' ').trim();
  const out = {
    counts: pick('counts'),
    list: pick('plan-list'),
    hidden: pick('hidden-rows'),
    never: pick('never-submitted'),
    changed: pick('changed-since-submit'),
  };
  unmount();
  return out;
}

describe('判据⑥ · 首页也要两种数据源跑出同一屏', () => {
  it('★ 计数 / 列表 / 两栏待办 / 被挡掉的那一侧，逐段逐字相同', async () => {
    const a = await homeRegions('mock');
    const b = await homeRegions('api');
    expect(b).toEqual(a);

    // ★ 顺带守住「这一屏确实有内容」以及靶子确实在现场 —— 两边都空也会相等，那是假绿
    expect(a.list).toContain('2026 Q4 销售计划');
    expect(a.never).toContain('2026 Q4 销售计划');
    // ★ 归档计划既不在列表里，也被数出来了（数是现算的，不是冻在 fixture 里的常量）
    expect(a.list).not.toContain('2025 Q4 封存计划');
    expect(a.hidden).toContain('已归档 1 张');
    // ★ 归档那张也是「已提交」—— 计数把它数进去的话这里就是 2
    expect(a.counts).toContain('1已提交');
  });
});
