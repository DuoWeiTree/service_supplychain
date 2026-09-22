import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { Modal } from '../components/Modal';
import { api, ApiError } from '../api';
import type { PlanDiff, RevList } from '../api/types';

interface CancelDone { cancelled: number; skippedTerminal: number; reason: string }

export function PlanRevs() {
  const planId = Number(useParams().planId);
  const [list, setList] = useState<RevList | null>(null);
  const [from, setFrom] = useState('');
  const [diff, setDiff] = useState<PlanDiff | null>(null);
  const [cancelling, setCancelling] = useState<number | null>(null);
  const [reason, setReason] = useState('');
  const [done, setDone] = useState<CancelDone | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  const load = () => api.listRevs(planId)
    .then((r) => { setList(r); setErr(null); })
    .catch((e: ApiError) => setErr(e));
  useEffect(() => { void load(); }, [planId]);

  if (!list) return <AppShell crumb="版本">{err ? <ErrorDetail err={err} /> : <div className="empty" />}</AppShell>;

  async function setCurrent(rev: number) {
    try {
      await api.setCurrentRev(planId, rev);
      await load();
      pushToast({ kind: 'ok', text: `rev ${rev} 已设为当前使用` });
    } catch (e) { setErr(e as ApiError); }
  }

  async function compare() {
    if (from === '' || !list) return;
    try { setDiff(await api.diff(planId, Number(from), list.current_rev ?? 0)); setErr(null); }
    catch (e) { setErr(e as ApiError); }
  }

  async function doCancel() {
    if (cancelling === null) return;
    try {
      const r = await api.cancelRev(planId, cancelling, reason);
      // ★ 先把列表刷新完，再报「done」—— done 是界面上最后落地的那个状态变化，
      //   这样测试等到 cancel-report 出现时，重新加载已经完成，不留悬空的异步更新。
      await load();
      // ★ CancelRevResult 保证 skipped_terminal 与 reason 两个字段（api/ui/submit.py:213-214）——
      //   直接读服务端回的这份，不是本地输入框此刻的值：这是「记在每条记录上」的那份理由。
      setDone({ cancelled: r.cancelled.length, skippedTerminal: r.skipped_terminal, reason: r.reason });
      pushToast({ kind: 'ok', text: `已撤销 ${r.cancelled.length} 条记录` });
      setCancelling(null);
      setReason('');
      setErr(null);
    } catch (e) { setErr(e as ApiError); }
  }

  return (
    <AppShell crumb="版本">
      <div className="head">
        <div className="head__main"><h1>版本</h1></div>
        <div className="head__act"><a className="btn btn--ghost" href={`/plans/${planId}`}>返回网格</a></div>
      </div>

      <div className="table-scroll">
        <table className="table table--dense">
          <thead>
            <tr>
              <th className="r">rev</th><th>提交人</th><th>提交时间</th><th className="r">记录</th>
              <th>digest</th><th>标记</th><th />
            </tr>
          </thead>
          <tbody>
            {list.revs.map((r) => (
              <tr key={r.rev} data-testid={`rev-${r.rev}`}>
                <td className="r">{r.rev}</td>
                <td>{r.submitted_by}</td>
                <td className="muted">{r.submitted_at.slice(0, 16).replace('T', ' ')}</td>
                <td className="r">{r.lines}</td>
                <td className="gate__code">{r.content_digest}</td>
                <td>
                  {/* ★ 两个标记分开：流转中由采购/排货消费，当前使用由算需求读（06 §1.3）——
                      合成一个标记就分不清「为什么改了当前使用，采购那边还是老的」 */}
                  {r.rev === list.in_flight_rev && <span className="chip chip--ours">流转中</span>}{' '}
                  {r.rev === list.current_rev && <span className="chip chip--ok">当前使用</span>}
                </td>
                <td>
                  {r.rev !== list.current_rev && (
                    <button type="button" className="btn btn--sm" onClick={() => void setCurrent(r.rev)}>设为当前使用</button>
                  )}{' '}
                  <button type="button" className="btn btn--sm btn--danger" onClick={() => setCancelling(r.rev)}>撤销</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="bar">
        <label className="bar__grp">
          <span className="bar__lbl">比较自</span>
          <select className="inp" aria-label="比较自" value={from} onChange={(e) => setFrom(e.target.value)}>
            <option value="">选一版</option>
            {list.revs.map((r) => <option key={r.rev} value={r.rev}>rev {r.rev}</option>)}
          </select>
        </label>
        <button type="button" className="btn" disabled={from === ''} onClick={() => void compare()}>比较</button>
      </div>

      {diff && (
        <>
          <DiffBlock
            title="新增" testId="diff-added" head={['货号', '月', '数量']}
            rows={diff.added.map((r) => ({
              id: `added-${r.sku}-${r.period}`, sku: r.sku, period: r.period, cells: [String(r.total_units)],
            }))}
          />
          <DiffBlock
            title="移除" testId="diff-removed" head={['货号', '月', '数量']}
            rows={diff.removed.map((r) => ({
              id: `removed-${r.sku}-${r.period}`, sku: r.sku, period: r.period, cells: [String(r.total_units)],
            }))}
          />
          <DiffBlock
            title="改动" testId="diff-changed" head={['货号', '月', `rev ${from} 数量`, '当前数量', '原需求', '当前需求']}
            rows={diff.changed.map((r) => ({
              id: `changed-${r.sku}-${r.period}`, sku: r.sku, period: r.period,
              // ★ 两个数并排给，不合成一个增减
              cells: [String(r.total_units.from), String(r.total_units.to), String(r.demand_at_submit.from), String(r.demand_at_submit.to)],
            }))}
          />
        </>
      )}

      {done && (
        <div className="flash flash--good" data-testid="cancel-report">
          <div>
            已撤销 {done.cancelled} 条记录
            {done.skippedTerminal > 0 && `，另有 ${done.skippedTerminal} 条已在终态，未动`}
          </div>
          <div className="muted">理由：{done.reason}</div>
        </div>
      )}

      {cancelling !== null && (
        <Modal title={`撤销 rev ${cancelling}`} danger onClose={() => setCancelling(null)}>
          <label className="field">
            <span className="lbl">理由</span>
            <input className="inp inp--text" aria-label="理由" value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="btn-row">
            <button type="button" className="btn btn--danger" onClick={() => void doCancel()}>确认撤销</button>
            <button type="button" className="btn btn--ghost" onClick={() => setCancelling(null)}>取消</button>
          </div>
          {err && <ErrorDetail err={err} />}
        </Modal>
      )}

      {err && cancelling === null && <ErrorDetail err={err} />}
    </AppShell>
  );
}

interface DiffRowView { id: string; sku: string; period: string; cells: string[] }

/** ★ 三个数组各自成块。空的写「无」—— 整块消失会让人以为这一类不存在 */
function DiffBlock({ title, testId, head, rows }: {
  title: string; testId: string; head: string[]; rows: DiffRowView[];
}) {
  return (
    <div className="sec" data-testid={testId}>
      <div className="sec__title"><span>{title}</span><span className="muted">{rows.length}</span></div>
      {rows.length === 0 ? <div className="todos__empty">无</div> : (
        <div className="table-scroll">
          <table className="table table--dense">
            <thead><tr>{head.map((h) => <th key={h} className="r">{h}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} data-testid={r.id}>
                  <td>{r.sku}</td>
                  <td>{r.period}</td>
                  {r.cells.map((c, i) => <td className="r num" key={i}>{c}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
