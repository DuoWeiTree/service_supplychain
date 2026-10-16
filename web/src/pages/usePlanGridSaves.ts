import { useState } from 'react';
import { pushToast } from '../shell/toastStore';
import { useInFlight } from '../shell/useInFlight';
import { api, ApiError } from '../api';
import type { DroppedPurchaseCell, PlanId, Period, Sid } from '../api/types';
import { parseUnits, type SkuBlock } from './planGridModel';

/** ★ 网格页的保存类动作（计划销量 / 计划采购量 / 移出货号 / 移出该店 / 移出 msku）从 PlanGrid.tsx
 *  抽到这里——行为与测试断言不变（Ruling B），只是搬家。
 *  `load` 由调用方传入并在每次保存后触发重载；`fail`/`succeed` 把失败挂到页面底部的
 *  ErrorDetail 上，并在**同一个动作**下次成功时把它收掉（不是任何一次 load 都清）。 */

/** ★ 在飞 key 的拼法集中在这几个函数里 —— 按钮的 `disabled` 与早退判据必须读同一个 key，
 *  各自拼一遍就会出现「置灰了但没挡住」或者反过来。 */
export const demandKey = (sellerSku: string, sid: Sid, period: Period) => `d:${sellerSku}:${sid}:${period}`;
export const purchaseKey = (sku: string, period: Period) => `p:${sku}:${period}`;
export const storeKey = (block: SkuBlock) => `store:${block.sid}:${block.sku}`;
export const skuKey = (sku: string) => `sku:${sku}`;
export const mskuKey = (sellerSku: string, sid: Sid) => `msku:${sellerSku}:${sid}`;
/** ★ 同一货号的三种移出（货号 / 该店 / msku）共用这一把在飞 key：它们改的是同一批行，
 *  界面上同时发两个，后端虽然按 (计划, 货号) 排队不会坏，但第二个拿到的是第一个删剩的，
 *  toast 会说一件已经不成立的事。上面三个 key 仍各管各的失败提示（fail/succeed）。 */
export const removeKey = (sku: string) => `rm:${sku}`;

/** ★ 丢东西必须有声：格数全报（空格也是删掉的一行），人填过的逐格带值点名 */
function droppedDemand(cells: { period: Period; expected_units: number | null; seller_sku?: string }[]): string {
  const named = cells.filter((c) => c.expected_units !== null)
    .map((c) => `${c.seller_sku ? `${c.seller_sku} ` : ''}${c.period} ${c.expected_units} 件`);
  return `丢弃计划销量 ${cells.length} 格${named.length > 0 ? `（${named.join('、')}）` : ''}`;
}
function droppedPurchase(cells: DroppedPurchaseCell[]): string {
  const named = cells.filter((c) => c.planned_units !== null).map((c) => `${c.period} ${c.planned_units} 件`);
  return `丢弃计划采购 ${cells.length} 格${named.length > 0 ? `（${named.join('、')}）` : ''}`;
}
/** 业主裁定 2026-09-27：货号的最后一个 msku 走了，货号连同采购格一起走 —— 这件事单独说一句 */
function skuGone(cells: DroppedPurchaseCell[]): string {
  return cells.length === 0 ? '' : `。货号 ${cells[0]!.sku} 已无 msku，一并移出：${droppedPurchase(cells)}`;
}

export function usePlanGridSaves(
  planId: PlanId,
  load: () => Promise<void>,
  fail: (key: string, e: ApiError) => void,
  succeed: (key: string) => void,
) {
  // ★ F3 裁定：PUT 在飞时置灰、忽略第二次 blur，避免先回来的 load() 覆盖后回来的那次。
  //   I3 裁定：六处写动作共用 useInFlight，不再各写一份 Set/boolean。
  const { pending, run } = useInFlight();
  // ★ F1 裁定：校验不过时就地给一条具名的行内提示，不是 toast —— toast 一闪就没了，
  //   这种「你刚填的这一格」的反馈需要留在原地
  const [inputErr, setInputErr] = useState<Record<string, string>>({});

  function clearErr(key: string) {
    setInputErr((m) => { if (!(key in m)) return m; const n = { ...m }; delete n[key]; return n; });
  }

  // ★ F1/F2 裁定：计划销量与计划采购量共用同一套解析（`parseUnits`）——
  //   空 = 未知 ⇒ null；非负整数才收；`5oo`/负数/小数一律拒收，且**不发请求**。
  async function saveDemand(sellerSku: string, sid: Sid, period: Period, raw: string) {
    const key = demandKey(sellerSku, sid, period);
    if (pending.has(key)) return;                  // ★ 校验之前先看在飞：在飞时连解析都不用做
    const parsed = parseUnits(raw);
    if (!parsed.ok) { setInputErr((m) => ({ ...m, [key]: parsed.why })); return; }
    clearErr(key);
    await run(key, async () => {
      try { await api.putDemand(planId, sellerSku, sid, period, parsed.value); await load(); succeed(key); }
      catch (e) { fail(key, e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).hint }); }
    });
  }

  async function savePurchase(sku: string, period: Period, raw: string) {
    const key = purchaseKey(sku, period);
    if (pending.has(key)) return;
    const parsed = parseUnits(raw);
    if (!parsed.ok) { setInputErr((m) => ({ ...m, [key]: parsed.why })); return; }
    clearErr(key);
    await run(key, async () => {
      try { await api.putPurchase(planId, sku, period, parsed.value); await load(); succeed(key); }
      catch (e) { fail(key, e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).hint }); }
    });
  }

  // ★ F4 裁定：try/catch → ErrorDetail + reload；逐个释放、遇错即停，toast 点名已释放的与失败的那一个。
  //   ★ F5（搁浅采购格未删、待处理）已被业主裁定 2026-09-27 推翻：最后一个 msku 走了，采购格随之删掉并点名
  async function removeStore(block: SkuBlock) {
    const key = storeKey(block);
    await run(removeKey(block.sku), async () => {
      const released: string[] = [];
      const dropped: { seller_sku: string; period: Period; expected_units: number | null }[] = [];
      const droppedBuy: DroppedPurchaseCell[] = [];
      for (const row of block.mskus) {
        try {
          const r = await api.releaseClaim(planId, row.seller_sku, row.sid);
          released.push(row.seller_sku);
          dropped.push(...r.dropped_cells.map((c) => ({ ...c, seller_sku: row.seller_sku })));
          droppedBuy.push(...r.dropped_purchase_cells);
        } catch (e) {
          await load();
          fail(key, e as ApiError);
          const releasedText = released.length > 0 ? released.join('、') : '无';
          pushToast({ kind: 'fail', text: `${block.seller_name} 的 ${block.sku} 移出该店中断：已释放 ${releasedText}，${row.seller_sku} 释放失败` });
          return;
        }
      }
      await load();
      succeed(key);
      pushToast({ kind: 'ok',
        text: `已移出该店 ${block.seller_name} 的 ${block.sku}：msku ${released.length} 个，${droppedDemand(dropped)}${skuGone(droppedBuy)}` });
    });
  }

  // ★ 业主裁定 2026-09-27：「直接删除货号与货号内对应的 msku」—— 一个调用一个事务（后端 remove_sku），
  //   不在前端逐个释放：中途失败会留下半个货号
  async function removeSku(sku: string) {
    const key = skuKey(sku);
    await run(removeKey(sku), async () => {
      try {
        const r = await api.removeSku(planId, sku);
        await load();
        succeed(key);
        pushToast({ kind: 'ok',
          text: `已移出货号 ${sku}：msku ${r.released.length} 个，${droppedDemand(r.dropped_cells)}，${droppedPurchase(r.dropped_purchase_cells)}` });
      } catch (e) {
        await load();
        fail(key, e as ApiError);
        pushToast({ kind: 'fail', text: `${sku} 移出失败` });
      }
    });
  }

  async function removeMsku(sellerSku: string, sid: Sid, sku: string) {
    const key = mskuKey(sellerSku, sid);
    await run(removeKey(sku), async () => {
      try {
        const { dropped_cells, dropped_purchase_cells } = await api.releaseClaim(planId, sellerSku, sid);
        await load();
        succeed(key);
        pushToast({ kind: 'ok',
          text: `已移出 msku ${sellerSku}：${droppedDemand(dropped_cells)}${skuGone(dropped_purchase_cells)}` });
      } catch (e) {
        await load();
        fail(key, e as ApiError);
        pushToast({ kind: 'fail', text: `${sellerSku} 移出失败` });
      }
    });
  }

  return { pending, inputErr, saveDemand, savePurchase, removeSku, removeStore, removeMsku };
}
