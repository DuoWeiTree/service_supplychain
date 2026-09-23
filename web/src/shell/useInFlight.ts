import { useCallback, useRef, useState } from 'react';

/** 在飞护栏。`run(key, fn)`：同一个 key 还在飞就**同步早退**，落地后（成功或失败）复位。
 *  `pending.has(key)` 给 `disabled` 用。
 *
 *  ★ 为什么收成一处：同一形状此前在六个写动作上各写了一遍（Set 型 4 份、boolean 型 4 份），
 *    而 `removeSku` / `removeMsku` / `create` 三处漏了 —— 复制粘贴的护栏总会漏，
 *    漏的那次长得跟真失败一模一样（双击移出货号会报「移出中断」，其实第一轮全成功了）。
 *
 *  ★ 为什么判据读 ref 不读 state：同一个事件循环里连打两次，第二次拿到的 `pending`
 *    还是点击前那一份快照（React 的更新是异步落地的），只靠 state 判会两次都放行。
 *    state 只负责渲染 disabled，ref 才是那道门。
 *
 *  ★ 为什么不吞异常：`fn` 抛了照样往外抛（`finally` 只管复位）——
 *    吞掉就成了静默兜底，调用方再也分不清「没发生」与「发生了但失败了」。 */
export function useInFlight() {
  const live = useRef<Set<string>>(new Set());
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set<string>());

  const run = useCallback(async (key: string, fn: () => Promise<void>): Promise<void> => {
    if (live.current.has(key)) return;
    live.current.add(key);
    setPending(new Set(live.current));
    try {
      await fn();
    } finally {
      live.current.delete(key);
      setPending(new Set(live.current));
    }
  }, []);

  return { pending, run };
}
