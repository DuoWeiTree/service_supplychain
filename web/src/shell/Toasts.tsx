import { useSyncExternalStore } from 'react';
import { dismissToast, getToasts, subscribeToasts } from './toastStore';

export function Toasts() {
  const items = useSyncExternalStore(subscribeToasts, getToasts, getToasts);
  if (items.length === 0) return null;
  return (
    <div className="toasts" role="status">
      {items.map((t) => (
        <button key={t.id} type="button" className={`toast toast--${t.kind}`} onClick={() => dismissToast(t.id)}>
          {t.text}
        </button>
      ))}
    </div>
  );
}
