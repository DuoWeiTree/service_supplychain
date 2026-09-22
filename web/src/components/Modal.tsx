import { useEffect, useId, useRef, type ReactNode } from 'react';

/** ★ 只留一套弹窗结构：.modal-backdrop > .modal（shell.css 里 dialog.modal 与 div 版并存，
 *  这里固定用 div 版，不用原生 <dialog>） */
export function Modal({
  title, danger = false, onClose, children,
}: { title: string; danger?: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const headingId = useId();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // ★ 键盘用户能到达一切：打开即把焦点放进对话框本体，Tab 从这里开始走
  useEffect(() => { ref.current?.focus(); }, []);

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        ref={ref}
        className="modal"
        role="dialog"
        aria-labelledby={headingId}
        aria-modal="true"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal__head" id={headingId}>{title}</div>
        {danger && <div className="modal__warn irreversible">⛔ 这一步会撤销已提交的记录</div>}
        <div className="modal__body">{children}</div>
      </div>
    </div>
  );
}
