import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../api';
import { ErrorDetail } from '../components/ErrorDetail';

const listeners = new Set<() => void>();

/** 写动作之后调：让所有挂着的红条重新取数。不轮询 —— 没有这条需求。 */
export function refreshAttention(): void {
  listeners.forEach((fn) => fn());
}

type State =
  | { kind: 'loading' }
  | { kind: 'ok'; count: number }
  | { kind: 'fail'; err: ApiError };

// 非 ApiError（建连失败等）也要落成可显示的明细，cause.code 是唯一能分辨「超时」与「连不上」的线索
function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  const err = e as { name?: string; message?: string; cause?: { code?: string } };
  return new ApiError(0, err.name ?? 'Error', err.message ?? String(e),
    err.cause?.code ? { cause_code: err.cause.code } : {});
}

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

  if (state.kind === 'loading' || (state.kind === 'ok' && state.count === 0)) return null;
  if (state.kind === 'fail') {
    return (
      <div className="wrap mt">
        <div className="muted">待处理数取不到</div>
        <ErrorDetail err={state.err} />
      </div>
    );
  }
  return (
    <div className="wrap mt">
      <Link className="flash flash--bad attention" to="/purchase">
        ⛔ {state.count} 张采购单需要人工处理
      </Link>
    </div>
  );
}
