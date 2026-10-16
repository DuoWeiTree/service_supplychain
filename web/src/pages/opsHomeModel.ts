/** 计划列表 `/` 的纯模型（spec 2026-10-16-plan-design §1）：下一步 · 排序 · 状态全集。 */
import type { DashboardPlans, PlanSummary, UnsubmittedBoard } from '../api/types';

/** ★ 从未提交的计划 state 为 null（没有 rev 就没有记录）—— 不是 04 里的任何一个状态值 */
export const NEVER = '未提交';
export function stateLabel(p: PlanSummary): string {
  return p.state === null ? NEVER : p.state;
}

export type NextKind = 'changed' | 'never';
export interface NextStep { kind: NextKind; text: string; note: string | null }

/** UX-D11：只有要运营动手的才给 chip；词取本页原文（两栏面板的标题）。
 *  ★ 只用两个看板的本地事实 —— 看板没取到时返回 null，调用方写「未知」，不当成「不用动手」 */
export function nextStep(p: PlanSummary, board: UnsubmittedBoard): NextStep | undefined {
  const ch = board.changed_since_submit.find((c) => c.plan_id === p.plan_id);
  if (ch) {
    const note = ch.human_cells !== null
      ? `人改了 ${ch.human_cells} 格 · 比 rev ${ch.since_rev}`
      // 028 之前提交的版本没有人填快照：比的是整份摘要（含预估），不冒充人改的格数
      : `比 rev ${ch.since_rev}（旧版本，含预估变化）`;
    return { kind: 'changed', text: '提交后又改过', note };
  }
  if (board.never_submitted.some((n) => n.plan_id === p.plan_id)) return { kind: 'never', text: NEVER, note: null };
  return undefined;
}

/** 要动手的在前：改过 → 未提交 → 其余；同档按周期新的在前 */
const RANK: Record<NextKind | 'none', number> = { changed: 0, never: 1, none: 2 };
export function byTodo(a: PlanSummary, b: PlanSummary, board: UnsubmittedBoard | null): number {
  const r = (p: PlanSummary) => RANK[(board && nextStep(p, board)?.kind) ?? 'none'];
  return r(a) - r(b) || b.period_start.localeCompare(a.period_start) || a.plan_id - b.plan_id;
}

/** UX-D13 / S-20：KPI 的键 = 接口给的全集 − 够不着的，**不手列** */
export function kpiKeys(dash: DashboardPlans): string[] {
  const hide = new Set<string>(dash.scope_note.unreachable_in_stage_a);
  return Object.keys(dash.counts).filter((k) => !hide.has(k));
}

/** 状态筛选的选项 = 未提交 + 全集 − 派生桶 − 够不着的。
 *  ★ 再并上列表里实际出现、却不在全集里的状态 —— 第 N+1 种态宁可多一个选项，不许筛不出来 */
export function stateOptions(dash: DashboardPlans | null, plans: PlanSummary[]): string[] {
  const out: string[] = [NEVER];
  if (dash) {
    const hide = new Set<string>([...dash.scope_note.unreachable_in_stage_a, ...dash.scope_note.derived_buckets]);
    for (const k of Object.keys(dash.counts)) if (!hide.has(k)) out.push(k);
  }
  for (const p of plans) { const s = stateLabel(p); if (!out.includes(s)) out.push(s); }
  return out;
}
