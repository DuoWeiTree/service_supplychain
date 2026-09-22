import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { api, ApiError } from '../api';
import type { CatalogMsku, CatalogResult, ClaimHolder } from '../api/types';

interface Outcome {
  seller_sku: string;
  sid: string;
  ok: boolean;
  /** 成功给「已认领」；失败给后端的 hint（人话），不是裸错误码 */
  hint: string;
  /** ★ 被拒时点名是被谁占的；不预先拼成一句话，留给渲染层分开落地（判据①1） */
  holder: ClaimHolder | null;
}

const EMPTY: CatalogResult = { need_query: true, truncated: false, limit: 0, items: [] };

export function PlanAdd() {
  const planId = Number(useParams().planId);
  const [q, setQ] = useState('');
  const [result, setResult] = useState<CatalogResult>(EMPTY);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [report, setReport] = useState<Outcome[] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  // ★ 没有历史 ≠ 预估 0（api/ui/plans.py:163-170）：认领这一下点了名，
  //   标记要留到本次会话结束，不是闪一下 toast 就没了
  const [noHistory, setNoHistory] = useState<Set<string>>(new Set());

  const key = (m: { seller_sku: string; sid: string }) => `${m.seller_sku}/${m.sid}`;

  // ★ 真实契约（api/ui/plans.py:156-160，经 http.ts 的 {error,hint,...fields} 展开）是
  //   fields.claimed_by 嵌套对象；兜底再认一种打平在顶层的旧形态，两种都不丢占用方
  function holderFrom(ae: ApiError): ClaimHolder | null {
    const nested = ae.fields['claimed_by'];
    if (nested && typeof nested === 'object') return nested as ClaimHolder;
    if (ae.fields['title']) {
      return { plan_id: Number(ae.fields['plan_id'] ?? 0), title: String(ae.fields['title']), actor: String(ae.fields['actor'] ?? '') };
    }
    return null;
  }

  async function search() {
    try { setResult(await api.searchCatalog(q.trim() === '' ? {} : { q: q.trim() })); setErr(null); }
    catch (e) { setErr(e as ApiError); }
  }

  function toggle(m: CatalogMsku) {
    setPicked((s) => { const n = new Set(s); const k = key(m); n.has(k) ? n.delete(k) : n.add(k); return n; });
  }

  async function add() {
    const targets = result.items.flatMap((s) => s.mskus).filter((m) => picked.has(key(m)));
    const out: Outcome[] = [];
    const freshNoHistory: string[] = [];
    for (const m of targets) {
      try {
        const r = await api.claim(planId, { seller_sku: m.seller_sku, sid: m.sid });
        out.push({ seller_sku: m.seller_sku, sid: m.sid, ok: true, hint: '已认领', holder: null });
        for (const h of r.no_history ?? []) freshNoHistory.push(`${h.seller_sku}/${h.sid}`);
      } catch (e) {
        const ae = e as ApiError;
        // ★ 原则五：一次列全，不是修一条报一条 —— 被拒的继续往下做，最后一起交代
        out.push({ seller_sku: m.seller_sku, sid: m.sid, ok: false, hint: ae.hint, holder: holderFrom(ae) });
      }
    }
    setReport(out);
    setPicked(new Set());
    if (freshNoHistory.length > 0) {
      setNoHistory((s) => { const n = new Set(s); freshNoHistory.forEach((k) => n.add(k)); return n; });
    }
    await search();
    const bad = out.filter((o) => !o.ok).length;
    const ok = out.length - bad;
    // ★ 页面文案不用西式中点拼句 —— 分句用中文标点，占用方另起结构化片段
    let text = `已加入 ${ok} 个 msku`;
    if (bad > 0) text += `；被拒 ${bad}`;
    if (freshNoHistory.length > 0) text += `；${freshNoHistory.length} 个没有销售历史，系统预估为空，需要人填`;
    pushToast({ kind: bad === 0 ? 'ok' : 'warn', text });
  }

  const unbuildable = result.items.flatMap((s) => s.unbuildable_sellers.map((u) => ({ ...u, sku: s.sku })));

  return (
    <AppShell crumb="添加货品">
      <div className="head">
        <div className="head__main"><h1>添加货品</h1></div>
        <div className="head__act">
          <span className="muted" data-testid="picked">已选 {picked.size}</span>
          <button type="button" className="btn btn--primary" disabled={picked.size === 0} onClick={() => void add()}>添加</button>
          <Link className="btn btn--ghost" to={`/plans/${planId}`}>返回网格</Link>
        </div>
      </div>

      <form className="bar" onSubmit={(e) => { e.preventDefault(); void search(); }}>
        <label className="bar__grp"><span className="bar__lbl">搜货号</span>
          <input className="inp inp--text" aria-label="搜货号" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <button type="submit" className="btn">搜索</button>
      </form>

      {/* ★ 两种空屏，两套字 —— 合成一种就分不清「还没搜」和「搜了没有」 */}
      {result.need_query && (
        <div className="empty" data-testid="need-query"><p className="empty__title">输入货号后搜索</p></div>
      )}
      {/* ★ 判空只用 items.length：matched 是可选字段，缺了就会把「搜了没有」显示成「还没搜」 */}
      {!result.need_query && result.items.length === 0 && (
        <div className="empty" data-testid="no-hit"><p className="empty__title">没有命中的货号</p></div>
      )}

      {result.truncated && (
        <div className="flash flash--bad">
          命中{result.matched === undefined ? '' : ` ${result.matched} 条`}，只列前 {result.limit} 条，缩小搜索条件
        </div>
      )}

      {unbuildable.length > 0 && (
        <div className="sec" data-testid="unbuildable">
          {unbuildable.map((u) => (
            <div className="dropline" key={`${u.sku}-${u.sid}`}>
              <span className="k">建不出格子</span>
              <span>{u.sku}</span>
              <span>sid {u.sid}</span>
              <span className="gate__code">{u.reason}</span>
            </div>
          ))}
        </div>
      )}

      {result.items.map((item) => (
        <div className="sec" key={item.sku}>
          <div className="sec__title">
            <span>{item.sku}</span>
            <span className="muted">{item.name}</span>
            {item.claimed_by === null && item.claimed_by_plans.length > 1 && (
              // ★ 占用方不唯一时挑一个显示就是编 —— 列全部名单；中文顿号枚举，不拼西式中点
              <span className="chip chip--warn">
                {item.claimed_by_plans.map((p) => p.title).join('、')}
              </span>
            )}
          </div>
          <div className="table-scroll">
            <table className="table table--dense">
              <thead><tr><th /><th>msku</th><th>店铺</th><th>sid</th><th>占用</th></tr></thead>
              <tbody>
                {item.mskus.map((m) => (
                  <tr key={key(m)} className={m.selectable ? undefined : 'off'} data-testid={`msku-${m.seller_sku}`}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`认领 ${m.seller_sku}`}
                        disabled={!m.selectable}
                        checked={picked.has(key(m))}
                        onChange={() => toggle(m)}
                      />
                    </td>
                    <td>
                      {m.seller_sku}
                      {noHistory.has(key(m)) && (
                        <span className="chip chip--warn" data-testid={`no-history-${m.seller_sku}`}>无历史</span>
                      )}
                    </td>
                    <td>{m.seller_name}</td>
                    <td>{m.sid}</td>
                    <td>
                      {m.claimed_by ? (
                        <span className="i-red">
                          <span className="k">计划</span><span>{m.claimed_by.title}</span>
                          <span className="k">操作人</span><span>{m.claimed_by.actor}</span>
                        </span>
                      ) : <span className="muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {report && (
        <div className="sec" data-testid="claim-report">
          <div className="gate__sum">
            成功 {report.filter((r) => r.ok).length}；被拒 {report.filter((r) => !r.ok).length}
          </div>
          <ul>
            {report.map((r) => (
              <li className={`gate__item gate__item--${r.ok ? 'pass' : 'fail'}`} key={`${r.seller_sku}/${r.sid}`}>
                <span>{r.seller_sku}</span>{' '}
                <span className="gate__detail">{r.hint}</span>
                {r.holder && (
                  <span className="gate__detail">
                    <span className="k">计划</span><span>{r.holder.title}</span>
                    <span className="k">操作人</span><span>{r.holder.actor}</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {err && <ErrorDetail err={err} />}
    </AppShell>
  );
}
