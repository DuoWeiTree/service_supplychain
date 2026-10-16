import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { useActionError } from '../shell/useActionError';
import { useInFlight } from '../shell/useInFlight';
import { ErrorDetail } from '../components/ErrorDetail';
import { Qty } from '../components/Qty';
import { api, ApiError } from '../api';
import type { DashboardPlans, PlanList, UnsubmittedBoard } from '../api/types';
import { byTodo, kpiKeys, NEVER, nextStep, stateLabel, stateOptions } from './opsHomeModel';

const CREATE_KEY = 'create';
/** 状态筛选里的「要动手的」（下一步 chip 有的那些） */
const TODO = 'todo';
const CHANGED = 'changed';

type SortKey = 'todo' | 'period' | 'title' | 'state';

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
  const [sort, setSort] = useState<SortKey>('todo');
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
    // ★ 已完结默认不在列表（底行计数），专门筛它时才列出来 —— 选了却一张都筛不出就是被藏了
    const kept = (list?.plans ?? []).filter((p) => fState === '已完结' || p.state !== '已完结');
    const rows = kept.filter((p) =>
      (fPeriod === '' || p.period_start.startsWith(fPeriod)) &&
      (fTitle === '' || p.title.includes(fTitle)) &&
      (fRev === '' || String(p.state_rev ?? '') === fRev) &&
      (fState === '' || (fState === TODO ? board !== null && nextStep(p, board) !== undefined
        : fState === CHANGED ? board?.changed_since_submit.some((c) => c.plan_id === p.plan_id) === true
        : stateLabel(p) === fState)));
    return [...rows].sort((a, b) =>
      sort === 'todo' ? byTodo(a, b, board)
      : sort === 'period' ? b.period_start.localeCompare(a.period_start)
      : sort === 'title' ? a.title.localeCompare(b.title)
      : stateLabel(a).localeCompare(stateLabel(b)));
  }, [list, board, fPeriod, fTitle, fRev, fState, sort]);

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
        {/* UX-D11：要动手的两个计数在前，点了即筛选 */}
        <button type="button" className="kpi kpi--btn" aria-pressed={fState === CHANGED}
          onClick={() => setFState(fState === CHANGED ? '' : CHANGED)}>
          <span className="kpi__n">{board === null ? <Qty v={{ kind: 'unknown' }} /> : board.changed_since_submit.length}</span>
          <span className="kpi__d">提交后又改过</span>
        </button>
        <button type="button" className="kpi kpi--btn" aria-pressed={fState === NEVER}
          onClick={() => setFState(fState === NEVER ? '' : NEVER)}>
          <span className="kpi__n">{board === null ? <Qty v={{ kind: 'unknown' }} /> : board.never_submitted.length}</span>
          <span className="kpi__d">{NEVER}</span>
        </button>
        <span className="sep" />
        {/* ★ UX-D13 / S-20：全集来自接口，减去它自报够不着的；取不到就写一格未知，不手列一份顶上 */}
        {dash ? kpiKeys(dash).map((k) => (
          <div className="kpi" key={k}>
            <span className="kpi__n">{dash.counts[k as keyof DashboardPlans['counts']]}</span>
            <span className="kpi__d">{k}</span>
          </div>
        )) : (
          <div className="kpi">
            <span className="kpi__n"><Qty v={{ kind: 'unknown' }} /></span>
            <span className="kpi__d">各状态计数</span>
          </div>
        )}
      </div>
      {dashErr && <ErrorDetail err={dashErr} />}
      {boardErr && <ErrorDetail err={boardErr} />}
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
            <option value={TODO}>要动手的</option>
            {fState === CHANGED && <option value={CHANGED}>提交后又改过</option>}
            {stateOptions(dash, list?.plans ?? []).map((k) => <option key={k} value={k}>{k}</option>)}
          </select></label>
        <label className="bar__grp"><span className="bar__lbl">排序</span>
          <select className="inp" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            <option value="todo">要动手的在前</option>
            <option value="period">周期</option><option value="title">计划名</option><option value="state">状态</option>
          </select></label>
      </div>

      {listErr ? <ErrorDetail err={listErr} /> : (
        <>
          <div className="table-scroll">
            <table className="table table--dense" data-testid="plan-list">
              <thead><tr><th>下一步</th><th>计划名</th><th>周期</th><th className="r">版本</th><th>状态</th><th>详情</th></tr></thead>
              <tbody>
                {list === null && <tr><td colSpan={6} className="muted">正在取…</td></tr>}
                {visible.map((p) => {
                  const next = board ? nextStep(p, board) : undefined;
                  return (
                    <tr key={p.plan_id}>
                      <td data-testid="next">
                        {next && <><span className="chip chip--ours">{next.text}</span>{next.note && <span className="muted"> {next.note}</span>}</>}
                      </td>
                      <td data-testid="plan-title">{p.title}</td>
                      {/* ★ I7 裁定：不用 `A · B` 拼元串 —— 表头已经说了这一列是周期 */}
                      <td>{p.period_start.slice(0, 7)} <span className="muted">跨 {p.months} 月</span></td>
                      <td className="r">{p.state_rev === null ? <Qty v={{ kind: 'unknown' }} /> : p.state_rev}</td>
                      <td><span className="chip chip--dim">{stateLabel(p)}</span></td>
                      <td><Link to={`/plans/${p.plan_id}`}>打开</Link></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {/* ★ 挡掉的两侧都要有个数 —— 「没有」和「被我藏了」长得一样；没取到就不写数（空 ≠ 0） */}
          <div className="muted mt" data-testid="hidden-rows">
            {list === null ? <span>正在取…</span> : (
              <>
                {fState !== '已完结' && <><span>已完结 {finished.length} 张不在列表</span>{' '}</>}
                <span>已归档 {list.excluded.archived} 张不在列表</span>
              </>
            )}
          </div>
        </>
      )}
    </AppShell>
  );
}
