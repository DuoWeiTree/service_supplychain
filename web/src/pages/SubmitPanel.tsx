import { Link } from 'react-router-dom';
import type { ApiError } from '../api/client';
import { ErrorDetail } from '../components/ErrorDetail';
import { Fold } from '../components/Fold';
import type { LineInPurchase, PlanId, Seller, SkippedCell, SubmitResult } from '../api/types';

export interface LinesInPurchaseBlock { err: ApiError; lines: LineInPurchase[] }
/** 提交后另取的「这张计划待采购几条」：null = 还在取 */
export type OrderableCount = { n: number } | { err: ApiError } | null;

const KNOWN_REASONS: readonly string[] = ['empty_purchase', 'zero_purchase', 'no_claimed_msku'];

const CHANGE: Record<LineInPurchase['change'], string> = { replace: '这次改了量', remove: '这次不再包含' };

/** 处置按条说：已完结 的记录没有「从采购单移掉」这条路（裁定 A） */
function howToProceed(l: LineInPurchase): string {
  const restore = l.change === 'remove'
    ? `把采购量填回原来的 ${l.total_units.from} 件` : `改回原来的 ${l.total_units.from} 件`;
  if (l.state === '已完结' || l.po_ids.length === 0) return restore;
  return `${restore}，或先从采购单里移掉这条记录`;
}

const units = (v: number | null | undefined) => (v === null || v === undefined ? '未知' : String(v));

function CellList({ cells }: { cells: SkippedCell[] }) {
  return (
    <ul>
      {cells.map((s) => (
        <li key={`${s.sku}-${s.period}`}><span className="k">{s.sku}</span> <span>{s.period}</span></li>
      ))}
    </ul>
  );
}

/** 提交结果的展示层。两块互斥：409 lines_in_purchase 时只有 `blocked`，
 *  成功（含空版本）时只有 `report` —— 由 PlanGrid.tsx 的 submit() 决定哪个非空。 */
export function SubmitPanel({ planId, report, blocked, sellers, orderable = null }: {
  planId: PlanId;
  report: SubmitResult | null;
  blocked: LinesInPurchaseBlock | null;
  sellers: Seller[] | null;
  orderable?: OrderableCount;
}) {
  const sellerName = (sid: string) => sellers?.find((s) => s.seller_id === sid)?.name ?? sid;
  const by = (reason: SkippedCell['reason']) => report?.skipped.filter((s) => s.reason === reason) ?? [];
  const empty = by('empty_purchase');
  const zero = by('zero_purchase');
  const noMsku = by('no_claimed_msku');
  // ★ 认不出的第 N+1 种原因默认显眼，不许被三个已知桶静默筛掉
  const unknown = report?.skipped.filter((s) => !KNOWN_REASONS.includes(s.reason)) ?? [];

  return (
    <>
      {blocked && (
        <div className="flash flash--bad" data-testid="lines-in-purchase">
          <div>{blocked.err.hint}</div>
          <div className="gate__code">{blocked.err.status} {blocked.err.error}</div>
          <ul>
            {blocked.lines.map((l) => (
              <li key={l.line_id} className="gate__detail">
                <span className="k">{l.sku}</span> <span>{l.period}</span>{' '}
                <span className="chip chip--dim">{l.state}</span> <span>{CHANGE[l.change]}</span>{' '}
                {l.change === 'replace' && <span>{l.total_units.from} → {l.total_units.to}</span>}{' '}
                {l.po_ids.map((po) => (
                  <Link key={po} to={`/purchase/${po}`}>采购单 {po}</Link>
                )).flatMap((a, i) => (i === 0 ? [a] : [' ', a]))}
                <div>{howToProceed(l)}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {report && (
        <div className="sec" data-testid="submit-report">
          <div className="sec__title">提交结果</div>
          <div className="gate__sum">
            <div>rev {report.rev}</div>
            <div>这一版 {report.lines} 条</div>
          </div>
          {/* ★ D5：四个数各自成列 —— 只报一个总数，人看不出哪些被换掉了 */}
          <div className="gate__sum">
            <div data-testid="count-kept">保留 {report.kept.count}</div>
            <div data-testid="count-replaced">替换 {report.replaced.count}</div>
            <div data-testid="count-added">新增 {report.added.count}</div>
            <div data-testid="count-removed">去掉 {report.removed.count}</div>
          </div>
          {report.replaced.count > 0 && (
            <Fold summary="替换明细" testId="replaced-detail">
              <ul>
                {report.replaced.lines.map((l) => (
                  <li key={l.old_line_id}>
                    <span className="k">{l.sku}</span> <span>{l.period}</span>{' '}
                    <span>{l.total_units.from} → {l.total_units.to}</span>
                  </li>
                ))}
              </ul>
            </Fold>
          )}
          {report.removed.count > 0 && (
            <Fold summary="去掉明细" testId="removed-detail">
              <ul>
                {report.removed.lines.map((l) => (
                  <li key={l.line_id}>
                    <span className="k">{l.sku}</span> <span>{l.period}</span> <span>{l.total_units}</span>
                  </li>
                ))}
              </ul>
            </Fold>
          )}
          {report.split_frozen.count > 0 && (
            <div className="muted" data-testid="split_frozen">
              <Fold summary={`已进采购，按原分店拆分保留 ${report.split_frozen.count} 条`} testId="split_frozen-detail">
                <ul>
                  {report.split_frozen.lines.map((l) => (
                    <li key={l.line_id}>
                      <span className="k">{l.sku}</span> <span>{l.period}</span>
                      <ul>
                        {[...new Set([...Object.keys(l.split.from), ...Object.keys(l.split.to)])].sort().map((sid) => (
                          <li key={sid}>{sellerName(sid)} {units(l.split.from[sid])} → {units(l.split.to[sid])}</li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              </Fold>
            </div>
          )}
          {report.left_completed.count > 0 && (
            <div className="muted" data-testid="left_completed">
              <Fold summary={`已完结，原样留着 ${report.left_completed.count} 条`} testId="left_completed-detail">
                <ul>
                  {report.left_completed.lines.map((l) => (
                    <li key={l.line_id}>
                      <span className="k">{l.sku}</span> <span>{l.period}</span> <span>rev {l.rev}</span>{' '}
                      <span>{l.total_units}</span>
                    </li>
                  ))}
                </ul>
              </Fold>
            </div>
          )}
          {/* ★ 空版本：说出「没有占在流转位」，否则人会以为提交成功了，
               然后奇怪为什么采购那边什么都没有 */}
          {report.lines === 0 && (
            <div className="note" data-testid="empty-rev">
              这一版是空的{report.in_flight === false && '，没有占在流转位'}
            </div>
          )}
          {/* ★ R1：空与 0 各自一行灰字（空 ≠ 0）；红色只给填了采购量却铸不出的 */}
          {empty.length > 0 && (
            <div className="muted" data-testid="skip-empty_purchase">
              <Fold summary={`采购量为空 ${empty.length} 个`}><CellList cells={empty} /></Fold>
            </div>
          )}
          {zero.length > 0 && (
            <div className="muted" data-testid="skip-zero_purchase">
              <Fold summary={`采购量为 0 ${zero.length} 个`}><CellList cells={zero} /></Fold>
            </div>
          )}
          {noMsku.length > 0 && (
            <div data-testid="skip-no_claimed_msku">
              <div className="gate__sum">没有已认领的 msku {noMsku.length} 个</div>
              <ul>
                {noMsku.map((s) => (
                  <li className="dropline" key={`${s.sku}-${s.period}`}>
                    <span className="k">{s.sku}</span>
                    <span>{s.period}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {unknown.length > 0 && (
            <div data-testid="skip-unknown">
              <div className="gate__sum">认不出的跳过原因 {unknown.length} 个</div>
              <ul>
                {unknown.map((s) => (
                  <li className="dropline" key={`${s.sku}-${s.period}`}>
                    <span className="k">{s.sku}</span>
                    <span>{s.period}</span>
                    <span className="gate__code">{s.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {/* ★ 不自动跳走：skipped[] 只在这一次响应里存在，由人点「去版本」。
               N 另取（orderable=1，与采购首页同一口径）；取不到就不带 N，原因折叠 —— 不猜数 */}
          {orderable !== null && 'err' in orderable && (
            <div className="muted" data-testid="orderable-unavailable">
              <Fold summary="待采购数取不到"><ErrorDetail err={orderable.err} /></Fold>
            </div>
          )}
          <div className="gate__sum">
            <Link className="btn btn--sm" to="/purchase">
              {orderable !== null && 'n' in orderable ? `去采购（${orderable.n} 条待采购）` : '去采购'}
            </Link>
            <Link className="btn btn--sm" to={`/plans/${planId}/revs`}>去版本</Link>
          </div>
        </div>
      )}
    </>
  );
}
