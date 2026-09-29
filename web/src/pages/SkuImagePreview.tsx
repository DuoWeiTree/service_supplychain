import { useState } from 'react';
import { ApiError, skuImageUrl } from '../api/client';
import { ErrorDetail } from '../components/ErrorDetail';

/** sku-image 计划 Task 4：按需预览的图片本体——只在 `PlanSheet` 打开的 `Modal` 里挂载，
 *  `<img>` 的 `src` 永远指向本仓的 `/v1/skus/{sku}/image`（凭证不出后端，PM 的地址从不
 *  出现在浏览器能看到的任何地方）。
 *
 *  三态：加载中 → 有一句可读的状态（`role="status"`）——★ 这条不是走过场：负责人实测
 *  PM 给的是原图，见过 7 MB 的真实主图（没有缩略图接口，也不该自己发明一份压缩/缩略图
 *  去凑），内网也有可感知的等待，没有状态运营会以为点了没反应而连点；成功 → 显示图片
 *  （CSS 限死显示尺寸，见 app.css .sku-image-preview img，不让 7 MB 原图撑爆弹层）；
 *  失败 → 走既有 `ErrorDetail` 形状给一句人话，★ 不留浏览器默认的「破图」图标——失败时
 *  `<img>` 本身被隐藏（`display:none`），不是让它带着裂图标继续占位。
 *
 *  `onSettled`：加载落地（成功或失败）时回调一次，供 `PlanSheet` 的 `useInFlight`
 *  在飞护栏放行下一次——护栏本身不在这个组件里，这里只负责如实报告"结束了"。 */
export function SkuImagePreview({ sku, onSettled }: { sku: string; onSettled: () => void }) {
  const [state, setState] = useState<'loading' | 'loaded' | 'error'>('loading');
  return (
    <div className="sku-image-preview">
      {state === 'loading' && <div role="status">加载中…</div>}
      {state === 'error' && (
        <ErrorDetail err={new ApiError(0, 'image_load_failed',
          '图片加载失败——可能是网络问题，或这张主图已经不在了', { sku })} />
      )}
      <img
        src={skuImageUrl(sku)}
        alt={`${sku} 主图`}
        style={{ display: state === 'loaded' ? 'block' : 'none' }}
        onLoad={() => { setState('loaded'); onSettled(); }}
        onError={() => { setState('error'); onSettled(); }}
      />
    </div>
  );
}
