import { Fragment, useState } from 'react';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { useInFlight } from '../../shell/useInFlight';
import { useActionError } from '../../shell/useActionError';
import { pushToast, type Toast } from '../../shell/toastStore';
import { refreshAttention } from '../../shell/AttentionBanner';
import { api, ApiError } from '../../api';
import type { AttentionItem, AttentionKind, PoAttempt, PoTimeline, PurchaseOrder, TimelineConclusion } from '../../api/types';
import { KIND_LABEL, isUnknownOutcome, usePoll } from './poStatus';
import { fmtAt, handlerThrew, logWrite, logWriteFail, requestThrew } from './fmt';

const OUTCOME_LABEL: Record<string, string> = {
  ok: '成功', rejected: '拒绝', not_sent: '没发出', unknown: '结果未知', in_flight: '进行中',
};

const json = (v: unknown) => (v === null || v === undefined ? '无' : JSON.stringify(v, null, 2));

export function AttemptTable({ attempts }: { attempts: PoAttempt[] }) {
  if (attempts.length === 0) return <div className="muted">没有调用记录</div>;
  return (
    <div className="table-scroll">
      <table className="table table--dense" data-testid="timeline">
        <thead><tr><th>时间</th><th>人</th><th>接口</th><th className="r">耗时</th><th>结果</th><th>cause</th><th>请求 / 响应</th></tr></thead>
        <tbody>
          {attempts.map((a) => (
            <tr key={a.call_id} data-testid="attempt">
              <td className="num">{fmtAt(a.at)}</td>
              <td>{a.actor}</td>
              <td>
                <div>{a.endpoint}</div>
                <div className="muted">{a.host ?? ''}{a.path ?? ''}</div>
                {a.idem_key && <div className="muted">幂等键 {a.idem_key}</div>}
                {a.retry_of !== null && <div className="muted">接续 #{a.retry_of}</div>}
              </td>
              <td className="r num">{a.outcome === 'in_flight' ? '进行中' : a.elapsed_ms === null ? '—' : `${a.elapsed_ms} ms`}</td>
              <td>
                <div>{a.outcome === null ? '—' : OUTCOME_LABEL[a.outcome] ?? a.outcome}</div>
                {a.http_status !== null && <div className="muted">http {a.http_status}</div>}
                {a.lx_code !== null && <div className="muted">code {a.lx_code}</div>}
                {a.lx_msg !== null && <div>{a.lx_msg}</div>}
              </td>
              <td>{a.cause ?? '—'}</td>
              <td>
                <Fold summary={`#${a.call_id}`}>
                  <div className="lbl">请求体</div>
                  <pre className="mono">{json(a.request)}</pre>
                  <div className="lbl">响应体</div>
                  <pre className="mono">{json(a.response)}</pre>
                </Fold>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Lookup = NonNullable<TimelineConclusion['last_lookup']>;
function lookupText(l: Lookup | null | undefined, mismatch: boolean): string {
  // ★ 空≠0：从没查过 与 查过没找到 是两件事
  if (!l) return '还没查过';
  const what = l.outcome === 'ok'
    ? (mismatch ? '查询成功，查到的单对不上，见下方领星明细不一致' : '查询成功，没有可认定为本单的单')
    : l.outcome === 'in_flight' ? '查询进行中' : `查询失败（${l.outcome}）`;
  return `${fmtAt(l.at)} ${what}${l.truncated ? '，结果被截断，不算查无' : ''}`;
}

const FIELD_LABEL: Record<string, string> = { custom_order_sn: '幂等键', supplier_id: '供应商', status: '状态' };
interface DiffRow { label: string; ours: string; theirs: string }

/** 后端 differences 每项是 {ours, <对侧>}。对侧键名是 L8 禁词（tests/test_layering_web.py），按「不是 ours 的那一侧」取 */
const sides = (v: unknown): [unknown, unknown] => {
  const o = (v ?? {}) as Record<string, unknown>;
  return [o['ours'], Object.entries(o).find(([k]) => k !== 'ours')?.[1]];
};

function itemRows(ours: unknown, theirs: unknown): DiffRow[] {
  const index = (xs: unknown) => {
    const m = new Map<string, string[]>();
    for (const t of Array.isArray(xs) ? xs : []) {
      const [sku, wid, units, price] = t as [unknown, unknown, unknown, unknown];
      const k = `${String(sku)} ${wid === null ? '无仓' : String(wid)}`;
      m.set(k, [...(m.get(k) ?? []), `${String(units)} 件，单价 ${String(price)}`]);
    }
    return m;
  };
  const a = index(ours);
  const b = index(theirs);
  const out: DiffRow[] = [];
  for (const k of [...new Set([...a.keys(), ...b.keys()])]) {
    const x = (a.get(k) ?? []).join('；');
    const y = (b.get(k) ?? []).join('；');
    if (x !== y) out.push({ label: `明细 ${k}`, ours: x || '无', theirs: y || '无' });
  }
  return out;
}

function mismatchRows(diff: unknown): DiffRow[] {
  const rows: DiffRow[] = [];
  for (const [k, v] of Object.entries((diff ?? {}) as Record<string, unknown>)) {
    const [ours, theirs] = sides(v);
    if (k === 'items') { rows.push(...itemRows(ours, theirs)); continue; }
    rows.push({ label: FIELD_LABEL[k] ?? k, ours: String(ours ?? '无'), theirs: String(theirs ?? '无') });
  }
  return rows;
}

const PROBLEM_LABEL: Record<string, string> = {
  not_observed: '领星明细未读到', missing_item: '领星缺这一行', qty_or_price_differs: '数量或单价不同',
  unexpected_item: '领星多出这一行',
};
const side = (units: unknown, price: unknown) =>
  units === null || units === undefined ? '无' : `${String(units)} 件，单价 ${String(price ?? '—')}`;

/** G1 落地首检（usecase/landed.py → v_check_po_landed）的问题行 */
function problemRows(problems: unknown): DiffRow[] {
  return (Array.isArray(problems) ? problems : []).map((x) => {
    const p = x as Record<string, unknown>;
    const at = p['line_no'] === null || p['line_no'] === undefined ? '' : `第 ${String(p['line_no'])} 行 `;
    const what = PROBLEM_LABEL[String(p['problem'])] ?? String(p['problem']);
    return { label: `${at}${String(p['sku'] ?? '')} ${p['wid'] === null ? '无仓' : String(p['wid'] ?? '')}：${what}`,
      ours: side(p['ours_units'], p['ours_price']), theirs: side(p['lx_units'], p['lx_price']) };
  });
}

function ActionError({ err, onDismiss }: { err: ApiError; onDismiss: () => void }) {
  if (err.error === 'contradicts_evidence') {
    const sns = Array.isArray(err.fields['found_order_sns']) ? (err.fields['found_order_sns'] as unknown[]).map(String) : [];
    return (
      <div className="flash flash--bad" role="alert" data-testid="contradicts">
        <div>{err.hint}</div>
        <div>
          领星里找到过：
          {sns.length === 0 ? '（后端未列单号）' : sns.map((sn, i) => (
            <Fragment key={sn}>{i > 0 && '、'}<span className="num" data-testid="found-sn">{sn}</span></Fragment>
          ))}
        </div>
        {err.fields['open_landed_mismatch'] === true && <div>另有领星明细不一致未处理</div>}
        <div className="gate__code">{err.status} {err.error}</div>
        <button type="button" className="btn btn--sm btn--ghost" onClick={onDismiss}>关闭</button>
      </div>
    );
  }
  if (err.error === 'lookup_in_progress') {
    return (
      <div className="flash flash--bad" role="alert">
        <div>查询进行中，稍后再试</div>
        <div className="gate__code">{err.status} {err.error}</div>
        <button type="button" className="btn btn--sm btn--ghost" onClick={onDismiss}>关闭</button>
      </div>
    );
  }
  return <ErrorDetail err={err} onDismiss={onDismiss} />;
}

interface Done { toast: Omit<Toast, 'id'>; log: Record<string, unknown> }

/** 有未处理项时置顶的红框：结论 → 时间线 → 能做的动作（spec §6.6）。 */
export function AttentionPanel({ po, timeline, timelineErr, stuck, placing, onChanged }: {
  po: PurchaseOrder;
  timeline: PoTimeline | null;
  timelineErr: ApiError | null;
  /** 读时推导，不在 po.attention 里，只在 /attention 队列里 */
  stuck: AttentionItem[];
  /** 在途（任何年龄）的下单调用 */
  placing: PoAttempt | null;
  onChanged: () => Promise<void>;
}) {
  const { pending, run } = useInFlight();
  const nvErr = useActionError();
  const lmErr = useActionError();
  const [orderSn, setOrderSn] = useState('');
  const [absentReason, setAbsentReason] = useState('');
  const [ackReason, setAckReason] = useState('');
  // 查单类动作没拿到明确答复：调用可能还在跑，等调用记录里没有在途的再重取、再放开按钮
  const [unknownKey, setUnknownKey] = useState<string | null>(null);
  const busy = pending.size > 0 || unknownKey !== null;
  usePoll(unknownKey !== null, async () => {
    const t = await api.getTimeline(po.po_id);
    if (t.attempts.some((a) => a.outcome === 'in_flight')) return false;
    console.info('[po-attention] unknown outcome settled', JSON.stringify({ po_id: po.po_id, action: unknownKey }));
    setUnknownKey(null);
    refreshAttention();
    await onChanged();
    return true;
  }, 'po-attention');

  const kinds = new Set<AttentionKind>(po.attention.map((a) => a.kind));
  if (stuck.length > 0) kinds.add('stuck_call');
  const nvOpen = kinds.has('needs_verify');
  const conclusion = timeline?.conclusion ?? null;
  const lmRow = timeline?.attention.find((a) => a.kind === 'landed_mismatch' && a.resolved_at === null) ?? null;
  const lmFacts = (lmRow?.detail ?? (conclusion?.kind === 'landed_mismatch' ? conclusion.facts : null) ?? {}) as Record<string, unknown>;

  const write = (key: string, errs: ReturnType<typeof useActionError>, call: string, fn: () => Promise<Done>) =>
    run(key, async () => {
      const t0 = performance.now();
      try {
        const d = await fn();
        logWrite('po-attention', call, t0, { po_id: po.po_id, ...d.log });
        errs.succeed(key);
        pushToast(d.toast);
      } catch (e) {
        // ★ 重新核对 / 登记已有单会打领星：请求本身抛的异常算结果未知，不算「没做」
        const err = key === 'recheck' || key === 'register'
          ? requestThrew('po-attention', key, e)
          : e instanceof ApiError ? e : handlerThrew('po-attention', key, e);
        logWriteFail('po-attention', call, t0, err);
        errs.fail(key, err);
        if ((key === 'recheck' || key === 'register') && isUnknownOutcome(err)) { setUnknownKey(key); return; }
      }
      // 失败也重取：409 nothing_to_resolve 之类说明状态在别处变了
      refreshAttention();
      await onChanged();
    });

  const recheck = () => write('recheck', nvErr, `POST /purchase-orders/${po.po_id}/recheck`, async () => {
    const r = await api.recheck(po.po_id);
    const log = { outcome: r.outcome, ...(r.outcome === 'needs_verify'
      ? { lookup_outcome: r.lookup_outcome ?? null, truncated: r.truncated ?? null } : { order_sn: r.order_sn }) };
    if (r.outcome === 'placed') {
      return { log, toast: { kind: 'ok', text: `已重新核对，领星有此单 ${r.order_sn ?? '未知'}，已记为已下单` } };
    }
    const why = r.lookup_outcome && r.lookup_outcome !== 'ok' ? `，查询失败（${r.lookup_outcome}）`
      : r.truncated ? '，查询结果被截断' : '';
    return { log, toast: { kind: 'warn', text: `已重新核对，仍未认定领星有此单${why}` } };
  });

  const register = () => write('register', nvErr, `POST /purchase-orders/${po.po_id}/register-existing`, async () => {
    const sn = orderSn.trim();
    const r = await api.registerExisting(po.po_id, sn);
    setOrderSn('');
    return { log: { outcome: r.outcome, order_sn: r.order_sn }, toast: { kind: 'ok', text: `已登记领星单 ${r.order_sn}` } };
  });

  const confirmAbsent = () => write('absent', nvErr, `POST /purchase-orders/${po.po_id}/confirm-absent`, async () => {
    const r = await api.confirmAbsent(po.po_id, absentReason.trim());
    setAbsentReason('');
    return { log: { unlocked: r.unlocked }, toast: { kind: 'ok', text: '已确认领星无此单，草稿已解锁' } };
  });

  const acknowledge = () => write('ack', lmErr, `POST /purchase-orders/${po.po_id}/acknowledge-mismatch`, async () => {
    const r = await api.acknowledgeMismatch(po.po_id, ackReason.trim());
    setAckReason('');
    return { log: { resolution: r.resolution }, toast: { kind: 'ok', text: '已确认知悉' } };
  });

  const lmKnown = conclusion?.kind === 'landed_mismatch' ? conclusion.known
    : typeof lmFacts['known'] === 'string' ? lmFacts['known'] : null;
  const lmUnknown = conclusion?.kind === 'landed_mismatch' ? conclusion.unknown
    : typeof lmFacts['unknown'] === 'string' ? lmFacts['unknown'] : null;
  const diffRows = [...mismatchRows(lmFacts['differences']), ...problemRows(lmFacts['problems'])];
  const sameKeySns = Array.isArray(lmFacts['order_sns']) ? (lmFacts['order_sns'] as unknown[]).map(String) : [];

  // 只剩「卡住 / 在途」时用 warn 外框：那是等系统，不是要人立刻动手
  const onlyWaiting = !nvOpen && !kinds.has('landed_mismatch');
  return (
    <section className={`ap${onlyWaiting ? ' ap--warn' : ''}`} aria-label="需要人工处理" data-testid="attention-panel">
      <div className="ap-head">
        <span className="ap-title">⛔ 需要人工处理</span>
        {placing && <span className="chip chip--warn">下单进行中</span>}
        {[...kinds].map((k) => <span key={k} className={`chip ${k === 'stuck_call' ? 'chip--warn' : 'chip--bad'}`}>{KIND_LABEL[k]}</span>)}
      </div>

      {timelineErr && <ErrorDetail err={timelineErr} />}
      {timeline === null && !timelineErr && <div className="muted">正在取时间线…</div>}

      {nvOpen && conclusion?.kind === 'needs_verify' && (
        <dl className="ap-facts" data-testid="conclusion">
          <dt>已知</dt><dd>{conclusion.known}</dd>
          <dt>不知道</dt><dd>{conclusion.unknown}</dd>
          <dt>最近查重</dt><dd>{lookupText(conclusion.last_lookup, kinds.has('landed_mismatch'))}</dd>
          {conclusion.how_to_search && (
            <>
              <dt>去领星查</dt>
              <dd>
                幂等键 <span className="num">{conclusion.how_to_search.custom_order_sn ?? '—'}</span>
                {conclusion.how_to_search.window['search_field_time'] === 'create_time' ? '，创建时间 ' : '，时间 '}
                {String(conclusion.how_to_search.window['start_date'] ?? '—')} 至 {String(conclusion.how_to_search.window['end_date'] ?? '—')}
                ，供应商 {po.supplier_id}，货号 {[...new Set(po.lines.map((l) => l.sku))].join('、') || '—'}
              </dd>
            </>
          )}
        </dl>
      )}

      {placing && (
        <div className="ap-note" data-testid="attn-placing">
          下单进行中：开始于 {fmtAt(placing.at)}，{placing.actor} 发起；落地后自动刷新
        </div>
      )}
      {unknownKey && (
        <div className="flash flash--warn" role="alert" data-testid="attn-unknown">
          结果未知：正在每 3 秒查一次调用记录，查完自动刷新
        </div>
      )}

      {nvOpen && (
        <div className="ap-acts" data-testid="attn-needs_verify">
          <div className="ap-act">
            <button type="button" className="btn" disabled={busy} onClick={() => void recheck()}>重新核对</button>
            <span className="ap-act__d">按幂等键再查一次</span>
          </div>
          <div className="ap-act">
            <input className="pe-reason" aria-label="领星单号" placeholder="领星单号，如 PO261010001" value={orderSn} disabled={busy}
              onChange={(e) => setOrderSn(e.target.value)} />
            <button type="button" className="btn" disabled={busy || orderSn.trim() === ''}
              onClick={() => void register()}>登记已有单</button>
            <span className="ap-act__d">对得上才接受</span>
          </div>
          <div className="ap-act">
            <input className="pe-reason" aria-label="确认无此单的理由" placeholder="理由（必填）" value={absentReason} disabled={busy}
              onChange={(e) => setAbsentReason(e.target.value)} />
            <button type="button" className="btn btn--danger" disabled={busy || absentReason.trim() === ''}
              onClick={() => void confirmAbsent()}>确认领星无此单</button>
            <span className="ap-act__d">解锁改、删、再下单</span>
          </div>
          {nvErr.err && <ActionError err={nvErr.err} onDismiss={nvErr.dismiss} />}
        </div>
      )}

      {kinds.has('landed_mismatch') && (
        <div className="ap-acts" data-testid="attn-landed_mismatch">
          <dl className="ap-facts">
            {lmKnown && <><dt>已知</dt><dd>{lmKnown}</dd></>}
            {lmUnknown && <><dt>不知道</dt><dd>{lmUnknown}</dd></>}
            {typeof lmFacts['order_sn'] === 'string' && <><dt>领星单</dt><dd className="num">{lmFacts['order_sn']}</dd></>}
            {sameKeySns.length > 0 && <><dt>同幂等键</dt><dd>领星里同幂等键的单：{sameKeySns.join('、')}</dd></>}
            {typeof lmFacts['also_found_order_sn'] === 'string' && (
              <><dt>另查到</dt><dd>已记账 <span className="num">{String(lmFacts['recorded_order_sn'] ?? '—')}</span>，另查到 <span className="num">{lmFacts['also_found_order_sn']}</span></dd></>
            )}
          </dl>
          {diffRows.length > 0 && (
            <table className="pd-t" data-testid="mismatch-rows">
              <thead><tr><th>项</th><th>我们发的</th><th>领星里的</th></tr></thead>
              <tbody>
                {diffRows.map((r) => (
                  <tr key={r.label} data-testid="mismatch-row"><td>{r.label}</td><td>{r.ours}</td><td className="ap-theirs">{r.theirs}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          {/* ★ PB-D16：草稿上没有「接受差异」出口（后端 409 mismatch_on_draft），只能去领星处理 */}
          {po.state === '已下单' ? (
            <div className="ap-act">
              <input className="pe-reason" aria-label="确认知悉的理由" placeholder="理由（必填）" value={ackReason} disabled={busy}
                onChange={(e) => setAckReason(e.target.value)} />
              <button type="button" className="btn" disabled={busy || ackReason.trim() === ''}
                onClick={() => void acknowledge()}>确认知悉</button>
              <span className="ap-act__d">不改任何单</span>
            </div>
          ) : (
            <div className="ap-note" data-testid="pbd16">
              {nvOpen ? '去领星改对后点重新核对，或在领星作废该单（草稿阻塞至作废回读）' : '在领星作废该单；草稿阻塞至作废回读'}
            </div>
          )}
          {lmErr.err && <ActionError err={lmErr.err} onDismiss={lmErr.dismiss} />}
        </div>
      )}

      {stuck.map((s) => (
        <div className="ap-note" key={`stuck-${s.call_id ?? s.opened_at}`} data-testid="attn-stuck_call">
          {KIND_LABEL.stuck_call}：等待自动恢复。开始于 {fmtAt(s.started_at ?? s.opened_at)}，{s.endpoint ?? '调用'} 超过 {s.stale_after_minutes ?? '—'} 分钟未结束
        </div>
      ))}

      {timeline && <Fold summary={`调用记录 ${timeline.attempts.length} 次`}><AttemptTable attempts={timeline.attempts} /></Fold>}
    </section>
  );
}
