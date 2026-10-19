import { useContext, useEffect, type MutableRefObject } from 'react';
import { UNSAFE_DataRouterContext, useBlocker } from 'react-router-dom';
import { Modal } from '../../components/Modal';

/** 有未保存的改动时：站内跳转先问（只换查询参数不算离开），关页 / 刷新走 beforeunload。
 *  `bypass`：保存 / 删除成功后的跳转置 true —— 那一刻 dirty 的 state 还没刷新。 */
export function LeaveGuard({ when, bypass }: { when: boolean; bypass: MutableRefObject<boolean> }) {
  useEffect(() => {
    if (!when) return;
    const onUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', onUnload);
    return () => window.removeEventListener('beforeunload', onUnload);
  }, [when]);
  // useBlocker 只在 data router 里能用；挂在 <MemoryRouter> 下（部分页面测试）时站内拦截不生效，要留痕
  const dataRouter = useContext(UNSAFE_DataRouterContext) !== null;
  useEffect(() => {
    if (!dataRouter) console.warn('[po-editor] leave guard: no data router, in-app navigation not blocked');
  }, [dataRouter]);
  return dataRouter ? <Blocker when={when} bypass={bypass} /> : null;
}

function Blocker({ when, bypass }: { when: boolean; bypass: MutableRefObject<boolean> }) {
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    when && !bypass.current && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state === 'blocked') {
      console.info('[po-editor] leave blocked', JSON.stringify({ to: blocker.location.pathname + blocker.location.search }));
    }
  }, [blocker]);
  if (blocker.state !== 'blocked') return null;
  return (
    <Modal title="有未保存的改动" onClose={() => blocker.reset()}>
      <p>离开后这次没保存的改动会丢掉。</p>
      <div className="btn-row">
        <button type="button" className="btn btn--ghost" onClick={() => blocker.reset()}>留下</button>
        <button type="button" className="btn btn--danger" onClick={() => {
          console.info('[po-editor] leave confirmed, unsaved changes dropped');
          blocker.proceed();
        }}>离开，丢掉改动</button>
      </div>
    </Modal>
  );
}
