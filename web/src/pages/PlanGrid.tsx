import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { useActionError } from '../shell/useActionError';
import { useInFlight } from '../shell/useInFlight';
import { ErrorDetail } from '../components/ErrorDetail';
import { api, ApiError } from '../api';
import type { GridResponse, LeadTime, NoHistoryEntry, PlanSummary, Seller, SubmitResult } from '../api/types';
import { buildGridModel } from './planGridModel';
import { usePlanGridSaves } from './usePlanGridSaves';
import { LeadTimeBar } from './LeadTimeBar';
import { PlanSheet } from './PlanSheet';
import { SubmitPanel, type InFlightBlock } from './SubmitPanel';

const SUBMIT_KEY = 'submit';
const RECOMPUTE_KEY = 'recompute';

export function PlanGrid() {
  const planId = Number(useParams().planId);
  const [plan, setPlan] = useState<PlanSummary | null>(null);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [leadTime, setLeadTime] = useState<LeadTime | null>(null);
  // ★ I6 裁定：读接口各记各的错 —— 一个挂了不许把整屏换成一个错误块。
  //   合成一个 err 的写法会让「计划抬头取不到」把已经拿回来的网格一起吞掉。
  //   提前期（Task 20 复核 R1）与另外三个同一条纪律：它是独立的第四个读，不挂在
  //   model（grid && sellers）身上 —— 网格挂了不该连累提前期条也一起消失。
  const [planErr, setPlanErr] = useState<ApiError | null>(null);
  const [gridErr, setGridErr] = useState<ApiError | null>(null);
  const [sellersErr, setSellersErr] = useState<ApiError | null>(null);
  const [leadTimeErr, setLeadTimeErr] = useState<ApiError | null>(null);
  // ★ T4 R2 裁定：写动作的错误不跟着 load() 走 —— 见 useActionError 的注释
  const { err: actionErr, fail, succeed, dismiss } = useActionError();
  const [report, setReport] = useState<SubmitResult | null>(null);
  const [inFlight, setInFlight] = useState<InFlightBlock | null>(null);
  // task-23：重算预测点名了哪些 msku 算不出速率——留到下次重算/刷新前，不是闪一下 toast 就没了
  // （与 PlanAdd.tsx 的 noHistory 同一个理由）
  const [noRate, setNoRate] = useState<NoHistoryEntry[]>([]);
  // ★ Ruling C + I3：提交的在飞护栏与其余五处写动作共用 useInFlight
  const { pending: submitting, run } = useInFlight();

  const load = async () => {
    const [p, g, s, lt] = await Promise.allSettled([
      api.getPlan(planId), api.getGrid(planId), api.listSellers(), api.getLeadTime(),
    ]);
    if (p.status === 'fulfilled') { setPlan(p.value); setPlanErr(null); } else setPlanErr(p.reason as ApiError);
    if (g.status === 'fulfilled') { setGrid(g.value); setGridErr(null); } else setGridErr(g.reason as ApiError);
    if (s.status === 'fulfilled') { setSellers(s.value); setSellersErr(null); } else setSellersErr(s.reason as ApiError);
    if (lt.status === 'fulfilled') { setLeadTime(lt.value); setLeadTimeErr(null); } else setLeadTimeErr(lt.reason as ApiError);
  };

  useEffect(() => { void load(); }, [planId]);

  const { pending, inputErr, saveDemand, savePurchase, removeSku, removeStore, removeMsku } =
    usePlanGridSaves(planId, load, fail, succeed);

  const model = useMemo(
    () => (grid && sellers ? buildGridModel(grid, sellers) : null),
    [grid, sellers],
  );

  async function submit() {
    await run(SUBMIT_KEY, async () => {
      setReport(null);
      setInFlight(null);
      try {
        const r = await api.submit(planId);
        setReport(r);
        succeed(SUBMIT_KEY);
        // ★ 不自动跳走：skipped[] 只在这一次响应里存在，成功后留在原地由人点「去版本」
        pushToast({
          kind: r.skipped.length === 0 ? 'ok' : 'warn',
          text: `已提交 rev ${r.rev}，铸出 ${r.lines} 条，跳过 ${r.skipped.length} 条`,
        });
        await load();
      } catch (e) {
        const ae = e as ApiError;
        // ★ 409 是「你没写错，但现在不行」—— 点名旧版号，下一步是去看那一版
        if (ae.status === 409 && ae.error === 'rev_in_flight') {
          setInFlight({ rev: Number(ae.fields['in_flight_rev']), err: ae });
          return;
        }
        fail(SUBMIT_KEY, ae);
      }
    });
  }

  async function recompute() {
    // ★ 入口早退：与其余六处写动作同一护栏（useInFlight），防双击并发打两次重算
    await run(RECOMPUTE_KEY, async () => {
      try {
        const r = await api.recomputeForecast(planId);
        setNoRate(r.no_rate);
        succeed(RECOMPUTE_KEY);
        // ★ task-23 裁定：成功提示要点名「N 格更新 · M 格算不出速率」，不是「重算完成」——
        //   「算不出速率」与「算出来了」是两件事，合成一句「重算完成」会把它们混成一个成功计数
        pushToast({
          kind: r.no_rate.length === 0 ? 'ok' : 'warn',
          text: `${r.recomputed.length} 格更新 · ${r.no_rate.length} 格算不出速率`,
        });
        await load();
      } catch (e) {
        fail(RECOMPUTE_KEY, e as ApiError);
      }
    });
  }

  return (
    <AppShell crumb="计划编辑">
      {plan && (
        <div className="head">
          <div className="head__main">
            <h1>{plan.title}</h1>
            {/* ★ F16 裁定：三段各自成句，不用 `·` 拼成一行元串 */}
            <div className="head__meta" data-testid="head-meta">
              <span>起始月 <b>{plan.period_start.slice(0, 7)}</b></span>
              <span>跨 <b>{plan.months}</b> 月</span>
              <span>负责人 <b>{plan.owner_actor}</b></span>
            </div>
          </div>
          <div className="head__act">
            {/* ★ F17 裁定：本页内部导航改用 Router 的 Link，避免整页刷新 */}
            <Link className="btn" to={`/plans/${planId}/add`}>添加货品</Link>
            <button type="button" className="btn btn--ghost" onClick={() => void load()}>重置</button>
            {/* task-23：换源之后旧认领的格子不会自己回头算，需要一个手动入口重新种一遍
                system_units/system_basis——disabled 是双击护栏（同 Ruling C） */}
            <button
              type="button" className="btn btn--ghost"
              disabled={submitting.has(RECOMPUTE_KEY)} onClick={() => void recompute()}
            >
              重算预测
            </button>
            {/* ★「提交」排在「重置」之后，与原理图一致；disabled 是双击护栏（Ruling C） */}
            <button
              type="button" className="btn btn--primary"
              disabled={submitting.has(SUBMIT_KEY)} onClick={() => void submit()}
            >
              提交
            </button>
            <Link className="btn" to={`/plans/${planId}/revs`}>版本</Link>
          </div>
        </div>
      )}
      {/* ★ I1 裁定：归档是「有、但封存了」，不是「没有这张计划」—— 屏上要说得出这三个字。
          写动作照发，后端的 409 plan_archived 会原样落到页尾的 ErrorDetail 上：
          置灰按钮会把「归档了」与「这一步现在不能做」混成一个说法 */}
      {plan?.archived_at != null && (
        <div className="note" data-testid="archived-notice">
          这张计划已归档（{plan.archived_at.slice(0, 10)}），不接受写入
        </div>
      )}
      {planErr && <ErrorDetail err={planErr} />}
      {!plan && !planErr && <div className="empty"><p className="empty__title">正在取计划抬头…</p></div>}

      <SubmitPanel planId={planId} report={report} inFlight={inFlight} />

      {gridErr && <ErrorDetail err={gridErr} />}
      {sellersErr && <ErrorDetail err={sellersErr} />}
      {!model && !gridErr && !sellersErr && (
        <div className="empty"><p className="empty__title">正在取网格…</p></div>
      )}

      {/* task-23：算不出速率的 msku 点名——复用依据面板之外的展开位（同 model.orphans
          的 .sec/.dropline 样式），不是把「重算完成」当成唯一反馈 */}
      {noRate.length > 0 && (
        <div className="sec" data-testid="recompute-no-rate">
          {noRate.map((n) => (
            <div className="dropline" key={`${n.seller_sku}-${n.sid}`}>
              <span className="k">{n.seller_sku}</span><span>{n.sid}</span><span>{n.reason}</span>
            </div>
          ))}
        </div>
      )}

      {model && model.orphans.length > 0 && (
        <div className="sec" data-testid="orphans">
          {model.orphans.map((o) => (
            <div className="dropline" key={`${o.kind}-${o.key}`}>
              <span className="k">{o.kind}</span><span>{o.key}</span>
            </div>
          ))}
        </div>
      )}

      {leadTimeErr && <ErrorDetail err={leadTimeErr} />}
      {leadTime && <LeadTimeBar leadTime={leadTime} onChanged={load} fail={fail} succeed={succeed} />}

      {model && (
        <PlanSheet
          model={model} pending={pending} inputErr={inputErr}
          onDemand={(ss, sid, p, raw) => void saveDemand(ss, sid, p, raw)}
          onPurchase={(sku, p, raw) => void savePurchase(sku, p, raw)}
          onRemoveSku={(sku) => void removeSku(sku)}
          onRemoveStore={(b) => void removeStore(b)}
          onRemoveMsku={(ss, sid, sku) => void removeMsku(ss, sid, sku)}
        />
      )}

      {actionErr && <ErrorDetail err={actionErr} onDismiss={dismiss} />}
    </AppShell>
  );
}

