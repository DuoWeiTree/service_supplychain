import { Link } from 'react-router-dom';
import type { ApiError } from '../api/client';
import type { PlanId, SkipReason, SubmitResult } from '../api/types';

/** S-14 的值域只有这两个。★ 这里写的是「当场发生的事」，不是功能解说 */
const SKIP_WHY: Record<SkipReason, string> = {
  zero_purchase: '计划采购量为空或 0',
  no_claimed_msku: '这个货号下没有已认领的 msku',
};

export interface InFlightBlock { rev: number; err: ApiError }

/** 提交结果的展示层。两块互斥：409 rev_in_flight 时只有 `inFlight`，
 *  成功（含空版本）时只有 `report`——由 PlanGrid.tsx 的 submit() 决定哪个非空。 */
export function SubmitPanel(
  { planId, report, inFlight }: { planId: PlanId; report: SubmitResult | null; inFlight: InFlightBlock | null },
) {
  return (
    <>
      {inFlight && (
        // ★ 409 是「你没写错，但现在不行」—— 点名旧版号，下一步是去看那一版，不是改表单
        <div className="flash flash--bad" data-testid="rev-in-flight">
          <div>rev {inFlight.rev} 还在流转，这一版不能提交</div>
          <div className="gate__code">{inFlight.err.status} {inFlight.err.error}</div>
          <Link className="btn btn--sm" to={`/plans/${planId}/revs`}>去看 rev {inFlight.rev}</Link>
        </div>
      )}

      {report && (
        <div className="sec" data-testid="submit-report">
          <div className="sec__title">提交结果</div>
          {/* ★ 铸出几条 + 跳过几条，各自成句：只报一个等于藏起另一半 */}
          <div className="gate__sum">
            <div>rev {report.rev}</div>
            <div>铸出 {report.lines} 条</div>
            <div>跳过 {report.skipped.length} 条</div>
          </div>
          {/* ★ 空版本：说出「没有占在流转位」，否则人会以为提交成功了，
               然后奇怪为什么采购那边什么都没有 */}
          {report.lines === 0 && (
            <div className="flash flash--bad" data-testid="empty-rev">
              这一版是空的{report.in_flight === false && '，没有占在流转位'}
            </div>
          )}
          {/* ★ 判据②：被跳过的格子逐条列出，一条都不许合成「部分跳过」 */}
          <ul data-testid="skipped-list">
            {report.skipped.map((s) => (
              <li className="dropline" key={`${s.sku}-${s.period}-${s.reason}`}>
                <span className="k">{s.sku}</span>
                <span>{s.period}</span>
                <span className="gate__code">{s.reason}</span>
                <span className="gate__detail">{SKIP_WHY[s.reason]}</span>
              </li>
            ))}
          </ul>
          {/* ★ 不自动跳走：skipped[] 只在这一次响应里存在，由人点「去版本」 */}
          <Link className="btn btn--sm" to={`/plans/${planId}/revs`}>去版本</Link>
        </div>
      )}
    </>
  );
}
