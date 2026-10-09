import type { ReactNode } from 'react';
import { useSyncExternalStore } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ACTORS, actorLabel } from './actors';
import { getActor, setActor, subscribeActor } from './actorStore';
import { Toasts } from './Toasts';
import { AttentionBanner } from './AttentionBanner';

export function AppShell({ crumb, children }: { crumb: string; children: ReactNode }) {
  const actor = useSyncExternalStore(subscribeActor, getActor, getActor);
  const inPurchase = useLocation().pathname.startsWith('/purchase');
  return (
    <>
      <div className="shell">
        <div className="shell__bar">
          <Link className="shell__brand" to="/" aria-current={inPurchase ? undefined : 'page'}>销售计划</Link>
          <Link className="shell__nav" to="/purchase" aria-current={inPurchase ? 'page' : undefined}>采购</Link>
          <span className="sep" />
          <span className="shell__crumb">{crumb}</span>
          <span className="shell__spacer" />
          <label className="lbl" htmlFor="actor">操作人</label>
          <select
            id="actor"
            className="inp inp--tiny"
            style={{ width: 'auto' }}
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          >
            {ACTORS.map((a) => <option key={a.actor_id} value={a.actor_id}>{actorLabel(a)}</option>)}
          </select>
          <span className="chip chip--dim">留痕可伪造</span>
        </div>
      </div>
      <AttentionBanner />
      <div className="wrap mt">{children}</div>
      <Toasts />
    </>
  );
}
