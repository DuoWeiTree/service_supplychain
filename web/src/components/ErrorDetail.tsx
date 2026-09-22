import type { ApiError } from '../api/client';

// ★ 原则五：闸失败要点名 —— 一次列全，不是修一条报一条
export function ErrorDetail({ err }: { err: ApiError }) {
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
    </div>
  );
}
