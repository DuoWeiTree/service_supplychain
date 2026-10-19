import { useId, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { LxObservation, PreflightGate } from '../../api/types';
import { fmtAt } from './fmt';
import { gateText } from './poEditorModel';

// 码表照领星 PurchaseOrderList 文档（data>>status / status_shipped）；认不出的原样写码，不猜
const LX_STATUS: Record<number, string> = {
  [-1]: '作废', 3: '待提交', 1: '待下单', 2: '待到货', 9: '完成', 121: '审批流待审核', 122: '审批流驳回', 124: '审批流作废',
};
const LX_SHIPPED: Record<number, string> = { 1: '未到货', 2: '部分到货', 3: '全部到货' };
export const lxStatusText = (s: number) => LX_STATUS[s] ?? `状态码 ${s}`;
export const lxShippedText = (s: number) => LX_SHIPPED[s] ?? `到货码 ${s}`;

/** ⛔ 不可逆动作：和所有普通按钮长得不一样，永远单独放在最右；被挡时在左边点名原因 */
export function CommitButton({ hint, hintTestId = 'place-blocked', children, className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & {
  hint?: string | null; hintTestId?: string;
}) {
  // 不挂 role=status：那是 Toasts 的查询口（同 AttentionBanner 的说明）；原因经 aria-describedby 读给按钮
  const hintId = useId();
  return (
    <span className="sc-commit-wrap">
      {hint && <span className="sc-commit__hint" id={hintId} data-testid={hintTestId}>{hint}</span>}
      <button type="button" {...rest} aria-describedby={hint ? hintId : undefined}
        className={['sc-commit', className].filter(Boolean).join(' ')}>
        <span className="sc-commit__mark">⛔</span>{' '}{children}
      </button>
    </span>
  );
}

export type LocalState = '草稿' | '草稿（已作废）' | '已下单';

/** 两轴状态：本地 | 领星，并排不合成（`10` 原则二）。领星一侧永远带读回时间 */
export function StatusPair({ local, lx, reading, readError, onReread, children }: {
  local: LocalState;
  lx: LxObservation | null;
  reading?: boolean;
  /** 这次读回失败的原因码；lx 仍是上一次的观察 */
  readError?: string | null;
  onReread?: (() => void) | undefined;
  /** 失败原因等折叠内容 */
  children?: ReactNode;
}) {
  const draft = local !== '已下单';
  return (
    <div className="sp" data-testid="po-axes">
      <div className="sp-axis">
        <span className="sp-k">本地</span>{' '}
        <span className={`sp-v${local === '已下单' ? ' sp-v--ok' : ''}`}>{local}</span>
      </div>
      <div className="sp-axis sp-axis--lx" data-testid="lx-status">
        <span className="sp-k">领星</span>{' '}
        {draft ? <span className="sp-v sp-v--muted">未下单</span>
          : lx ? (
            <>
              <span className={`sp-v${lx.status === -1 ? ' sp-v--danger' : ''}`}>{lxStatusText(lx.status)}</span>{' '}
              {lx.status_shipped != null && <><span className="sp-v sp-v--sub">{lxShippedText(lx.status_shipped)}</span>{' '}</>}
              <span className="sp-at">读回于 {fmtAt(lx.captured_at)}</span>
            </>
          ) : <span className="sp-v sp-v--muted">{reading ? '正在向领星查询…' : '未读回'}</span>}
        {reading && lx && <span className="sp-at">正在向领星查询…</span>}
        {onReread && (
          <button type="button" className="btn btn--sm btn--ghost" disabled={reading} onClick={onReread}>
            {reading ? '查询中…' : '重新读回'}
          </button>
        )}
      </div>
      {readError && (
        <div className="sp-err" role="alert">
          这次读回失败（{readError}）{lx ? '，显示的是上次读回的状态' : ''}
        </div>
      )}
      {children}
    </div>
  );
}

/** 闸的 detail（后端 / 本地同形）→ 一句「为什么没过」；认不出的形状不编，留空 */
export function gateDetail(g: PreflightGate): string | null {
  const d = g.detail ?? {};
  switch (g.gate) {
    case 'line_units': return `数量 ${String(d['units'])} < 来源合计 ${String(d['sources'])}`;
    case 'line_unplanned_reason': return `无计划来源 ${String(d['unplanned'])} 件，未写理由`;
    case 'qc_type': return '未选';
    case 'single_wid': {
      const w = d['wids'];
      return Array.isArray(w) && w.length > 1 ? `明细分属仓 ${w.join('、')}` : null;
    }
    case 'sources_orderable': {
      const off = d['offenders'];
      if (Array.isArray(off) && off.length > 0) {
        return off.map((o) => `记录 #${String((o as Record<string, unknown>)['line_id'])} ${String((o as Record<string, unknown>)['state'])}`).join('、');
      }
      return d['loaded'] === false ? '来源记录状态没取到' : null;
    }
    case 'line_price': return '未填或不是非负数';
    case 'line_wid': return '未选仓';
    case 'lines': return '还没有明细';
    default: return '未填';
  }
}

/** 下单前检查：一次列全，未过的排前面；带后端闸名便于对照 */
export function GateList({ gates, source }: { gates: PreflightGate[]; source: string }) {
  const bad = gates.filter((g) => !g.ok);
  const ordered = [...bad, ...gates.filter((g) => g.ok)];
  return (
    <section className="gl" aria-label="下单前检查" data-testid="preflight">
      <div className="gl-head">
        <span className="gl-title">下单前检查</span>
        <span className={`chip ${bad.length ? 'chip--bad' : 'chip--ok'}`} data-testid="gate-summary">
          {bad.length ? `${bad.length} 道未过` : '全部通过'}
        </span>
        <span className="gl-src">{source}</span>
      </div>
      <ol className="gl-list">
        {ordered.map((g, i) => (
          <li key={`${g.gate}#${String(g.detail?.['line_no'] ?? i)}`} className={`gl-item ${g.ok ? 'is-ok gate__item--pass' : 'is-bad gate__item--fail'}`}>
            <span className="gl-mark" aria-label={g.ok ? '通过' : '未过'}>{g.ok ? '✓' : '✕'}</span>
            <span className="gl-label">{gateText(g)}</span>
            {!g.ok && gateDetail(g) && <span className="gl-detail">{gateDetail(g)}</span>}
            <code className="gl-key">{g.gate}</code>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** UX-D6：页内面包屑「采购 / 单号」，「采购」回列表并定位到来源计划（AppShell 的 crumb 只是一段字） */
export function PoCrumbs({ home, here }: { home: string; here: string }) {
  return (
    <nav className="pe-crumbs" aria-label="面包屑" data-testid="po-crumbs">
      <Link to={home}>采购</Link><span className="pe-crumbs__sep" aria-hidden="true">/</span><span>{here}</span>
    </nav>
  );
}
