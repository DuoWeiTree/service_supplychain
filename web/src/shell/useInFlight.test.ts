import { describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useInFlight } from './useInFlight';

/** 手动控制的 promise —— 用它把「在飞」这段时间撑开，才能断言 pending 的中间态 */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe('useInFlight', () => {
  it('★ 同一个 key 在飞时第二次调用直接早退，不再跑 fn', async () => {
    const { result } = renderHook(() => useInFlight());
    const d = deferred<void>();
    let runs = 0;

    let first!: Promise<void>;
    act(() => { first = result.current.run('k', async () => { runs += 1; await d.promise; }); });
    expect(runs).toBe(1);
    expect(result.current.pending.has('k')).toBe(true);

    // ★ 早退必须是同步的：这一次连 fn 都不许进
    await act(async () => { await result.current.run('k', async () => { runs += 1; }); });
    expect(runs).toBe(1);

    await act(async () => { d.resolve(); await first; });
    expect(result.current.pending.has('k')).toBe(false);
  });

  it('★ 不同 key 互不相干 —— 一格在飞不许把另一格也锁上', async () => {
    const { result } = renderHook(() => useInFlight());
    const a = deferred<void>();
    const b = deferred<void>();
    let ranB = false;

    let pa!: Promise<void>;
    let pb!: Promise<void>;
    act(() => { pa = result.current.run('a', async () => { await a.promise; }); });
    act(() => { pb = result.current.run('b', async () => { ranB = true; await b.promise; }); });
    expect(ranB).toBe(true);
    expect(result.current.pending.has('a')).toBe(true);
    expect(result.current.pending.has('b')).toBe(true);

    // a 先落地，b 还在飞 —— 复位是逐 key 的，不是一次清空整张表
    await act(async () => { a.resolve(); await pa; });
    expect(result.current.pending.has('a')).toBe(false);
    expect(result.current.pending.has('b')).toBe(true);

    await act(async () => { b.resolve(); await pb; });
    expect(result.current.pending.has('b')).toBe(false);
  });

  it('★ fn 抛了也要复位，并把错误原样抛出去 —— 吞掉就成了静默兜底', async () => {
    const { result } = renderHook(() => useInFlight());
    const boom = new Error('炸了');

    let caught: unknown = null;
    await act(async () => {
      await result.current.run('k', async () => { throw boom; }).catch((e: unknown) => { caught = e; });
    });
    expect(caught).toBe(boom);
    expect(result.current.pending.has('k')).toBe(false);

    // ★ 复位后同一个 key 还能再来一次：只复位 UI 不复位判据，第二次会被永久挡住
    let again = false;
    await act(async () => { await result.current.run('k', async () => { again = true; }); });
    expect(again).toBe(true);
  });
});
