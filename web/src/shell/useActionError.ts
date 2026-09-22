import { useCallback, useState } from 'react';
import type { ApiError } from '../api/client';

/** 写动作的错误态，按**动作**记账。
 *
 *  ★ 为什么带 key：`load()` 成功时顺手 `setErr(null)` 会把用户还没看见的那条错误抹掉 ——
 *    删货号失败后随便改一格，保存成功触发重载，那条「释放失败」就无声无息地没了。
 *    所以一条错误只由两件事收掉：**同一个动作**下次成功，或人明确关掉它。
 *    读接口自己的失败另有去处（逐段的 loadErr），不走这里。 */
export function useActionError() {
  const [entry, setEntry] = useState<{ key: string; err: ApiError } | null>(null);

  const fail = useCallback((key: string, err: ApiError) => { setEntry({ key, err }); }, []);
  const succeed = useCallback((key: string) => {
    setEntry((cur) => (cur !== null && cur.key === key ? null : cur));
  }, []);
  const dismiss = useCallback(() => { setEntry(null); }, []);

  return { err: entry?.err ?? null, fail, succeed, dismiss };
}
