import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AppShell } from '../../shell/AppShell';
import { ErrorDetail } from '../../components/ErrorDetail';
import { Fold } from '../../components/Fold';
import { Modal } from '../../components/Modal';
import { api, ApiError } from '../../api';
import { asApiError } from '../../api/client';
import { pushToast } from '../../shell/toastStore';
import type {
  CellPut, ConsignmentView, SheetAutofill, DemandRow, DispatchAttention, DispatchCell, DispatchGate, DispatchRoute, DispatchStore, LogisticsChannel,
  OwsBlocker, OwsResult, OwsSubmission, PackingBox, PackingRow, SheetDetail, SheetGrid, SheetPreflight, SheetReceiptResult, SkuMatchStatus,
  UnplannedSku,
} from '../../api/types';
import {
  AUTOFILL_REASON, ROUTE_LABEL, addOptions, autofillMarks, autofillSummary, blockingGates, destKey, destLabel, destOptions, filterRecords, fillFor, fmtLocal, missingPacking, parseUnits,
  planTotals, stockFor, whName, type RecordFilter,
} from './dispatchGridModel';
import { GATE_LABEL, TriGateList } from './TriGateList';

type Flash = { tone: 'ok' | 'warn' | 'danger' | 'info'; text: string; err?: ApiError };
type View = 'arrange' | 'cons' | 'gates';
/** 这一次现查领星的结果；领星进度本身永远取库里的观察（detail.ext_refs），这里只管「这次读到没有」 */
type Receipt = { kind: 'idle' } | { kind: 'loading' } | { kind: 'ok'; r: SheetReceiptResult } | { kind: 'fail'; err: ApiError };
type StoreLike = Pick<DispatchStore, 'sid' | 'seller_name' | 'market' | 'has_fba' | 'mskus'> & { expected?: number | null };

const n = (v: number) => v.toLocaleString('en-US');

/** 排货工作台（设计 artifact「DispatchSheet」）：左边记录清单答「还差多少」，右边答「货在哪」并直接填格子，
 *  底部常驻排货单与闸答「排成了什么、还缺什么」。★ 所有量都取服务端返回，前端不重算判据。 */
export function SheetPage() {
  const sheetId = Number(useParams().sheetId);
  const [detail, setDetail] = useState<SheetDetail | null>(null);
  const [grid, setGrid] = useState<SheetGrid | null>(null);
  const [pre, setPre] = useState<SheetPreflight | null>(null);
  const [loadErr, setLoadErr] = useState<ApiError | null>(null);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [side, setSide] = useState<'plan' | 'unplanned'>('plan');
  const [filter, setFilter] = useState<RecordFilter>('pending');
  const [q, setQ] = useState('');
  const [selKey, setSelKey] = useState<string | null>(null);
  const [view, setView] = useState<View>('arrange');
  const [cancelReason, setCancelReason] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [reopenReason, setReopenReason] = useState<string | null>(null);
  const [reopening, setReopening] = useState(false);
  const [rcpt, setRcpt] = useState<Receipt>({ kind: 'idle' });
  const autoRead = useRef<number | null>(null);
  const [manualVoid, setManualVoid] = useState<{ ext_no: string; plans: string[] }[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [reading, setReading] = useState<string | null>(null);
  const [sub, setSub] = useState<OwsSubmission | null>(null);
  const [subErr, setSubErr] = useState<ApiError | null>(null);
  const [subLoading, setSubLoading] = useState(false);
  const [submitOpen, setSubmitOpen] = useState(false);

  const load = useCallback(async () => {
    let d: SheetDetail;
    try {
      const [d_, g] = await Promise.all([api.getSheet(sheetId), api.getSheetGrid(sheetId)]);
      d = d_;
      // 旧后端没有 in_flight：读成 undefined 会被 `> 0` 当成「没在提交」—— 点名，不默认（同列表 missingPlanFields）
      if (d.in_flight === undefined) {
        console.error('[sheet] detail shape mismatch', JSON.stringify({ call: `GET /v1/sheets/${sheetId}`, missing: ['in_flight'] }));
      }
      setDetail(d);
      setGrid(g);
      setLoadErr(null);
      setPre(d.state === '草稿' ? await api.sheetPreflight(sheetId) : null);
    } catch (e) {
      setLoadErr(asApiError(e));
      return;
    }
    // 提交预览读不到不该把整页拖垮：排货表本身照常显示，坞里点名
    // 海外仓预览要现查三方 sku 配对（实测 4.5 s）：这段时间按钮照样在，写「核对中」—— 两条链看到的是同一个按钮
    setSubLoading(d.state === '已确认');
    try {
      setSub(d.state === '已确认' ? await api.getSubmission(sheetId) : null);
      setSubErr(null);
    } catch (e) {
      setSub(null);
      setSubErr(asApiError(e));
    } finally {
      setSubLoading(false);
    }
  }, [sheetId]);
  useEffect(() => { void load(); }, [load]);

  /** 回执（spec 2026-10-13）：现查这张表已投送单在领星的进度。结果逐张写在单上（同采购单「重新读回」） */
  const readReceipt = useCallback(async () => {
    const t0 = performance.now();
    setRcpt({ kind: 'loading' });
    try {
      const r = await api.receiptSheet(sheetId);
      const bad = r.results.filter((x) => x.outcome !== 'ok');
      console.info('[sheet] receipt', JSON.stringify({ sheet_id: sheetId, run_id: r.run_id, refs: r.results.length,
        failed: bad.map((x) => ({ ext_no: x.ext_no, outcome: x.outcome, cause: x.cause })), archived: r.archived,
        ms: Math.round(performance.now() - t0) }));
      setRcpt({ kind: 'ok', r });
      if (r.archived.length) setFlash({ tone: 'ok', text: `排货单 ${r.archived.map((c) => `#${c}`).join('、')} 领星已完成，已归档` });
      await load();
    } catch (e) {
      const err = asApiError(e);
      console.warn('[sheet] receipt failed', JSON.stringify({ sheet_id: sheetId, status: err.status, error: err.error,
        fields: err.fields, ms: Math.round(performance.now() - t0) }));
      setRcpt({ kind: 'fail', err });
    }
  }, [sheetId, load]);
  // 打开即现查一次（同采购单）：库里那条观察可能是半小时前定时轮询的
  const hasLiveRefs = !!detail && detail.ext_refs.some((e) => e.voided_at === null);
  useEffect(() => {
    if (hasLiveRefs && autoRead.current !== sheetId) {
      autoRead.current = sheetId;
      void readReceipt();
    }
  }, [hasLiveRefs, sheetId, readReceipt]);

  if (loadErr) return <AppShell crumb={`排货表 #${sheetId}`} wide><ErrorDetail err={loadErr} /></AppShell>;
  if (!detail || !grid) return <AppShell crumb={`排货表 #${sheetId}`} wide><div className="empty">正在取排货表…</div></AppShell>;

  const frozen = detail.state !== '草稿';
  const put = async (body: CellPut, label: string): Promise<boolean> => {
    try {
      const r = await api.putSheetCell(sheetId, body);
      const over = r.warnings.find((w) => w.code === 'over_available');
      const unknown = r.warnings.some((w) => w.code === 'stock_unavailable');
      setFlash(body.units
        ? { tone: over || unknown ? 'warn' : 'ok',
            text: `已排 ${n(body.units)} 件 ${label}${over && over.code === 'over_available' ? `　超出可安排 ${n(-over.available)} 件（确认时 G3 会挡）` : ''}${unknown ? '　库存读不到，没法核对可安排' : ''}` }
        : { tone: 'info', text: `已撤回 ${label}` });
      await load();
      return true;
    } catch (e) {
      const err = asApiError(e);
      setFlash({ tone: 'danger', text: err.hint, err });
      return false;
    }
  };

  /** CA-D12「现查领星」：库存与采购单读回各自成败，各自点名 —— 读不到不写成 0 */
  const liveRead = async (sku: string) => {
    setReading(sku);
    try {
      const r = await api.liveRead(sheetId, sku);
      const parts: string[] = [];
      let tone: Flash['tone'] = 'ok';
      if ('error' in r.stock) {
        tone = 'warn';
        parts.push(`库存现读失败（${String(r.stock.error['outcome'] ?? '原因未知')}）`);
      } else {
        parts.push(`库存 ${r.stock.buckets.length} 个仓`);
      }
      parts.push(`采购单 ${r.purchase_orders.length} 张`);
      for (const p of r.purchase_orders.filter((x) => x.outcome !== 'ok')) {
        tone = 'warn';
        parts.push(`${p.order_sn} 读回 ${p.outcome}`);
      }
      setFlash({ tone, text: `已现查领星 ${sku}：${parts.join('，')}` });
      await load();
    } catch (e) {
      const err = asApiError(e);
      setFlash({ tone: 'danger', text: err.hint, err });
    } finally {
      setReading(null);
    }
  };

  const confirm = async () => {
    setConfirming(true);
    try {
      await api.confirmSheet(sheetId, detail.watermark);
      pushToast({ kind: 'ok', text: '已确认排货表' });
      setFlash(null);
      await load();
    } catch (e) {
      const err = asApiError(e);
      if (err.error === 'confirm_blocked') {
        setPre({ sheet_id: sheetId, ok: false, gates: err.fields['gates'] as DispatchGate[], stock_as_of: pre?.stock_as_of ?? null });
        setView('gates');
      }
      setFlash({ tone: 'danger', text: err.hint, err });
    } finally {
      setConfirming(false);
    }
  };

  const cancel = async (reason: string) => {
    if (cancelling) return;
    setCancelling(true);
    try {
      const r = await api.cancelSheet(sheetId, reason);
      const mv = r.manual_void ?? [];
      const deleted = r.deleted_ows.filter((x) => !mv.some((m) => m.ext_no === x));
      pushToast({ kind: 'ok', text: deleted.length ? `已取消排货表，删除领星备货单 ${deleted.join('、')}` : '已取消排货表' });
      setManualVoid(mv);          // ★ CC-D4：FBA 计划没有作废接口 —— 常驻点名，不是一闪而过的 toast
      setCancelReason(null);
      await load();
    } catch (e) {
      const err = asApiError(e);
      // 前端等不到 ≠ 后端没删：可能已删了几张
      setFlash(err.error === 'timeout'
        ? { tone: 'warn', text: '等太久没有回应：结果未知，刷新看哪些领星备货单已删、表是否已取消', err }
        : { tone: 'danger', text: err.hint, err });
      setCancelReason(null);
      await load();
    } finally {
      setCancelling(false);
    }
  };

  /** ★ CA-D14：冻结点是「发到领星」—— 没发过就能退回修改 */
  const reopen = async (reason: string) => {
    if (reopening) return;
    setReopening(true);
    try {
      await api.reopenSheet(sheetId, reason);
      pushToast({ kind: 'ok', text: '已退回修改，改完重新确认' });
      setReopenReason(null);
      await load();
    } catch (e) {
      const err = asApiError(e);
      setFlash({ tone: 'danger', text: err.hint, err });
      setReopenReason(null);
      await load();
    } finally {
      setReopening(false);
    }
  };

  const totals = planTotals(grid);
  const gates = pre?.gates ?? detail.last_gate_run?.gates ?? [];
  const blocking = blockingGates(gates);
  const live = detail.consignments.filter((c) => c.our_stage !== '已放弃' && c.our_stage !== '已取消');
  const packMissing = live.reduce((a, c) => a + missingPacking(c, detail.packing), 0);
  const rev = grid.demand.reduce((a, l) => Math.max(a, l.rev), 0);
  const liveRefs = detail.ext_refs.filter((e) => e.voided_at === null);
  const toCreate = sub ? sub.orders.filter((o) => o.ext_no === null) : [];
  const toLock = sub?.to_lock ?? [];
  const submittable = detail.route === 'oversea' || detail.route === 'fba';
  const stateChip = detail.state === '草稿' ? 'chip--ours' : detail.state === '已确认' ? 'chip--ok' : 'chip--dim';

  return (
    <AppShell crumb={`排货表 #${sheetId}`} wide>
      <div className="ds">
        <header className="ds-head">
          <div className="ds-head__id">
            <h2 className="pdt-sn">排货表 #{sheetId} <span className={`chip ${stateChip}`} data-testid="sheet-state">{detail.state}</span>{' '}
              <span className={`chip ${detail.route === null ? 'chip--bad' : detail.route === 'fba' ? 'ds-route--fba' : 'ds-route--os'}`} data-testid="sheet-route">
                {detail.route === null ? '旧版混合表：只能取消' : ROUTE_LABEL[detail.route]}
              </span>
            </h2>
            <div className="pdt-meta">
              <span data-testid="sheet-plan">
                {grid.sheet.plan_title ? <>销售计划 <b>{grid.sheet.plan_title}{rev ? ` rev${rev}` : ''}</b></> : '未关联销售计划'}
              </span>{' '}
              <span className={grid.stock_error ? 'ds-neg' : undefined} data-testid="stock-as-of">
                {grid.stock_error ? `库存读不到（${String(grid.stock_error.cause['kind'] ?? '原因未知')}），可安排未知，确认不可用` : `库存截至 ${grid.stock_as_of ?? '未知'}`}
              </span>{' '}
              <Link to="/dispatch">全部排货表</Link>
            </div>
          </div>
          <div className="ds-kpis" data-testid="plan-kpis">
            {([['需求', totals.total], ['本表已排', totals.here], ['别的表已排未发', totals.other], ['已投送', totals.sent]] as const).map(([l, v]) => (
              <div key={l} className="pm-kpi"><span className="pm-kpi__l">{l}</span><span className="pm-kpi__v">{n(v)}</span></div>
            ))}
            <div className="pm-kpi ds-kpi--left"><span className="pm-kpi__l">还差</span>
              <span className={`pm-kpi__v${totals.left === 0 ? ' ds-done' : ''}`} data-testid="plan-left">{totals.left === 0 ? '已满足' : n(totals.left)}</span></div>
            <div className="pm-kpi"><span className="pm-kpi__l">记录</span>
              <span className="pm-kpi__v">{totals.linesLeft} 条还差，{totals.linesDone} 条已满足</span></div>
          </div>
        </header>

        {flash && (
          <div className={`ds-flash ds-flash--${flash.tone}`} role={flash.tone === 'danger' ? 'alert' : 'status'} data-testid="ds-flash">
            <span>{flash.text}</span>
            {flash.err && <code>{flash.err.status} {flash.err.error}</code>}
            {flash.err && Object.keys(flash.err.fields).length > 0 && (
              <Fold summary="点名"><ErrorDetail err={flash.err} /></Fold>
            )}
            <button type="button" className="pe-x" onClick={() => setFlash(null)}>关闭</button>
          </div>
        )}

        {detail.autofill && <AutofillNote af={detail.autofill} grid={grid} />}

        {manualVoid.length > 0 && (
          <div className="flash flash--bad" role="alert" data-testid="manual-void">
            已取消。领星发货计划需在领星手工作废（领星没有作废接口；库存已释放）：
            {manualVoid.map((m) => `${m.ext_no}（计划 ${m.plans.join('、')}）`).join('；')}
          </div>
        )}
        {sub && sub.attention.length > 0 && (
          <AttentionBar sheetId={sheetId} items={sub.attention} grid={grid} onDone={load} onFlash={setFlash} />
        )}

        <div className="ds-body">
          <RecordList grid={grid} side={side} setSide={(s) => { setSide(s); setSelKey(null); }} filter={filter} setFilter={setFilter}
            q={q} setQ={setQ} selKey={selKey} onSelect={(k) => { setSelKey(k); setView('arrange'); }} />
          <div className="ds-main">
            <div className="pm-seg ds-views" role="tablist">
              <button type="button" role="tab" aria-pressed={view === 'arrange'} onClick={() => setView('arrange')}>安排</button>
              <button type="button" role="tab" aria-pressed={view === 'cons'} onClick={() => setView('cons')}>
                排货单与装箱<span className={`pm-seg__n${packMissing ? ' pm-seg__n--warn' : ''}`}>{live.length}</span>
              </button>
              <button type="button" role="tab" aria-pressed={view === 'gates'} onClick={() => setView('gates')}>
                确认检查<span className={`pm-seg__n${blocking.length ? ' pm-seg__n--danger' : ''}`}>{blocking.length}</span>
              </button>
            </div>
            {view === 'arrange' && (
              <Arrange grid={grid} side={side} filter={filter} q={q} selKey={selKey} frozen={frozen} put={put}
                marks={autofillMarks(detail.autofill ?? null, grid.cells)}
                reading={reading} onLiveRead={(sku) => void liveRead(sku)} />
            )}
            {view === 'cons' && (
              <Consignments sheetId={sheetId} grid={grid} detail={detail} frozen={frozen} onSaved={load}
                onFlash={setFlash} rcpt={rcpt} onReread={() => void readReceipt()} />
            )}
            {view === 'gates' && (gates.length > 0
              ? <TriGateList gates={gates} source={frozen ? '确认时的结果' : '确认时由服务端重跑'}
                  actions={frozen ? {} : { G7: <button type="button" className="btn btn--sm" onClick={() => setView('cons')}>去选物流方式</button> }} />
              : <div className="pm-empty">还没有检查结果</div>)}
          </div>
        </div>

        <div className="pe-bar ds-dock" data-testid="dock">
          <div className="ds-dock__cons">
            {live.length === 0 ? <span className="pe-bar__state">还没有排货单</span> : live.map((c) => {
              const miss = missingPacking(c, detail.packing);
              return (
                <button key={c.consignment_id} type="button" className="ds-chipbtn" onClick={() => setView('cons')}>
                  <span className={`ds-dest ${c.channel === 'fba' ? 'ds-dest--fba' : 'ds-dest--os'}`}>{c.channel === 'fba' ? 'FBA' : '海外仓'}</span>
                  {destLabel(grid, c).replace(/^(FBA|海外仓) /, '')}
                  <b>{n(c.lines.reduce((a, l) => a + l.units, 0))}</b>
                  {miss > 0 && <span className="ds-miss">缺装箱 {miss}</span>}
                  {liveRefs.filter((e) => e.consignment_id === c.consignment_id).map((e) => (
                    <span key={e.ext_ref_id} className={e.drift ? 'ds-neg' : 'pt-used'}>领星 {e.erp_stage ?? (rcpt.kind === 'loading' ? '查询中…' : '未读回')}</span>
                  ))}
                </button>
              );
            })}
          </div>
          <span className="pm-bar__sp" />
          {detail.state !== '已取消' && (
            <button type="button" className="btn btn--ghost" onClick={() => setCancelReason('')}>取消排货表</button>
          )}
          {detail.state === '已确认' && detail.ext_refs.length === 0 && !(sub && sub.attention.length > 0) && (
            <button type="button" className="btn btn--ghost" onClick={() => setReopenReason('')}>退回修改</button>
          )}
          {!frozen && blocking.length > 0 && (
            <span className="sc-commit__hint" data-testid="blocking">未过：{blocking.map((x) => `${x.gate} ${GATE_LABEL[x.gate] ?? ''}`.trim()).join('、')}</span>
          )}
          {!frozen && (
            <button type="button" className="btn btn--primary" disabled={confirming || blocking.length > 0 || live.length === 0}
              onClick={() => void confirm()}>
              {confirming ? '确认中…' : '确认排货表'}
            </button>
          )}
          {detail.state === '已确认' && detail.route === 'fba' && liveRefs.length > 0 && toCreate.length === 0 && toLock.length === 0 && (
            <span className="sc-commit__hint" data-testid="fba-next">已提交领星 FBA 发货计划：请在领星完成 STA 建货件 / 发货</span>
          )}
          {detail.state === '已确认' && subErr && (
            <span className="sc-commit__hint ds-neg" data-testid="ows-sub-error">提交领星的预览读不到：{subErr.hint}</span>
          )}
          {detail.state === '已确认' && submittable && subLoading && !sub && (
            <button type="button" className="btn btn--primary" disabled>提交领星（核对中…）</button>
          )}
          {detail.state === '已确认' && submittable && sub && toCreate.length + toLock.length > 0 && (
            <>
              {sub.blockers.length > 0 && (
                <span className="sc-commit__hint" data-testid="ows-blockers">{sub.blockers.map(blockerText(grid)).join('；')}</span>
              )}
              <button type="button" className="btn btn--primary"
                disabled={sub.blockers.length > 0 || sub.attention.length > 0} onClick={() => setSubmitOpen(true)}>
                {toCreate.length > 0 ? `提交领星（${toCreate.length} 张）` : `补锁库存（${toLock.length} 张）`}
              </button>
            </>
          )}
          {detail.state === '已确认' && detail.route === 'oversea' && sub && sub.orders.length > 0 && toCreate.length === 0 && (
            <span className="pe-bar__state">海外仓备货单已全部提交领星</span>
          )}
          {/* DL-D8/DL-Q7：只写状态字，不禁用提交按钮；后端在途时本就 409 submitting */}
          {detail.in_flight > 0 && <span className="pe-bar__state" data-testid="in-flight">正在提交…</span>}
          {detail.in_flight === undefined && (
            <span className="pe-bar__state" data-testid="in-flight-missing">读不到在途写调用：后端缺字段 in_flight</span>
          )}
          {detail.state === '已确认' && <span className="pe-bar__state">已确认，整表冻结</span>}
          {detail.state === '已取消' && <span className="pe-bar__state">已取消</span>}
        </div>
      </div>

      {cancelReason !== null && (
        <Modal title={`取消排货表 #${sheetId}`} onClose={() => setCancelReason(null)}>
          <div className="modal__body">
            <div>取消后它占的量全部释放，排货单一起作废</div>
            {liveRefs.some((e) => e.kind === 'fba_plan') && (
              <div className="flash flash--bad" data-testid="cancel-fba">
                会释放领星发货计划的库存（{liveRefs.filter((e) => e.kind === 'fba_plan').map((e) => e.ext_no).join('、')}）并在计划备注写「SCM 已取消 请作废」；
                领星没有作废接口，计划本身需在领星手工作废；有计划已「已处理」就不取消
              </div>
            )}
            {liveRefs.some((e) => e.kind !== 'fba_plan') && (
              <div className="flash flash--bad" data-testid="cancel-ows">
                会同时删除领星备货单 {liveRefs.filter((e) => e.kind !== 'fba_plan').length} 张（{liveRefs.filter((e) => e.kind !== 'fba_plan').map((e) => e.ext_no).join('、')}）；
                任一张在领星已过「待发货」就一张都不删，排货表也不取消；删到一半失败的，已删的照样留痕，表不取消，再点取消接着删
              </div>
            )}
            <input className="pe-reason" placeholder="理由（必填）" aria-label="取消理由" value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)} />
          </div>
          <div className="modal__foot">
            <button type="button" className="btn btn--ghost" onClick={() => setCancelReason(null)}>不取消</button>
            <button type="button" className="btn btn--danger" disabled={cancelling || cancelReason.trim() === ''}
              onClick={() => void cancel(cancelReason)}>{cancelling ? '取消中…' : '确认取消'}</button>
          </div>
        </Modal>
      )}

      {reopenReason !== null && (
        <Modal title={`退回修改排货表 #${sheetId}`} onClose={() => setReopenReason(null)}>
          <div className="modal__body">
            <div>回到草稿：格子、目的地、物流方式都能改，改完要重新确认</div>
            <input className="pe-reason" placeholder="理由（必填）" aria-label="退回理由" value={reopenReason}
              onChange={(e) => setReopenReason(e.target.value)} />
          </div>
          <div className="modal__foot">
            <button type="button" className="btn btn--ghost" onClick={() => setReopenReason(null)}>不退回</button>
            <button type="button" className="btn btn--primary" disabled={reopening || reopenReason.trim() === ''}
              onClick={() => void reopen(reopenReason)}>{reopening ? '退回中…' : '确认退回'}</button>
          </div>
        </Modal>
      )}

      {submitOpen && sub && (
        <SubmitModal sheetId={sheetId} grid={grid} sub={sub} orders={toCreate} route={detail.route}
          onClose={() => { setSubmitOpen(false); void load(); }} />
      )}
    </AppShell>
  );
}

const AF_STAGE: Record<string, string> = { read: '读计划与镜像', allocate: '计算', stock: '读本地库存', write: '写格子' };

/** UX-D7：新建时系统填的那一版。成功只写结果，规则与跳过清单进折叠区；没做成不折叠、点名原因 */
function AutofillNote({ af, grid }: { af: SheetAutofill; grid: SheetGrid }) {
  if (af.outcome === 'failed') {
    const e = af.error;
    return (
      <div className="ds-flash ds-flash--warn" role="alert" data-testid="autofill">
        <span>默认分配没做成：{e?.hint ?? '原因未知'}{e ? `（${AF_STAGE[e.stage] ?? e.stage}）` : ''}，格子要人填</span>
        {e && <code>{e.error}</code>}
        {e && Object.keys(e.fields).length > 0 && <Fold summary="点名"><pre className="pt-used">{JSON.stringify(e.fields, null, 1)}</pre></Fold>}
      </div>
    );
  }
  const s = autofillSummary(af);
  const line = (id: number) => grid.demand.find((l) => l.line_id === id);
  const store = (sid?: string) => (sid ? grid.demand.flatMap((l) => l.stores).find((x) => x.sid === sid)?.seller_name ?? sid : '');
  return (
    <div className="ds-flash ds-flash--info" role="status" data-testid="autofill">
      <span>{s.cells > 0 ? `新建时系统填了 ${s.cells} 格 ${n(s.units)} 件默认排货量` : '新建时没有可默认填的格子'}{s.skipped > 0 ? `，跳过 ${s.skipped} 条` : ''}</span>
      <Fold summary="规则与跳过" testId="autofill-fold">
        <div>每条记录的还差按各店期望比例拆到店、店内按认领 msku 期望拆，封顶到可安排；按月份先后，库存用完为止（{af.rule}）。填进去就是普通格子，改了不会重填。</div>
        {af.skipped.length > 0 && (
          <ul className="pl-ex">
            {af.skipped.map((x, i) => {
              const l = line(x.line_id);
              return (
                <li key={i}>{l ? `${l.sku} ${l.period.slice(0, 7)}` : `记录 ${x.line_id}`} {store(x.sid)} {x.seller_sku ?? ''}：{AUTOFILL_REASON[x.reason] ?? x.reason}
                  {x.wanted !== undefined && `（还差 ${n(x.wanted)}，填了 ${n(x.filled ?? 0)}）`}</li>
              );
            })}
          </ul>
        )}
      </Fold>
    </div>
  );
}

const wh = (grid: SheetGrid, b: OwsBlocker) => (b.dest_wid != null ? whName(grid, b.dest_wid) : '目的仓');

const blockerText = (grid: SheetGrid) => (b: OwsBlocker): string =>
  b.code === 'fnsku_missing' ? `msku ${b.seller_sku ?? '?'} 在领星 listing 里没有 FNSKU（FBA 发货计划必填）：多半还没转成亚马逊配送 —— 在卖家后台把配送渠道改为亚马逊配送，领星同步后再点提交`
    : b.code === 'fnsku_unavailable' ? '领星 listing 读不到，没法核对 FNSKU：稍后再试'
    : b.code === 'packing_missing' ? `msku ${b.seller_sku ?? '?'} 装箱没填（单箱件数 / 箱数）`
    : b.code === 'third_party_sku_missing' ? `货号 ${b.sku ?? b.product_id ?? '?'} 在 ${wh(grid, b)} 还没配对三方 sku：在领星该海外仓的产品配对里配好，再点提交`
    : b.code === 'third_party_sku_ambiguous' ? `货号 ${b.sku ?? '?'} 在 ${wh(grid, b)} 配对了多个三方 sku（${(b.codes ?? []).join('、')}）：在领星只留一个`
    : b.code === 'sku_match_unavailable' ? `${wh(grid, b)} 的三方 sku 配对读不到（领星接口失败），稍后再试`
    : b.code === 'route_not_submittable' ? '平台仓表不在这里提交'
    : b.code === 'legacy_mixed' ? '旧版混合表不能提交，只能取消'
    : b.code === 'cancel_incomplete' ? '取消只做了一半（领星备货单删了一部分）：先点「取消排货表」把它做完'
    : b.code === 'product_id_missing' ? `货号 ${b.sku ?? '?'} 在领星没有产品 ID`
    : b.code === 'logistics_missing' ? `${(b.dest_wid != null ? whName(grid, b.dest_wid) : '目的仓')} 没选物流方式`
      : b.code === 'logistics_disabled' ? `${(b.dest_wid != null ? whName(grid, b.dest_wid) : '目的仓')} 的物流方式已停用` : b.code;

const OUTCOME: Record<OwsResult['outcome'], string> = {
  created: '已建', adopted: '领星已有，认下', rejected: '领星拒绝', not_sent: '没发出去（可再点提交）',
  lookup_failed: '查重失败，没建（可再点提交）', needs_verify: '结果未知，待核实',
  unrecorded: '领星已建，但本地没记上：把这个单号报给管理员',
  locked: '已补锁库存',
  lock_failed: '发货计划已建，库存没锁上：再点提交只补锁，不会重建',
};

/** ⛔ 出站确认：逐张列出要在领星建的备货单；提交后逐张回显结果（出错就停在那一张，剩下的等人看过再点） */
function SubmitModal({ sheetId, grid, sub, orders, route, onClose }: {
  sheetId: number; grid: SheetGrid; sub: OwsSubmission; orders: OwsSubmission['orders']; route: DispatchRoute | null;
  onClose: () => void;
}) {
  const fba = route === 'fba';
  const toLock = sub.to_lock ?? [];
  // ★ CC-D1 退回边「建了没锁上 → 补锁」：没有要新建的单时，弹窗列的是要补锁的那几张，按它们放行
  const relockOnly = orders.length === 0 && toLock.length > 0;
  const shown = relockOnly ? sub.orders.filter((o) => toLock.includes(o.idem_key)) : orders;
  const noun = fba ? 'FBA 发货计划' : '海外仓备货单';
  const title = relockOnly ? `⛔ 补锁库存：${toLock.length} 张 ${noun}` : `⛔ 提交领星：建 ${orders.length} 张 ${noun}`;
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<OwsResult[] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const go = async () => {
    setBusy(true);
    try {
      setResults((await api.submitSheet(sheetId, sub.confirm_token)).results);
      setErr(null);
    } catch (e) {
      setErr(asApiError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={title} onClose={busy ? () => {} : onClose}>
      <div className="modal__body">
        {results === null ? (
          <>
            {relockOnly ? <div>上次建成了但库存没锁上：这次只补锁 {toLock.join('、')}，不会重建</div> : fba
              ? <div>在领星建 FBA 发货计划（参考号写批次备注），建后锁定本地仓库存；STA 建货件 / 发货在领星里做。取消时释放库存，计划需在领星手工作废</div>
              : <div>在领星建为「待发货」，会锁定本地仓库存；取消排货表时可删除（已发货的删不掉）</div>}
            <table className="pd-t" data-testid="ows-orders">
              <thead><tr><th>参考号</th><th>来源仓 → 目的地</th><th>{fba ? '装箱' : '物流方式'}</th><th className="r">件数</th><th className="r">产品行</th></tr></thead>
              <tbody>
                {shown.map((o) => (
                  <tr key={o.key}>
                    <td><code>{o.idem_key}</code></td>
                    <td>{whName(grid, o.s_wid)} → {o.r_wid !== null ? whName(grid, o.r_wid) : `FBA 店 ${o.dest_sid ?? '?'}`}</td>
                    <td>{fba ? o.product_list.map((p) => `${String(p['box_num'] ?? '?')} 箱 × ${String(p['quantity_in_case'] ?? '?')}`).join('；')
                      : (o.channel_name ?? '未选')}</td>
                    <td className="r">{n(o.lines.reduce((a, l) => a + l.units, 0))}</td>
                    <td className="r">{o.product_list.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {sub.skipped_fba.length > 0 && <div className="pt-used">FBA 排货单 {sub.skipped_fba.length} 张不在这里提交</div>}
          </>
        ) : (
          <ul className="ds-results" data-testid="ows-results">
            {results.map((r) => (
              <li key={r.key} className={r.outcome === 'created' || r.outcome === 'adopted' ? 'ds-done' : 'ds-neg'}>
                <code>{r.idem_key}</code> {OUTCOME[r.outcome] ?? r.outcome}
                {r.ext_no && <> <b>{r.ext_no}</b></>}
                {r.lx_msg && <>：{r.lx_msg}</>}
                {r.cause && <span className="pt-used">（{r.cause}）</span>}
              </li>
            ))}
            {results.length < shown.length && <li className="pt-used">其余 {shown.length - results.length} 张没有发：先看上面那一张</li>}
          </ul>
        )}
        {err && (
          <div className="flash flash--bad" role="alert">
            {err.error === 'timeout' ? '等太久没有回应：结果未知，关掉后刷新看哪些已建' : err.hint}
            <Fold summary="点名"><ErrorDetail err={err} /></Fold>
          </div>
        )}
      </div>
      <div className="modal__foot">
        {results === null ? (
          <>
            <button type="button" className="btn btn--ghost" disabled={busy} onClick={onClose}>不提交</button>
            <button type="button" className="btn btn--danger" disabled={busy || shown.length === 0} onClick={() => void go()}>
              {busy ? '提交中…' : relockOnly ? '确认补锁' : '确认提交'}
            </button>
          </>
        ) : <button type="button" className="btn btn--primary" onClick={onClose}>关闭</button>}
      </div>
    </Modal>
  );
}

/** 待核实：领星里查不到、也不能确定没建。三个出口：重新查 · 登记已有单号 · 确认领星无此单（要理由） */
function AttentionBar({ sheetId, items, grid, onDone, onFlash }: {
  sheetId: number; items: DispatchAttention[]; grid: SheetGrid; onDone: () => Promise<void>; onFlash: (f: Flash) => void;
}) {
  const [extNo, setExtNo] = useState<Record<number, string>>({});
  const [reason, setReason] = useState<Record<number, string>>({});
  const run = async (label: string, f: () => Promise<unknown>) => {
    try {
      await f();
      onFlash({ tone: 'ok', text: label });
      await onDone();
    } catch (e) {
      const err = asApiError(e);
      onFlash({ tone: 'danger', text: err.hint, err });
    }
  };
  return (
    <div className="flash flash--bad" role="alert" data-testid="ows-attention">
      {items.map((a) => (
        <div key={a.attention_id} className="ds-attn">
          <span>备货单 <code>{a.idem_key}</code>（{whName(grid, a.from_wid)}）提交结果未知，先核实再继续</span>
          <button type="button" className="btn btn--sm" onClick={() => void run(`已核实 ${a.idem_key}`, () => api.verifyExtRef(sheetId, a.attention_id))}>重新查</button>
          <input className="pe-reason" aria-label="领星备货单号" placeholder="领星备货单号" value={extNo[a.attention_id] ?? ''}
            onChange={(e) => setExtNo({ ...extNo, [a.attention_id]: e.target.value })} />
          <button type="button" className="btn btn--sm" disabled={!(extNo[a.attention_id] ?? '').trim()}
            onClick={() => void run(`已登记 ${a.idem_key}`, () => api.registerExtRef(sheetId, a.attention_id, (extNo[a.attention_id] ?? '').trim()))}>登记</button>
          <input className="pe-reason" aria-label="确认领星无此单的理由" placeholder="理由（必填）" value={reason[a.attention_id] ?? ''}
            onChange={(e) => setReason({ ...reason, [a.attention_id]: e.target.value })} />
          <button type="button" className="btn btn--sm btn--danger" disabled={!(reason[a.attention_id] ?? '').trim()}
            onClick={() => void run(`已确认领星无 ${a.idem_key}，可重新提交`, () => api.confirmExtAbsent(sheetId, a.attention_id, (reason[a.attention_id] ?? '').trim()))}>确认领星无此单</button>
        </div>
      ))}
    </div>
  );
}

// ── 左栏：记录清单 ───────────────────────────────────────

function RecordList({ grid, side, setSide, filter, setFilter, q, setQ, selKey, onSelect }: {
  grid: SheetGrid; side: 'plan' | 'unplanned'; setSide: (s: 'plan' | 'unplanned') => void;
  filter: RecordFilter; setFilter: (f: RecordFilter) => void; q: string; setQ: (q: string) => void;
  selKey: string | null; onSelect: (k: string) => void;
}) {
  const recs = filterRecords(grid, filter, q);
  const needle = q.trim().toLowerCase();
  const un = grid.unplanned.filter((u) => needle === ''
    || `${u.sku} ${u.stores.flatMap((s) => s.mskus.map((m) => m.seller_sku)).join(' ')}`.toLowerCase().includes(needle));
  const sel = selectedKey(grid, side, filter, q, selKey);
  const whs = (sku: string) => (grid.stock ?? []).filter((s) => s.sku === sku).length;
  const ex = grid.excluded;
  return (
    <div className="ds-list" data-testid="record-list">
      <div className="pm-seg ds-tabs" role="tablist">
        <button type="button" role="tab" aria-pressed={side === 'plan'} onClick={() => setSide('plan')}>
          计划记录<span className="pm-seg__n">{grid.demand.length}</span></button>
        <button type="button" role="tab" aria-pressed={side === 'unplanned'} onClick={() => setSide('unplanned')}>
          无计划来源<span className="pm-seg__n">{grid.unplanned.length}</span></button>
      </div>
      <div className="pm-search-wrap">
        <input className="pm-search ds-search" type="search" aria-label="搜索记录" value={q}
          placeholder={side === 'plan' ? '搜货号、品名' : '搜货号、msku'} onChange={(e) => setQ(e.target.value)} />
      </div>
      {side === 'plan' && (
        <div className="pm-seg ds-filter">
          {([['pending', '还差 > 0'], ['stock', '本地有货'], ['all', '全部']] as const).map(([k, l]) => (
            <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
          ))}
        </div>
      )}
      {side === 'plan' && grid.stock === null && <div className="ds-warn">库存读不到：「N 个仓有货」未知</div>}
      <ul className="ds-recs" role="listbox" aria-label={side === 'plan' ? '计划记录' : '无计划来源库存'}>
        {side === 'plan' ? recs.map((l) => {
          const k = `L${l.line_id}`;
          const w = whs(l.sku);
          return (
            <li key={k} role="option" aria-selected={sel === k} data-testid={`rec-${l.line_id}`}
              className={`ds-rec${sel === k ? ' is-on' : ''}${l.pending === 0 ? ' is-done' : ''}`} onClick={() => onSelect(k)}>
              <div className="ds-rec__top">
                <span className="ds-rec__sku">{l.sku}</span>
                <span className="ds-rec__per">{l.period.slice(0, 7)}</span>
                {(l.state === '已撤销' || l.state === '已完结') && <span className="chip chip--bad">{l.state}</span>}
                <span className={`ds-rec__left${l.pending === 0 ? ' is-done' : l.pending < 0 ? ' is-over' : ''}`}>
                  {l.pending === 0 ? '已满足' : l.pending < 0 ? `超 ${n(-l.pending)}` : `还差 ${n(l.pending)}`}
                </span>
              </div>
              <div className="ds-rec__nums">
                <span>需求 <b>{n(l.total)}</b></span>
                <span>本表 <b>{n(l.staged_here)}</b></span>
                {l.staged_other > 0 && <span>别表 <b>{n(l.staged_other)}</b></span>}
                {grid.stock !== null && <span className={w ? 'ds-has' : 'ds-none'}>{w ? `${w} 个仓有货` : '本地无货'}</span>}
              </div>
            </li>
          );
        }) : un.slice(0, 200).map((u) => {
          const k = `U${u.sku}`;
          return (
            <li key={k} role="option" aria-selected={sel === k} data-testid={`urec-${u.sku}`}
              className={`ds-rec${sel === k ? ' is-on' : ''}`} onClick={() => onSelect(k)}>
              <div className="ds-rec__top"><span className="ds-rec__sku">{u.sku}</span>
                <span className="ds-rec__per">{u.sku_name}</span></div>
              <div className="ds-rec__nums"><span>本地 <b>{whs(u.sku)}</b> 个仓有货</span></div>
            </li>
          );
        })}
      </ul>
      {side === 'plan' && recs.length === 0 && <div className="pm-empty">没有符合的记录</div>}
      {side === 'unplanned' && un.length > 200 && <div className="pm-empty">只列前 200 个，再缩小搜索</div>}
      {side === 'plan' && (
        <Fold summary="未列入">
          <ul className="pl-ex" data-testid="excluded">
            <li>不可排的记录（已撤销 / 已完结）<b> {ex.lines_not_dispatchable}</b></li>
            <li>店铺不在镜像里<b> {ex.seller_not_in_mirror.length}</b></li>
            <li>缺 msku<b> {ex.msku_missing.length}</b></li>
            <li>库存行店铺认不出<b> {ex.stock ? `${ex.stock['unknown_seller']?.rows ?? 0} 行 / ${ex.stock['unknown_seller']?.units ?? 0} 件` : '未知'}</b></li>
            <li>库存行不是本地仓<b> {ex.stock ? `${ex.stock['not_local_wid']?.rows ?? 0} 行 / ${ex.stock['not_local_wid']?.units ?? 0} 件` : '未知'}</b></li>
          </ul>
        </Fold>
      )}
    </div>
  );
}

/** 当前选中：点过的那条；没点过就是清单第一条 */
function selectedKey(grid: SheetGrid, side: 'plan' | 'unplanned', filter: RecordFilter, q: string, selKey: string | null): string | null {
  if (selKey) return selKey;
  if (side === 'plan') { const f = filterRecords(grid, filter, q)[0]; return f ? `L${f.line_id}` : null; }
  const u = grid.unplanned[0];
  return u ? `U${u.sku}` : null;
}

// ── 右栏 · 安排 ─────────────────────────────────────────

type Marks = ReturnType<typeof autofillMarks>;

function Arrange({ grid, side, filter, q, selKey, frozen, put, marks, reading, onLiveRead }: {
  grid: SheetGrid; side: 'plan' | 'unplanned'; filter: RecordFilter; q: string; selKey: string | null; frozen: boolean; marks: Marks;
  put: (b: CellPut, label: string) => Promise<boolean>; reading: string | null; onLiveRead: (sku: string) => void;
}) {
  const key = selectedKey(grid, side, filter, q, selKey);
  const line: DemandRow | null = key?.startsWith('L') ? grid.demand.find((l) => `L${l.line_id}` === key) ?? null : null;
  const un: UnplannedSku | null = key?.startsWith('U') ? grid.unplanned.find((u) => `U${u.sku}` === key) ?? null : null;
  if (!line && !un) return <div className="pm-empty">左边选一条记录</div>;
  const sku = line ? line.sku : un!.sku;
  const stores: StoreLike[] = line ? line.stores : un!.stores;
  const live = !line || (line.state !== '已撤销' && line.state !== '已完结');
  return (
    <div className="ds-arr" data-testid="arrange">
      <div className="ds-arr__head">
        <span className="ds-arr__sku">{sku}</span>
        <span className="ds-arr__name">{line ? line.sku_name : un!.sku_name}</span>
        {line && <span className="ds-arr__per">{line.period.slice(0, 7)}</span>}
        {line ? <span className="chip">记录状态 {line.state}</span> : <span className="chip chip--dim">无计划来源</span>}
        <span className="pm-bar__sp" />
        <button type="button" className="btn btn--sm" disabled={reading !== null} onClick={() => onLiveRead(sku)}
          title="现读这个货号的本地仓库存，并读回它关联的已下单采购单">
          {reading === sku ? '现查中…' : '现查领星'}
        </button>
      </div>
      {line && !live && (
        <div className="flash flash--bad" role="alert">记录{line.state}，只能撤回：本表在这条记录上的格子确认时会被挡</div>
      )}
      <div className={line ? 'ds-sides' : undefined}>
        {line && (
          <section className="ds-side" data-testid="side-need"><div className="ds-side__t">需求</div>
            <dl className="ds-kv">
              <dt>需求</dt><dd>{n(line.total)}</dd>
              <dt>本表已排</dt><dd>{n(line.staged_here)}</dd>
              <dt>别的表已排未发</dt><dd>{n(line.staged_other)}</dd>
              <dt>已投送</dt><dd>{n(line.dispatched)}</dd>
              <dt className="ds-kv__big">还差</dt>
              <dd className={`ds-kv__big${line.pending === 0 ? ' ds-done' : ''}`}>{line.pending === 0 ? '已满足' : n(line.pending)}</dd>
            </dl>
          </section>
        )}
        {line && (
          <section className="ds-side" data-testid="side-purchase"><div className="ds-side__t">采购</div>
            <dl className="ds-kv">
              <dt>已组单</dt><dd>{n(line.confirmed)}</dd>
              <dt>已下单</dt><dd>{n(line.ordered)}</dd>
              <dt>已入库（整单）</dt><dd>{line.entered === null ? '未读到' : n(line.entered)}</dd>
              <dt>待到货（整单）</dt><dd>{line.arriving === null ? '未读到' : n(line.arriving)}</dd>
            </dl>
            {line.purchase_orders.length > 0 && (
              <ul className="ds-pos">
                {line.purchase_orders.map((p) => (
                  <li key={p.po_id} className={p.is_void ? 'is-void' : undefined} data-testid={`po-${p.po_id}`}>
                    <Link to={`/purchase/${p.po_id}`}><code>{p.order_sn ?? `SCM-PO-${p.po_id}`}</code></Link>
                    <span className="chip">{p.is_void ? '领星已作废' : p.state}</span>
                    <span>入库仓 {whName(grid, p.wid)}</span>
                    <span>本记录 {n(p.units)}</span>
                    <span className="pt-used">入库 {p.entered ?? '未读到'}　待到 {p.pending ?? '未读到'}</span>
                    {p.source && <span className="pt-used">{p.source === 'api' ? '领星读回' : '采集'} {fmtLocal(p.captured)}</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        <section className="ds-side ds-side--wide" data-testid="side-stock">
          <div className="ds-side__t">本地库存<span className="ds-asof">{stockSourceLabel(grid, sku)}</span></div>
          <StockTable grid={grid} sku={sku} stores={stores} />
        </section>
      </div>
      <div className="ds-stores">
        {stores.map((st) => (
          <section key={st.sid} className="ds-store" data-testid={`store-${st.sid}`}>
            <div className="ds-store__head">
              <span className="ds-store__n">{st.seller_name}</span>
              {st.market && <span className="pm-mkt">{st.market}</span>}
              {!st.has_fba && <span className="chip chip--dim">无 FBA</span>}
              {line && <span className="ds-store__exp">{st.expected == null ? '期望未知' : `期望 ${n(st.expected)}（参考）`}</span>}
            </div>
            {st.mskus.map((m) => {
              const mine = grid.cells.filter((c) => c.sid === st.sid && c.seller_sku === m.seller_sku
                && (line ? c.line_id === line.line_id : c.line_id === null && c.sku === sku));
              return (
                <div key={m.seller_sku} className="ds-msku" data-testid={`msku-${m.seller_sku}`}>
                  <div className="ds-msku__id"><code>{m.seller_sku}</code>{m.fnsku && <span className="pt-used">{m.fnsku}</span>}</div>
                  <div className="ds-msku__cells">
                    {mine.map((c) => <CellRow key={`${c.arrangement_id}/${c.units}`} grid={grid} cell={c} store={st} frozen={frozen} put={put}
                      mark={marks.get(c.arrangement_id)} />)}
                    {!frozen && live && grid.sheet.route !== null && (
                      <AddCell grid={grid} sku={sku} store={st} msku={m.seller_sku} lineId={line?.line_id ?? null}
                        left={line ? Math.max(0, line.pending) : null} existing={mine} put={put} />
                    )}
                  </div>
                </div>
              );
            })}
          </section>
        ))}
      </div>
    </div>
  );
}

/** 这个货号的库存来自哪：领星现读（带时刻）还是每日采集（带那批的采集时刻） */
function stockSourceLabel(grid: SheetGrid, sku: string): string {
  const rows = (grid.stock ?? []).filter((r) => r.sku === sku);
  const api = rows.find((r) => r.source === 'api');
  if (api) return `领星现读 ${fmtLocal(api.as_of)}`;
  const ch = rows[0]?.as_of ?? null;
  return ch ? `采集 截至 ${fmtLocal(ch)}` : grid.stock_as_of ? `采集 截至 ${grid.stock_as_of}` : '';
}

function StockTable({ grid, sku, stores }: { grid: SheetGrid; sku: string; stores: StoreLike[] }) {
  if (grid.stock === null) return <div className="ds-warn">库存读不到：可安排未知，确认不可用</div>;
  const rows = grid.stock.filter((r) => r.sku === sku);
  if (rows.length === 0) return <div className="pm-empty">本地没有这个货号的可发库存</div>;
  const best = (r: typeof rows[number]) => Math.max(...stores.map((s) => r.available[s.sid] ?? 0));
  const sorted = [...rows].sort((a, b) => best(b) - best(a));
  return (
    <table className="pd-t ds-stock" data-testid="stock-table">
      <thead><tr><th>本地仓</th><th className="r">共享桶</th>
        {stores.map((s) => <th key={s.sid} className="r">{s.seller_name} 可安排</th>)}</tr></thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={r.wid}>
            <td>{whName(grid, r.wid)}</td>
            <td className="r">{n(r.buckets['shared'] ?? 0)}</td>
            {stores.map((s) => {
              const a = r.available[s.sid] ?? 0;
              const own = r.buckets[s.sid] ?? 0;
              return (
                <td key={s.sid} className={`r${a < 0 ? ' ds-neg' : ''}`}>
                  {a < 0 ? `已超 ${n(-a)}` : n(a)}
                  {own > 0 && <span className="ds-own">含本店桶 {n(own)}</span>}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CellRow({ grid, cell, store, frozen, put, mark }: { grid: SheetGrid; cell: DispatchCell; store: StoreLike; frozen: boolean;
  put: (b: CellPut, label: string) => Promise<boolean>; mark: { units: number; changed: boolean } | undefined }) {
  // ★ CA-D14：确认后选错仓库，退回修改后在这里改目的地 —— 同一事务挪格，件数不变（不用撤了重加）
  const dests = grid.sheet.route === 'oversea' ? destOptions(store, grid.warehouses.oversea, 'oversea') : [];
  const move = async (key: string) => {
    const d = dests.find((x) => x.key === key);
    if (!d || key === destKey(cell)) return;
    await put({ line_id: cell.line_id, seller_sku: cell.seller_sku, sid: cell.sid, from_wid: cell.from_wid,
      channel: d.channel, dest_wid: d.dest_wid, dest_sid: d.dest_sid, units: cell.units,
      replaces: { channel: cell.channel, dest_wid: cell.dest_wid, dest_sid: cell.dest_sid, from_wid: cell.from_wid } },
    `${cell.seller_sku} 改去 ${d.label}`);
  };
  const [text, setText] = useState(String(cell.units));
  const avail = (stockFor(grid, cell.sku, cell.sid) ?? []).find((w) => w.wid === cell.from_wid)?.available ?? null;
  const label = `${cell.seller_sku} → ${destLabel(grid, cell)}`;
  const body = (units: number): CellPut => ({ line_id: cell.line_id, seller_sku: cell.seller_sku, sid: cell.sid,
    from_wid: cell.from_wid, channel: cell.channel, dest_wid: cell.dest_wid, dest_sid: cell.dest_sid, units });
  const commit = async () => {
    const p = parseUnits(text);
    if (!p.ok) { setText(String(cell.units)); return; }
    if (p.units === cell.units) return;
    if (!(await put(body(p.units), label))) setText(String(cell.units));
  };
  return (
    <div className={`ds-cell${avail !== null && avail < 0 ? ' is-over' : ''}`} data-testid={`cell-${cell.arrangement_id}`}>
      <span className="ds-cell__from">{whName(grid, cell.from_wid)}</span>
      <span className="ds-arrow">→</span>
      {!frozen && dests.length > 1 ? (
        <select className="ps-select" aria-label={`改目的地 ${cell.seller_sku} ${whName(grid, cell.from_wid)}`} value={destKey(cell)}
          onChange={(e) => void move(e.target.value)}>
          {dests.map((d) => <option key={d.key} value={d.key}>海外仓 {d.label}</option>)}
        </select>
      ) : <span className={`ds-dest ${cell.channel === 'fba' ? 'ds-dest--fba' : 'ds-dest--os'}`}>{destLabel(grid, cell)}</span>}
      {isThirdParty(grid, cell.dest_wid) && <span className="pt-used" title="第三方海外仓：货号须在领星配对三方 sku 后才能提交备货单">第三方仓 · 需配对</span>}
      <input className="pe-qty ds-units" inputMode="numeric" disabled={frozen} value={text}
        aria-label={`件数 ${cell.seller_sku} ${whName(grid, cell.from_wid)} ${destLabel(grid, cell)}`}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void commit(); }} onBlur={() => void commit()} />
      <span className="ds-cell__u">件</span>
      {mark && <span className="pt-used ds-auto" data-testid={`auto-${cell.arrangement_id}`}>
        {mark.changed ? `默认 ${n(mark.units)}，已改` : '默认'}</span>}
      {avail !== null && avail < 0 && <span className="ds-cell__warn">超出可安排 {n(-avail)}</span>}
      {!frozen && <button type="button" className="pe-x" onClick={() => void put(body(0), label)}>撤回</button>}
    </div>
  );
}

/** 加一格：来源仓（只列有货、可安排多的在前）→ 目的地 → 件数；回车或「排」生效。
 *  ★ 撞上已有格子（同来源仓同目的地）不发请求 —— 那会覆盖人填的量（终审 I1），让人去改那一格 */
function AddCell({ grid, sku, store, msku, lineId, left, existing, put }: {
  grid: SheetGrid; sku: string; store: StoreLike; msku: string; lineId: number | null; left: number | null;
  existing: DispatchCell[]; put: (b: CellPut, label: string) => Promise<boolean>;
}) {
  const opts = addOptions(grid, sku, store.sid);
  const dests = destOptions(store, grid.warehouses.oversea, grid.sheet.route);
  const [from, setFrom] = useState<number | null>(opts[0]?.wid ?? null);
  // ★ 去向已由表定（CA-D13）：FBA 表只有本店，带上；海外仓表一律不预选，要人选（spec §6）
  const preset = grid.sheet.route === 'fba' && dests.length === 1;
  const [dest, setDest] = useState<string>(preset ? dests[0]!.key : '');
  const [units, setUnits] = useState('');
  const [err, setErr] = useState<string | null>(null);
  if (grid.stock === null) return <div className="ds-add ds-add--none" data-testid={`add-${msku}`}>库存读不到，先不能加</div>;
  if (opts.length === 0) return <div className="ds-add ds-add--none" data-testid={`add-${msku}`}>本地没有可安排给这家店的库存</div>;
  const fromWid = from ?? opts[0]!.wid;
  const cur = opts.find((o) => o.wid === fromWid);
  const fill = cur ? fillFor(left, cur.available) : null;
  const target = dests.find((d) => d.key === dest);
  const go = async (raw: string) => {
    const p = parseUnits(raw);
    if (!p.ok || p.units === 0) { setErr('件数要是正整数'); return; }
    if (!target) { setErr('先选目的地'); return; }
    if (existing.some((c) => c.from_wid === fromWid && destKey(c) === dest)) { setErr('这一格已有安排，直接改那一格'); return; }
    setErr(null);
    const ok = await put({ line_id: lineId, seller_sku: msku, sid: store.sid, from_wid: fromWid, channel: target.channel,
      dest_wid: target.dest_wid, dest_sid: target.dest_sid, units: p.units }, `${msku} → ${target.channel === 'fba' ? `FBA ${store.seller_name}` : target.label}`);
    if (ok) setUnits('');
  };
  return (
    <div className="ds-add" data-testid={`add-${msku}`}>
      <select className="ps-select" aria-label={`${msku} 来源仓`} value={fromWid} onChange={(e) => setFrom(Number(e.target.value))}>
        {opts.map((o) => <option key={o.wid} value={o.wid}>{o.name}　可安排 {n(o.available)}</option>)}
      </select>
      <span className="ds-arrow">→</span>
      <select className="ps-select" aria-label={`${msku} 目的地`} value={dest} onChange={(e) => setDest(e.target.value)}>
        {!preset && <option value="">选目的地</option>}
        {dests.map((d) => <option key={d.key} value={d.key}>
          {d.channel === 'fba' ? `FBA ${store.seller_name}（本店）` : `海外仓 ${d.label}${isThirdParty(grid, d.dest_wid) ? '（第三方仓 · 需配对）' : ''}`}
        </option>)}
      </select>
      <input className="pe-qty ds-units" inputMode="numeric" placeholder="件数" aria-label={`${msku} 件数`} value={units}
        onChange={(e) => { setUnits(e.target.value); setErr(null); }}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void go(units); } }} />
      <button type="button" className="btn btn--sm" disabled={!units} onClick={() => void go(units)}>排</button>
      {fill !== null && fill > 0 && (
        <button type="button" className="pe-fill" title={`还差 ${left} 与这个仓可安排 ${cur?.available} 取小`}
          onClick={() => void go(String(fill))}>排 {n(fill)}</button>
      )}
      {err && <span className="pe-st__err" role="alert">{err}</span>}
      {target?.channel === 'oversea' && target.dest_wid !== null && isThirdParty(grid, target.dest_wid) && (
        <ThirdPartyHint key={`${target.dest_wid}/${sku}`} wid={target.dest_wid} sku={sku} />
      )}
    </div>
  );
}

const isThirdParty = (grid: SheetGrid, wid: number | null) =>
  wid !== null && grid.warehouses.oversea.some((w) => w.wid === wid && w.kind === 'oversea_3pl');

/** 选到第三方海外仓：当场提示须配对，并现查这个货号配没配（同提交时的判据 CB-D8）—— 别等到确认、提交才卡住 */
function ThirdPartyHint({ wid, sku }: { wid: number; sku: string }) {
  const [st, setSt] = useState<SkuMatchStatus | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  useEffect(() => {
    let live = true;
    api.checkSkuMatch(wid, sku).then((r) => { if (live) setSt(r); }, (e) => { if (live) setErr(asApiError(e)); });
    return () => { live = false; };
  }, [wid, sku]);
  return (
    <div className="ds-tp-hint" data-testid="tp-hint">
      第三方海外仓：货号须在领星配对三方 sku 后才能提交备货单。
      {err ? <span className="ds-neg"> 配对现查失败（{err.hint}），提交时还会再查</span>
        : st === null ? <span className="pt-used"> 核对中…</span>
          : !st.needs_match ? <span className="pt-used"> 这个仓没接 API，不需要配对</span>
            : st.matched ? <span className="ds-done"> ✓ 已配对（三方编码 {st.codes.join('、')}）</span>
              : <span className="ds-neg"> ✗ 未配对：先让三方仓建好这个产品，再在领星「海外仓 → 产品配对」里配上</span>}
    </div>
  );
}

// ── 右栏 · 排货单与装箱（系统派生，只读；装箱只给 FBA）──────────────

/** 两轴并排不合成（`10` 原则二，同采购单 StatusPair）：本地 | 领星 + 读回时刻 + 「重新读回」。
 *  这次没读到 → 点名并写明显示的是上次的观察；读回请求本身失败 ≠ 领星状态。 */
function ExtAxes({ ourStage, ext, rcpt, onReread }: {
  ourStage: string; ext: SheetDetail['ext_refs'][number]; rcpt: Receipt; onReread: () => void;
}) {
  const reading = rcpt.kind === 'loading';
  const mine = rcpt.kind === 'ok' ? rcpt.r.results.find((x) => x.ext_no === ext.ext_no) : undefined;
  const failed = mine && mine.outcome !== 'ok' ? mine : null;
  return (
    <span className="sp ds-axes">
      <span className="sp-axis" data-testid="axis-local"><span className="sp-k">本地</span> <span className="sp-v">{ourStage}</span></span>
      <span className="sp-axis sp-axis--lx" data-testid="axis-lx">
        <span className="sp-k">领星</span>{' '}
        {ext.erp_stage
          ? <><span className={`sp-v${ext.drift ? ' sp-v--danger' : ''}`}>{ext.erp_stage}</span> <span className="sp-at">读回于 {fmtLocal(ext.stage_observed_at)}</span></>
          : <span className="sp-v sp-v--muted">{reading ? '正在向领星查询…' : '未读回'}</span>}
        {reading && ext.erp_stage && <span className="sp-at"> 正在向领星查询…</span>}
        <button type="button" className="btn btn--sm btn--ghost" disabled={reading} onClick={onReread}>
          {reading ? '查询中…' : '重新读回'}
        </button>
      </span>
      {failed && (
        <span className="sp-err" role="alert">
          这次读回失败（{failed.outcome}{failed.lx_msg ? `：${failed.lx_msg}` : ''}）{ext.erp_stage ? '，显示的是上次读回的状态' : ''}
          {failed.cause && <Fold summary="读回失败原因">{failed.cause}</Fold>}
        </span>
      )}
      {rcpt.kind === 'fail' && (
        <span className="sp-err" role="alert">
          读回请求失败：{rcpt.err.hint}{ext.erp_stage ? '，显示的是上次读回的状态' : ''}
          <Fold summary="读回失败原因"><ErrorDetail err={rcpt.err} /></Fold>
        </span>
      )}
    </span>
  );
}

const BOX_COLS: [keyof PackingBox, string][] = [
  ['box_count', '箱数'], ['box_len', '长'], ['box_wid', '宽'], ['box_hei', '高'], ['box_weight', '单箱重 kg'], ['units_per_box', '单箱件数'],
];

function Consignments({ sheetId, grid, detail, frozen, onSaved, onFlash, rcpt, onReread }: {
  sheetId: number; grid: SheetGrid; detail: SheetDetail; frozen: boolean;
  onSaved: () => Promise<void>; onFlash: (f: Flash) => void; rcpt: Receipt; onReread: () => void;
}) {
  const live = detail.consignments.filter((c) => c.our_stage !== '已放弃' && c.our_stage !== '已取消');
  const sellerName = useMemo(() => {
    const m = new Map<string, string>();
    grid.demand.forEach((l) => l.stores.forEach((s) => m.set(s.sid, s.seller_name)));
    grid.unplanned.forEach((u) => u.stores.forEach((s) => m.set(s.sid, s.seller_name)));
    return m;
  }, [grid]);
  if (live.length === 0) return <div className="pm-empty">还没有排货单：在「安排」里填了格子，这里按目的地自动生成</div>;
  return (
    <div className="ds-cons">
      {live.map((c) => {
        const miss = missingPacking(c, detail.packing);
        return (
          <section key={c.consignment_id} className="ds-con" data-testid={`consignment-${c.consignment_id}`}>
            <div className="ds-con__head">
              <span className={`ds-dest ${c.channel === 'fba' ? 'ds-dest--fba' : 'ds-dest--os'}`}>{c.channel === 'fba' ? 'FBA' : '海外仓'}</span>
              <span className="ds-con__dest">{destLabel(grid, c).replace(/^(FBA|海外仓) /, '')}</span>
              <span className="pt-used">（系统派生）{c.idem_key}</span>
              <span className="pm-bar__sp" />
              <span><b>{n(c.lines.reduce((a, l) => a + l.units, 0))}</b> 件，{c.lines.length} 行</span>
              {c.channel === 'fba' && <span className={`chip ${miss ? 'chip--warn' : 'chip--ok'}`}>{miss ? `装箱缺 ${miss} 行` : '装箱已齐'}</span>}
              {c.our_stage !== '草稿' && <span className={`chip ${c.our_stage === '已投送' ? 'chip--ok' : ''}`}>{c.our_stage}</span>}
            </div>
            {c.channel === 'fba' && detail.ext_refs.some((e) => e.consignment_id === c.consignment_id) && (
              <div className="ds-con__lx">
                {detail.ext_refs.filter((e) => e.consignment_id === c.consignment_id).map((e) => (
                  <span key={e.ext_ref_id} className={e.voided_at ? 'is-void' : undefined} data-testid={`ext-${e.ext_no}`}>
                    领星发货计划 <code>{e.ext_no}</code>（{whName(grid, e.from_wid)}）· 计划 {(e.order_sns ?? []).join('、')}
                    {e.voided_at ? ' 已取消（需在领星手工作废）' : e.locked ? ' · 已锁库存' : <span className="ds-neg"> · 库存未锁</span>}
                    {!e.voided_at && <ExtAxes ourStage={c.our_stage} ext={e} rcpt={rcpt} onReread={onReread} />}
                    {e.drift && <span className="ds-neg" role="alert"> ⚠ {e.drift}（我方仍是已投送）</span>}
                  </span>
                ))}
              </div>
            )}
            {c.channel === 'oversea' && c.dest_wid !== null && (
              <div className="ds-con__lx">
                <LogisticsPick sheetId={sheetId} destWid={c.dest_wid} detail={detail} frozen={frozen} onSaved={onSaved} onFlash={onFlash} />
                {detail.ext_refs.filter((e) => e.consignment_id === c.consignment_id).map((e) => (
                  <span key={e.ext_ref_id} className={e.voided_at ? 'is-void' : undefined} data-testid={`ext-${e.ext_no}`}>
                    领星备货单 <code>{e.ext_no}</code>（{whName(grid, e.from_wid)}）{e.voided_at ? ' 已删除' : ''}
                    {!e.voided_at && <ExtAxes ourStage={c.our_stage} ext={e} rcpt={rcpt} onReread={onReread} />}
                    {e.drift && <span className="ds-neg" role="alert"> ⚠ {e.drift}（我方仍是已投送）</span>}
                  </span>
                ))}
              </div>
            )}
            <table className="pd-t">
              <thead><tr><th>msku</th><th>店</th><th>来源仓</th><th className="r">计划来源</th><th className="r">无计划来源</th>
                {c.channel === 'fba' && BOX_COLS.map(([, t]) => <th key={t} className="r">{t}</th>)}</tr></thead>
              <tbody>
                {c.lines.map((ln) => (
                  <tr key={`${ln.seller_sku}/${ln.sid}/${ln.from_wid}`}>
                    <td><code>{ln.seller_sku}</code></td><td>{sellerName.get(ln.sid) ?? ln.sid}</td><td>{whName(grid, ln.from_wid)}</td>
                    <td className="r">{ln.planned ? n(ln.planned) : '—'}</td><td className="r">{ln.unplanned ? n(ln.unplanned) : '—'}</td>
                    {c.channel === 'fba' && (
                      <PackingCells sheetId={sheetId} ln={ln} detail={detail} frozen={frozen} onSaved={onSaved} onFlash={onFlash} />
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        );
      })}
    </div>
  );
}

/** 海外仓排货单的物流方式（G7）：草稿可选；冻结后只显示。渠道表首次为空时后端自动从领星拉 */
function LogisticsPick({ sheetId, destWid, detail, frozen, onSaved, onFlash }: {
  sheetId: number; destWid: number; detail: SheetDetail; frozen: boolean;
  onSaved: () => Promise<void>; onFlash: (f: Flash) => void;
}) {
  const cur = detail.logistics.find((l) => l.dest_wid === destWid) ?? null;
  const [chans, setChans] = useState<LogisticsChannel[] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  useEffect(() => {
    if (frozen) return;
    let live = true;
    api.listLogisticsChannels().then((r) => { if (live) setChans(r.channels); }).catch((e) => { if (live) setErr(asApiError(e)); });
    return () => { live = false; };
  }, [frozen]);
  if (frozen) {
    return <span>物流方式 <b>{cur ? cur.channel_name : '未选'}</b>{cur && !cur.enabled && <span className="ds-neg">（已停用）</span>}</span>;
  }
  if (err) return <span className="ds-neg">物流渠道读不到：{err.hint}</span>;
  if (chans === null) return <span className="pt-used">正在取物流渠道…</span>;
  const pick = async (lid: number) => {
    try {
      const r = await api.setSheetLogistics(sheetId, destWid, lid);
      onFlash({ tone: 'ok', text: `物流方式：${r.channel_name}` });
      await onSaved();
    } catch (e) {
      const er = asApiError(e);
      onFlash({ tone: 'danger', text: er.hint, err: er });
    }
  };
  return (
    <label className={cur ? undefined : 'ds-lx-missing'}>物流方式{' '}
      {!cur && <span className="ds-neg" data-testid="logistics-missing">还没选物流方式（确认会被 G7 挡）</span>}{' '}
      <select className="ps-select" aria-label="物流方式" value={cur ? String(cur.logistics_id) : ''}
        onChange={(e) => { if (e.target.value) void pick(Number(e.target.value)); }}>
        {!cur && <option value="">（未选）</option>}
        {chans.filter((ch) => ch.enabled || ch.logistics_id === cur?.logistics_id).map((ch) => (
          <option key={ch.logistics_id} value={ch.logistics_id}>
            {/* 领星里同名渠道很多（如「美线普船 · 海卡」×4），只有物流商能分开 */}
            {[ch.channel_name, ch.method_name, ch.provider_name].filter(Boolean).join(' · ')}{ch.enabled ? '' : '（已停用）'}
          </option>
        ))}
      </select>
    </label>
  );
}

function PackingCells({ sheetId, ln, detail, frozen, onSaved, onFlash }: {
  sheetId: number; ln: ConsignmentView['lines'][number]; detail: SheetDetail; frozen: boolean;
  onSaved: () => Promise<void>; onFlash: (f: Flash) => void;
}) {
  const saved: PackingRow | undefined = detail.packing.find((p) => p.seller_sku === ln.seller_sku && p.sid === ln.sid && p.from_wid === ln.from_wid);
  const [box, setBox] = useState<Record<string, string>>(() =>
    Object.fromEntries(BOX_COLS.map(([k]) => [k, saved?.[k] == null ? '' : String(saved[k])])));
  const save = async (k: keyof PackingBox) => {
    const before = saved?.[k] == null ? '' : String(saved[k]);
    if (box[k] === before) return;
    try {
      await api.putSheetPacking(sheetId, { seller_sku: ln.seller_sku, sid: ln.sid, from_wid: ln.from_wid },
        Object.fromEntries(BOX_COLS.map(([f]) => [f, box[f] === '' ? null : Number(box[f])])) as unknown as PackingBox);
      onFlash({ tone: 'ok', text: `已保存装箱 ${ln.seller_sku}` });
      await onSaved();
    } catch (e) {
      const err = asApiError(e);
      onFlash({ tone: 'danger', text: err.hint, err });
    }
  };
  return (
    <>
      {BOX_COLS.map(([k, t]) => (
        <td key={k} className="r">
          <input className={`ds-pk${box[k] === '' ? ' is-empty' : ''}`} inputMode="decimal" disabled={frozen} value={box[k]}
            aria-label={`${t} ${ln.seller_sku}`} onChange={(e) => setBox({ ...box, [k]: e.target.value })}
            onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }} onBlur={() => void save(k)} />
        </td>
      ))}
    </>
  );
}
