export interface Toast { id: number; kind: 'ok' | 'fail' | 'warn'; text: string }

let seq = 0;
let items: Toast[] = [];
const listeners = new Set<() => void>();

export function getToasts(): Toast[] { return items; }
export function pushToast(t: Omit<Toast, 'id'>): void {
  items = [...items, { ...t, id: ++seq }];
  listeners.forEach((fn) => fn());
}
export function dismissToast(id: number): void {
  items = items.filter((t) => t.id !== id);
  listeners.forEach((fn) => fn());
}
export function subscribeToasts(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
