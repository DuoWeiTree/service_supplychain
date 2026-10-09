import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../api';
import { ErrorDetail } from '../components/ErrorDetail';
import { asApiError } from '../api/client';

const listeners = new Set<() => void>();

/** 写动作之后调：让所有挂着的红条重新取数。不轮询 —— 没有这条需求。 */
export function refreshAttention(): void {
  listeners.forEach((fn) => fn());
}

type State =
  | { kind: 'loading' }
  | { kind: 'ok'; count: number }
  | { kind: 'fail'; err: ApiError };


export function AttentionBanner() {
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    let alive = true;
    let seq = 0;
    const load = () => {
      const mine = ++seq;
      const t0 = performance.now();
      // 包一层 then：api 同步抛（桩缺方法）也落进 catch，不让红条位悄悄消失
      Promise.resolve().then(() => api.listAttention()).then(
        (q) => { if (alive && mine === seq) setState({ kind: 'ok', count: q.count }); },
        (e: unknown) => {
          const err = asApiError(e);
          console.warn('[attention] listAttention failed', JSON.stringify({
            status: err.status, error: err.error, fields: err.fields,
            ms: Math.round(performance.now() - t0),
          }));
          if (alive && mine === seq) setState({ kind: 'fail', err });
        },
      );
    };
    listeners.add(load);
    load();
    return () => { alive = false; listeners.delete(load); };
  }, []);

  // 容器先于内容进 DOM：live region 必须在内容出现前就存在，读屏才会播报后到的红条。
  // 只给 aria-live=polite、不挂 role：张数是常驻队列信息，不该打断正在做的事；
  // 且 role=status 已是 Toasts 的、role=alert 已是各页错误的查询口，再占一个会让两边的测试与读屏导航都多出空地标。
  // 取不到时内部的 ErrorDetail 自带 role=alert，那才是需要立刻知道的。
  let body: ReactNode = null;
  if (state.kind === 'fail') {
    body = (
      <div className="wrap mt">
        <div className="muted">待处理数取不到</div>
        <ErrorDetail err={state.err} />
      </div>
    );
  } else if (state.kind === 'ok' && state.count > 0) {
    body = (
      <div className="wrap mt">
        <Link className="flash flash--bad attention" to="/purchase">
          ⛔ {state.count} 张采购单需要人工处理
        </Link>
      </div>
    );
  }
  return <div aria-live="polite" aria-atomic="true" data-testid="attention-region">{body}</div>;
}
