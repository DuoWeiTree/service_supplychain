/** ★ 空 ≠ 0 · 不适用 ≠ 0 · 不可求和 ≠ 未知 —— 这三条在本项目各咬过一次，所以给三个字形 */
export type QtyValue =
  | { kind: 'num'; value: number }
  | { kind: 'unknown' }
  | { kind: 'na' }
  | { kind: 'nosum' };

export function Qty({ v, big = false }: { v: QtyValue; big?: boolean }) {
  if (v.kind === 'unknown') return <span className="cell__unknown">—</span>;
  if (v.kind === 'nosum') return <span className="cell__unknown">不可求和</span>;
  if (v.kind === 'na') return <span className="chip chip--dim">不适用</span>;
  const cls = big ? 'cell__close num' : `qty num${v.value === 0 ? ' qty--zero' : ''}`;
  return <span className={cls}>{v.value}</span>;
}
