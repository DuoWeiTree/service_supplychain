import { useState } from 'react';
import { ApiError, skuImageUrl } from '../api/client';
import { ErrorDetail } from '../components/ErrorDetail';

/** sku-image 计划 Task 4：按需预览的图片本体——只在 `PlanSheet` 打开的 `Modal` 里挂载，
 *  `<img>` 的 `src` 永远指向本仓的 `/v1/skus/{sku}/image`（凭证不出后端，PM 的地址从不
 *  出现在浏览器能看到的任何地方）。
 *
 *  三态：加载中 → 有一句可读的状态（`role="status"`）；成功 → 显示图片；失败 → 走既有
 *  `ErrorDetail` 形状给一句人话，★ 不留浏览器默认的「破图」图标——失败时 `<img>` 本身
 *  被隐藏（`display:none`），不是让它带着裂图标继续占位。 */
export function SkuImagePreview({ sku }: { sku: string }) {
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
        onLoad={() => setState('loaded')}
        onError={() => setState('error')}
      />
    </div>
  );
}
