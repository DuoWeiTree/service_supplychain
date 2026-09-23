import { useState } from 'react';
import { pushToast } from '../shell/toastStore';
import { useInFlight } from '../shell/useInFlight';
import { api, ApiError } from '../api';
import type { PlanId, Period, Sid } from '../api/types';
import { parseUnits, type SkuBlock } from './planGridModel';

/** ★ 网格页的保存类动作（期望销量 / 计划采购量 / 移出货号 / 移出 msku）从 PlanGrid.tsx
 *  抽到这里——行为与测试断言不变（Ruling B），只是搬家。
 *  `load` 由调用方传入并在每次保存后触发重载；`fail`/`succeed` 把失败挂到页面底部的
 *  ErrorDetail 上，并在**同一个动作**下次成功时把它收掉（不是任何一次 load 都清）。 */

/** ★ 在飞 key 的拼法集中在这四个函数里 —— 按钮的 `disabled` 与早退判据必须读同一个 key，
 *  各自拼一遍就会出现「置灰了但没挡住」或者反过来。 */
export const demandKey = (sellerSku: string, sid: Sid, period: Period) => `d:${sellerSku}:${sid}:${period}`;
export const purchaseKey = (sku: string, period: Period) => `p:${sku}:${period}`;
export const skuKey = (block: SkuBlock) => `sku:${block.sid}:${block.sku}`;
export const mskuKey = (sellerSku: string, sid: Sid) => `msku:${sellerSku}:${sid}`;

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

  // ★ F1/F2 裁定：期望销量与计划采购量共用同一套解析（`parseUnits`）——
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

  // ★ F4/F5 裁定：try/catch → ErrorDetail + reload；逐个释放、遇错即停，
  //   toast 点名已释放的与失败的那一个；搁浅的货号级采购格单独报一句，不许吞掉
  async function removeSku(block: SkuBlock) {
    const key = skuKey(block);
    await run(key, async () => {
      const released: string[] = [];
      let dropped = 0;
      let stranded = 0;
      for (const row of block.mskus) {
        try {
          const r = await api.releaseClaim(planId, row.seller_sku, row.sid);
          released.push(row.seller_sku);
          dropped += r.dropped_cells.length;
          stranded += r.stranded_purchase_cells.length;
        } catch (e) {
          await load();
          fail(key, e as ApiError);
          const releasedText = released.length > 0 ? released.join('、') : '无';
          pushToast({ kind: 'fail', text: `${block.sku} 移出中断：已释放 ${releasedText}，${row.seller_sku} 释放失败` });
          return;
        }
      }
      await load();
      succeed(key);
      let text = `已移出 ${released.length} 个 msku，丢弃 ${dropped} 格`;
      if (stranded > 0) text += `。货号级采购格 ${stranded} 个未删、待处理`;
      pushToast({ kind: 'ok', text });
    });
  }

  async function removeMsku(sellerSku: string, sid: Sid) {
    const key = mskuKey(sellerSku, sid);
    await run(key, async () => {
      try {
        const { dropped_cells, stranded_purchase_cells } = await api.releaseClaim(planId, sellerSku, sid);
        await load();
        succeed(key);
        let text = `${sellerSku} 已移出，丢弃 ${dropped_cells.length} 格`;
        if (stranded_purchase_cells.length > 0) text += `。货号级采购格 ${stranded_purchase_cells.length} 个未删、待处理`;
        pushToast({ kind: 'ok', text });
      } catch (e) {
        await load();
        fail(key, e as ApiError);
        pushToast({ kind: 'fail', text: `${sellerSku} 移出失败` });
      }
    });
  }

  return { pending, inputErr, saveDemand, savePurchase, removeSku, removeMsku };
}
