import type { ApiError } from '../../api/client';

/** 照后端给的本地时间显示到秒，不换时区：时间线要能和服务端日志逐行对上 */
export const fmtAt = (iso: string | null | undefined): string =>
  iso ? iso.slice(0, 19).replace('T', ' ') : '—';

/** Σ 数量 × 单价，按十进制定点精确求和。★ 不经浮点（0.3333 × 12 在浮点里不是 3.9996），
 *  也不四舍五入 —— 金额的取舍位数没有裁定，照实给全。单价认不出时返回 null（未知，不当 0）。 */
export function exactTotal(lines: { units: number; price: string }[]): string | null {
  const parsed = lines.map((l) => /^(\d+)(?:\.(\d+))?$/.exec(l.price.trim()));
  if (parsed.some((m) => m === null)) return null;
  const scale = Math.max(0, ...parsed.map((m) => m![2]?.length ?? 0));
  let sum = 0n;
  parsed.forEach((m, i) => {
    sum += BigInt(m![1]! + (m![2] ?? '').padEnd(scale, '0')) * BigInt(lines[i]!.units);
  });
  if (scale === 0) return sum.toString();
  const s = sum.toString().padStart(scale + 1, '0');
  const frac = s.slice(-scale).replace(/0+$/, '');
  return frac === '' ? s.slice(0, -scale) : `${s.slice(0, -scale)}.${frac}`;
}

/** 写动作的三问日志：打的谁（call）· 多久（ms）· 怎么失败的（status / error / cause_code） */
export function logWrite(tag: string, call: string, t0: number, extra: Record<string, unknown>): void {
  console.info(`[${tag}]`, JSON.stringify({ call, ms: Math.round(performance.now() - t0), ...extra }));
}
export function logWriteFail(tag: string, call: string, t0: number, err: ApiError): void {
  console.warn(`[${tag}]`, JSON.stringify({ call, ms: Math.round(performance.now() - t0), status: err.status,
    error: err.error, fields: err.fields }));
}
