import { describe, expect, it } from 'vitest';
import type { DashboardPlans, PlanSummary, UnsubmittedBoard } from '../api/types';
import { nextStep, stateOptions } from './opsHomeModel';

const plan = (plan_id: number, state: PlanSummary['state']): PlanSummary => ({
  plan_id, title: `p${plan_id}`, period_start: '2026-10-01', months: 3, owner_actor: 'a',
  archived_at: null, state, state_rev: state === null ? null : 1,
});

describe('计划列表模型', () => {
  it('028 之前的版本比的是整份摘要：chip 照给，但不冒充「人改了 N 格」', () => {
    const board: UnsubmittedBoard = {
      never_submitted: [],
      changed_since_submit: [{ plan_id: 1, title: 'p1', since_rev: 3, human_cells: null, basis: 'content_digest' }],
    };
    expect(nextStep(plan(1, '已提交'), board)).toEqual({ kind: 'changed', text: '提交后又改过', note: '比 rev 3（旧版本，含预估变化）' });
  });

  it('★ 列表里出现了全集之外的状态 —— 多一个选项，不许筛不出来', () => {
    const dash = {
      counts: { 进行中: 0, 已提交: 1 },
      scope_note: { unreachable_in_stage_a: [], derived_buckets: ['进行中'], never_submitted_excluded: 0 },
    } as unknown as DashboardPlans;
    expect(stateOptions(dash, [plan(1, '已提交'), plan(2, '神秘态' as PlanSummary['state'])])).toEqual(['未提交', '已提交', '神秘态']);
  });
});
