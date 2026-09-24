import { useState } from 'react';
import { useInFlight } from '../shell/useInFlight';
import { Fold } from '../components/Fold';
import { pushToast } from '../shell/toastStore';
import { api, type ApiError } from '../api';
import type { LeadTime } from '../api/types';
import { parseUnits } from './planGridModel';

/** 03 §41 CHECK：0~720 天 */
const MAX_DAYS = 720;
const SAVE_KEY = 'lead-time';

/** 14 B12：提前期是全局用户变量（生产天数 / 货运天数），改完由 `onChanged`
 *  重拉网格 —— 立即重算，不留旧数。
 *
 *  ★ 复核 R1：`leadTime` 由 `PlanGrid` 读（与 plan/grid/sellers 同一批
 *  `Promise.allSettled`，I6 独立读错互不连累），本组件不再自己 `getLeadTime()`；
 *  写失败也不再落一个就地的裸 `<span role="alert">`，改走 `fail`/`succeed`
 *  ——与页面其余五处写动作（提交、填销量、填采购、移出货号/msku）共用同一条
 *  ErrorDetail 通路，`err.fields` 才不会被吞掉。 */
export function LeadTimeBar({
  leadTime, onChanged, fail, succeed,
}: {
  leadTime: LeadTime;
  onChanged: () => Promise<void>;
  fail: (key: string, e: ApiError) => void;
  succeed: (key: string) => void;
}) {
  const [edit, setEdit] = useState<{ make: string; ship: string } | null>(null);
  // ★ 只装客户端校验的就地提示（非负整数 / 必填 / >720）——真的打了接口之后的
  //   成败走 fail()/pushToast，不进这个 state
  const [err, setErr] = useState<string | null>(null);
  // ★ 与网格页其余写动作共用同一道在飞护栏（useInFlight），不再各写一份 boolean
  const { pending, run } = useInFlight();

  async function save() {
    if (pending.has(SAVE_KEY)) return;
    const out: { make_days?: number; ship_days?: number } = {};
    for (const [label, raw, key] of [['生产', edit!.make, 'make_days'], ['货运', edit!.ship, 'ship_days']] as const) {
      const p = parseUnits(raw);
      if (!p.ok || p.value === null) { setErr(`${label}：${p.ok ? '必填' : p.why}`); return; }
      if (p.value > MAX_DAYS) { setErr(`${label}：不能超过 ${MAX_DAYS} 天`); return; }
      out[key] = p.value;
    }
    await run(SAVE_KEY, async () => {
      try {
        const r = await api.putLeadTime(out);
        setEdit(null); setErr(null);
        succeed(SAVE_KEY);
        // ★ 按钮动词「保存」→ toast 动词「已保存」（copy discipline：同一个动作一个名）
        pushToast({ kind: 'ok', text: `已保存提前期（生产 ${r.make_days} 天，货运 ${r.ship_days} 天）` });
        await onChanged();                      // ★ 14 B12：改完立即重算并回显
      } catch (e) {
        // ★ 与 saveDemand/savePurchase 同一条路：fail() 把 err.fields 一并交给
        //   页尾的 ErrorDetail，toast 只点名简讯，不许只留一句 hint 就把 fields 吞掉
        fail(SAVE_KEY, e as ApiError);
        pushToast({ kind: 'fail', text: (e as ApiError).hint });
      }
    });
  }

  return (
    <div className="bar" data-testid="lead-time-bar">
      {edit === null ? (
        <>
          {/* ★ 与页头元数据同一条纪律（F16）：三段各自成句，不拼 `·` 元串 */}
          <div className="bar__say" data-testid="lt-bar-meta">
            <span>生产 <b>{leadTime.make_days}</b> 天</span>{' '}
            <span>货运 <b>{leadTime.ship_days}</b> 天</span>{' '}
            <span>下单月 <b>{leadTime.anchor_day}</b> 号</span>
          </div>
          <button
            type="button" className="btn btn--sm" aria-label="改提前期"
            onClick={() => setEdit({ make: String(leadTime.make_days), ship: String(leadTime.ship_days) })}
          >
            改
          </button>
        </>
      ) : (
        <>
          <label className="bar__grp">
            <span className="bar__lbl">生产天数</span>
            <input
              className="inp inp--tiny" aria-label="生产天数" value={edit.make}
              onChange={(e) => setEdit({ ...edit, make: e.target.value })}
            />
          </label>
          <label className="bar__grp">
            <span className="bar__lbl">货运天数</span>
            <input
              className="inp inp--tiny" aria-label="货运天数" value={edit.ship}
              onChange={(e) => setEdit({ ...edit, ship: e.target.value })}
            />
          </label>
          <button
            type="button" className="btn btn--primary btn--sm" aria-label="保存提前期"
            disabled={pending.has(SAVE_KEY)} onClick={() => void save()}
          >
            保存
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setEdit(null); setErr(null); }}>取消</button>
        </>
      )}
      {err && <span role="alert" className="i-red">{err}</span>}
      <Fold summary="这是什么">全局配置：所有计划共用。下单月 15 号 + 生产天数 = 完工，再 + 货运天数 = 到货可售。</Fold>
    </div>
  );
}
