import { ACTORS } from './actors';

let current: string = ACTORS[0]!.actor_id;
const listeners = new Set<() => void>();

export function getActor(): string { return current; }

export function setActor(id: string): void {
  if (!ACTORS.some((a) => a.actor_id === id)) {
    // ★ 静默兜底是最坏的一种：认不出的 actor 要报错，不是悄悄落回默认值
    throw new Error(`unknown actor: ${id}`);
  }
  current = id;
  listeners.forEach((fn) => fn());
}

export function subscribeActor(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
