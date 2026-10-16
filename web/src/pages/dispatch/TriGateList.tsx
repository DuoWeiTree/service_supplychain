import type { ReactNode } from 'react';
import type { DispatchGate } from '../../api/types';

const LABEL: Record<DispatchGate['gate'], string> = {
  G1: 'FBA 行装箱填齐', G2: 'FBA 排货单有店铺', G3: '本地可发够',
  G4: '打开后别的排货表没改过同一货号 × 来源仓', G5: '疑似未关联的发货', G6: '目的地合法', G7: '海外仓排货单选了物流方式',
};

/** 闸 detail（后端形状）→ 一句话；认不出的形状不编 */
export function gateDetail(g: DispatchGate): string {
  const d = g.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d.length > 0) {
    if (g.gate === 'G1') return `${d.length} 行没填齐`;
    if (g.gate === 'G3') return d.map((x) => {
      const r = x as { sku: string; from_wid: number; short: number };
      return `${r.sku} 差 ${r.short} 件`;
    }).join('；');
    if (g.gate === 'G7') return `${d.length} 个海外仓目的地没选物流方式（或已停用）`;
    if (g.gate === 'G4') return `排货表 ${d.map((x) => `#${String((x as { sheet_id: number }).sheet_id)}`).join('、')} 刚改过同一货号`;
    return `${d.length} 处`;
  }
  if (g.ok === null && d && typeof d === 'object') {
    const kind = (d as { cause?: { kind?: string } }).cause?.kind;
    return `库存读不到（${kind ?? '原因未知'}），无法判定`;
  }
  return '';
}

/** 三态闸：✓ 过 · ✕ 未过 · ？无法判定 / 未检查（★ 不显示成通过） */
/** `actions`：未过时挂在那道闸后面的「去修」按钮（闸自己不知道页面结构，由页面传进来） */
export function TriGateList({ gates, source, actions }: {
  gates: DispatchGate[]; source?: string; actions?: Partial<Record<DispatchGate['gate'], ReactNode>>;
}) {
  const bad = gates.filter((g) => g.ok === false);
  const unk = gates.filter((g) => g.ok === null);
  const ordered = [...bad, ...unk, ...gates.filter((g) => g.ok === true)];
  return (
    <section className="gl" aria-label="确认检查" data-testid="gates">
      <div className="gl-head">
        <span className="gl-title">确认检查</span>
        {bad.length > 0 && <span className="chip chip--bad">{bad.length} 道未过</span>}
        {unk.length > 0 && <span className="chip chip--warn">{unk.length} 道无法判定 / 未检查</span>}
        {bad.length === 0 && unk.length === 0 && <span className="chip chip--ok">全部通过</span>}
        {source && <span className="gl-src">{source}</span>}
      </div>
      <ol className="gl-list gl-list--one">
        {ordered.map((g) => {
          const st = g.ok === true ? 'is-ok' : g.ok === false ? 'is-bad' : 'is-unk';
          const detail = gateDetail(g);
          return (
            <li key={g.gate} className={`gl-item ${st}`} data-testid={`gate-${g.gate}`}>
              <span className="gl-mark" aria-label={g.ok === true ? '通过' : g.ok === false ? '未过' : '无法判定'}>
                {g.ok === true ? '✓' : g.ok === false ? '✕' : '？'}
              </span>
              <span className="gl-label"><b className="gl-g">{g.gate}</b> {LABEL[g.gate]}</span>
              {g.ok !== true && (detail || g.fix) && (
                <span className="gl-detail">{[detail, g.fix && g.fix !== '—' ? `→ ${g.fix}` : ''].filter(Boolean).join('　')}</span>
              )}
              {g.ok !== true && <code className="gl-key">{g.code}</code>}
              {g.ok === false && actions?.[g.gate]}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
