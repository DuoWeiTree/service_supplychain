import type { ReactNode } from 'react';
import { useSyncExternalStore } from 'react';
import { Link } from 'react-router-dom';
import { ACTORS, actorLabel } from './actors';
import { getActor, setActor, subscribeActor } from './actorStore';
import { Toasts } from './Toasts';

export function AppShell({ crumb, children }: { crumb: string; children: ReactNode }) {
  const actor = useSyncExternalStore(subscribeActor, getActor, getActor);
  return (
    <>
      <div className="shell">
        <div className="shell__bar">
          <Link className="shell__brand" to="/">销售计划</Link>
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
      <div className="wrap mt">{children}</div>
      <Toasts />
    </>
  );
}
