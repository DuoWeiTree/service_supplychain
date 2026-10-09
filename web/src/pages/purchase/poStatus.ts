import { useEffect, useRef } from 'react';
import type { ApiError } from '../../api/client';
import type { AttentionKind, PoAttempt, PoTimeline, PurchaseOrder } from '../../api/types';

export const KIND_LABEL: Record<AttentionKind, string> = {
  needs_verify: '下单结果待核实',
  landed_mismatch: '领星明细不一致',
  stuck_call: '下单请求卡住',
};

// 设计取值 · 未实测：结果未知后查调用记录的间隔
export const POLL_MS = 3000;

/** 写动作没拿到明确答复：请求可能已到后端并在执行（建单 / 查单）。
 *  ★ 只有后端给出的明确错误码才算「被拒」；超时 · 连不上 · 5xx · 回的不是 JSON 都是「不知道」。
 *  例外：后端自己发的 502 lookup_failed 是明确结论（查询本身失败，什么也没改）。 */
export function isUnknownOutcome(err: ApiError): boolean {
  if (err.status === 0 || err.error === 'bad_response') return true;
  return err.status >= 500 && err.error !== 'lookup_failed';
}

export const isCreate = (a: PoAttempt) => a.endpoint === 'create_purchase_order';

/** 任何年龄的在途下单调用（不只 V5 之后的 stuck_call）：在途时后端拒改 / 删 / 下单（po_placing） */
export const inFlightCreate = (t: PoTimeline): PoAttempt | null =>
  t.attempts.find((a) => isCreate(a) && a.outcome === 'in_flight') ?? null;

export const lastCreateId = (t: PoTimeline): number =>
  Math.max(0, ...t.attempts.filter(isCreate).map((a) => a.call_id));

/** ★ 判可编辑要三样都在手：PO、待处理队列、调用记录。任何一样取不到就不判（失败即关闭） */
export const isEditable = (p: PurchaseOrder, stuck: number, t: PoTimeline) =>
  p.state === '草稿' && !p.discarded_at && p.attention.length === 0 && stuck === 0 && inFlightCreate(t) === null;

/** 每 POLL_MS 跑一次 `tick`，直到它返回 true 或组件卸载。tick 抛错只记日志、照常下一轮 */
export function usePoll(active: boolean, tick: () => Promise<boolean>, tag: string): void {
  const ref = useRef(tick);
  ref.current = tick;
  useEffect(() => {
    if (!active) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const loop = () => {
      timer = setTimeout(() => {
        ref.current().then(
          (done) => { if (alive && !done) loop(); },
          (e: unknown) => {
            console.warn(`[${tag}] poll failed`, JSON.stringify({ err: String(e) }));
            if (alive) loop();
          },
        );
      }, POLL_MS);
    };
    loop();
    return () => { alive = false; if (timer !== undefined) clearTimeout(timer); };
  }, [active, tag]);
}
