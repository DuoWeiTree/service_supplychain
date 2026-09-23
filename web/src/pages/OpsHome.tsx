import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { useActionError } from '../shell/useActionError';
import { useInFlight } from '../shell/useInFlight';
import { ErrorDetail } from '../components/ErrorDetail';
import { Qty } from '../components/Qty';
import { api, ApiError } from '../api';
import type { DashboardPlans, PlanList, PlanSummary, UnsubmittedBoard } from '../api/types';

/** ★ 阶段 A 只渲染够得着的三态（00e:45）。挡的地方只有这一处 —— 接口照样返回全集（S-20） */
export const STAGE_A_VISIBLE = ['未提交', '已提交', '已撤销'] as const;

const CREATE_KEY = 'create';

type SortKey = 'period' | 'title' | 'state';

export function OpsHome() {
  const navigate = useNavigate();
  const [list, setList] = useState<PlanList | null>(null);
  const [board, setBoard] = useState<UnsubmittedBoard | null>(null);
  const [dash, setDash] = useState<DashboardPlans | null>(null);
  // ★ I6 裁定：三个端点各记各的错。合成一个 err 的写法会在 dashboardPlans 挂掉时
  //   把已经成功返回的计划列表与两栏待办一起换成一个错误块
  const [listErr, setListErr] = useState<ApiError | null>(null);
  const [boardErr, setBoardErr] = useState<ApiError | null>(null);
  const [dashErr, setDashErr] = useState<ApiError | null>(null);
  const { err: createErr, fail, succeed, dismiss } = useActionError();
  const { pending, run } = useInFlight();

  const [fPeriod, setFPeriod] = useState('');
  const [fTitle, setFTitle] = useState('');
  const [fRev, setFRev] = useState('');
  const [fState, setFState] = useState('');
  const [sort, setSort] = useState<SortKey>('period');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    // ★ 三个接口一起拉，但逐个收：计数与列表来自不同端点，一个挂了另外两个照样出数
    void Promise.allSettled([api.listPlans({ archived: false }), api.dashboardUnsubmitted(), api.dashboardPlans()])
      .then(([l, b, d]) => {
        if (l.status === 'fulfilled') setList(l.value); else setListErr(l.reason as ApiError);
        if (b.status === 'fulfilled') setBoard(b.value); else setBoardErr(b.reason as ApiError);
        if (d.status === 'fulfilled') setDash(d.value); else setDashErr(d.reason as ApiError);
      });
  }, []);

  const finished = useMemo(() => (list?.plans ?? []).filter((p) => p.state === '已完结'), [list]);
  const visible = useMemo(() => {
    const kept = (list?.plans ?? []).filter((p) => p.state !== '已完结');
    const rows = kept.filter((p) =>
      (fPeriod === '' || p.period_start.startsWith(fPeriod)) &&
      (fTitle === '' || p.title.includes(fTitle)) &&
      (fRev === '' || String(p.state_rev ?? '') === fRev) &&
      (fState === '' || stateLabel(p) === fState));
    return [...rows].sort((a, b) =>
      sort === 'period' ? b.period_start.localeCompare(a.period_start)
      : sort === 'title' ? a.title.localeCompare(b.title)
      : stateLabel(a).localeCompare(stateLabel(b)));
  }, [list, fPeriod, fTitle, fRev, fState, sort]);

  // ★ 三个数来自三个端点。取不到的那个给「未知」，不落回 0 ——
  //   「一张都没有」与「这个数取不到」长得一模一样才是最坏的
  const counts: Record<(typeof STAGE_A_VISIBLE)[number], number | null> = {
    未提交: board === null ? null : board.never_submitted.length,
    已提交: dash === null ? null : dash.counts['已提交'],
    已撤销: list === null ? null : list.plans.filter((p) => p.state === '已撤销').length,
  };

  async function create(form: HTMLFormElement) {
    const data = new FormData(form);
    // ★ I3：双击建出两张同名计划，而阶段 A 没有删计划的界面入口（S-27）—— 只能去归档
    await run(CREATE_KEY, async () => {
      try {
        const { plan_id } = await api.createPlan({
          title: String(data.get('title')),
          period_start: `${String(data.get('period_start'))}-01`,  // ★ 建计划的入参是月初日期
          months: Number(data.get('months')),
        });
        succeed(CREATE_KEY);
        navigate(`/plans/${plan_id}`);
      } catch (e) {
        fail(CREATE_KEY, e as ApiError);
        pushToast({ kind: 'fail', text: (e as ApiError).hint });
      }
    });
  }

  return (
    <AppShell crumb="我的计划">
      <div className="head">
        <div className="head__main"><h1>我的计划</h1></div>
        <div className="head__act">
          <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>新建销售计划</button>
        </div>
      </div>

      <div className="sec counts" data-testid="counts">
        {STAGE_A_VISIBLE.map((k) => (
          <div className="kpi" key={k}>
            <span className="kpi__n">
              {counts[k] === null ? <Qty v={{ kind: 'unknown' }} /> : counts[k]}
            </span>
            <span className="kpi__d">{k}</span>
          </div>
        ))}
      </div>
      {dashErr && <ErrorDetail err={dashErr} />}
      {/* ★ 接口自报哪些态阶段 A 够不着；测试拿它当靶子，前端不硬编码这份名单 */}
      <span data-testid="unreachable" hidden>
        {JSON.stringify(dash?.scope_note.unreachable_in_stage_a ?? [])}
      </span>

      {creating && (
        <form className="sec bar" onSubmit={(e) => { e.preventDefault(); void create(e.currentTarget); }}>
          <label className="field"><span className="lbl">标题</span>
            <input className="inp inp--text" name="title" required /></label>
          <label className="field"><span className="lbl">起始月</span>
            <input className="inp" name="period_start" type="month" defaultValue="2026-10" required /></label>
          <label className="field"><span className="lbl">跨 N 月</span>
            <input className="inp inp--tiny" name="months" type="number" min={1} max={24} defaultValue={3} required /></label>
          <button type="submit" className="btn btn--primary" disabled={pending.has(CREATE_KEY)}>新建</button>
          <button type="button" className="btn btn--ghost" onClick={() => setCreating(false)}>取消</button>
        </form>
      )}
      {createErr && <ErrorDetail err={createErr} onDismiss={dismiss} />}

      <div className="sec twocol">
        <div className="panel">
          <div className="panel__head">未提交</div>
          <div className="panel__body" data-testid="never-submitted">
            {(board?.never_submitted ?? []).map((p) => (
              <div key={p.plan_id}><Link to={`/plans/${p.plan_id}`}>{p.title}</Link></div>
            ))}
            {board?.never_submitted.length === 0 && <div className="todos__empty">无</div>}
          </div>
        </div>
        <div className="panel">
          <div className="panel__head">提交后又改过</div>
          <div className="panel__body" data-testid="changed-since-submit">
            {(board?.changed_since_submit ?? []).map((p) => (
              <div key={p.plan_id}>
                <Link to={`/plans/${p.plan_id}`}>{p.title}</Link>{' '}
                <span className="muted">rev {p.since_rev}</span>
              </div>
            ))}
            {board?.changed_since_submit.length === 0 && <div className="todos__empty">无</div>}
          </div>
        </div>
      </div>
      {boardErr && <ErrorDetail err={boardErr} />}

      <div className="bar">
        <label className="bar__grp"><span className="bar__lbl">周期</span>
          <input className="inp inp--tiny" value={fPeriod} onChange={(e) => setFPeriod(e.target.value)} /></label>
        <label className="bar__grp"><span className="bar__lbl">计划</span>
          <input className="inp" value={fTitle} onChange={(e) => setFTitle(e.target.value)} /></label>
        <label className="bar__grp"><span className="bar__lbl">版本</span>
          <input className="inp inp--tiny" value={fRev} onChange={(e) => setFRev(e.target.value)} /></label>
        <label className="bar__grp"><span className="bar__lbl">状态</span>
          <select className="inp" value={fState} onChange={(e) => setFState(e.target.value)}>
            <option value="">全部</option>
            {STAGE_A_VISIBLE.map((k) => <option key={k} value={k}>{k}</option>)}
          </select></label>
        <label className="bar__grp"><span className="bar__lbl">排序</span>
          <select className="inp" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            <option value="period">周期</option><option value="title">计划名</option><option value="state">状态</option>
          </select></label>
      </div>

      {listErr ? <ErrorDetail err={listErr} /> : (
        <>
          <div className="table-scroll">
            <table className="table table--dense" data-testid="plan-list">
              <thead><tr><th>周期</th><th>计划名</th><th className="r">版本</th><th>状态</th><th>详情</th></tr></thead>
              <tbody>
                {visible.map((p) => (
                  <tr key={p.plan_id}>
                    {/* ★ I7 裁定：不用 `A · B` 拼元串 —— 表头已经说了这一列是周期 */}
                    <td>{p.period_start.slice(0, 7)} <span className="muted">跨 {p.months} 月</span></td>
                    <td>{p.title}</td>
                    <td className="r">{p.state_rev === null ? <Qty v={{ kind: 'unknown' }} /> : p.state_rev}</td>
                    <td><span className="chip chip--dim">{stateLabel(p)}</span></td>
                    <td><Link to={`/plans/${p.plan_id}`}>打开</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* ★ 挡掉的两侧都要有个数 —— 「没有」和「被我藏了」长得一样 */}
          <div className="muted mt" data-testid="hidden-rows">
            <span>已完结 {finished.length} 张不在列表</span>{' '}
            <span>已归档 {list?.excluded.archived ?? 0} 张不在列表</span>
          </div>
        </>
      )}
    </AppShell>
  );
}

/** ★ 从未提交的计划 state 为 null（没有 rev 就没有记录）—— 不是 04 里的任何一个状态值 */
function stateLabel(p: PlanSummary): string {
  return p.state === null ? '未提交' : p.state;
}
