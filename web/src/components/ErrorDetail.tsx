import type { ApiError } from '../api/client';

// ★ 原则五：闸失败要点名 —— 一次列全，不是修一条报一条
/** `onDismiss` 给**写动作**的错误用：它只由「同一个动作下次成功」或人点关闭收掉，
 *  不许被下一次 load() 顺手抹掉（T4 R2 裁定）。读接口的逐段错误不传它 ——
 *  那一段重新取数成功时本来就该自己消失，给个关不掉的按钮反而是假承诺。 */
export function ErrorDetail({ err, onDismiss }: { err: ApiError; onDismiss?: () => void }) {
  const rows = Object.entries(err.fields);
  return (
    <div className="flash flash--bad" role="alert">
      <div>{err.hint}</div>
      <div className="gate__code">{err.status} {err.error}</div>
      {rows.length > 0 && (
        <ul>
          {rows.map(([k, v]) => (
            <li key={k} className="gate__detail">
              <span className="muted">{k}</span> {typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}
            </li>
          ))}
        </ul>
      )}
      {onDismiss && (
        <button type="button" className="btn btn--sm btn--ghost" onClick={onDismiss}>关闭</button>
      )}
    </div>
  );
}
