import { useEffect, useId, useRef, useState } from 'react';
import { ErrorDetail } from '../components/ErrorDetail';
import { api, ApiError } from '../api';
import type { CatalogItem } from '../api/types';

// ★ 三个数都是设计取值 · 未实测（负责人 2026-10-15 认可的设计稿，见 00 S-25b）
const DEBOUNCE_MS = 250;
const SUGGEST_LIMIT = 20;
const RECENT_LIMIT = 50;

type Panel =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'recent'; items: CatalogItem[]; unmapped: number }
  | { kind: 'hits'; items: CatalogItem[]; truncated: boolean; matched: number | undefined };

function status(item: CatalogItem, chosen: boolean): string {
  if (chosen) return '已选';
  const free = item.mskus.filter((m) => m.selectable).length;
  return free === 0 ? '已被占用' : `可认领 ${free}/${item.mskus.length}`;
}

/** 搜货号的输入框 + 下拉。空输入列「以前计划里用过的」，有输入列命中的货号。 */
export function SkuPicker({ chosen, onPick }: {
  chosen: ReadonlySet<string>;
  onPick: (item: CatalogItem) => void;
}) {
  const listId = useId();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState<Panel>({ kind: 'idle' });
  const [active, setActive] = useState(-1);
  const [err, setErr] = useState<ApiError | null>(null);
  // ★ 每次输入都发一次请求，回来的顺序不保证 —— 只认最后发出的那一个
  const seq = useRef(0);

  useEffect(() => {
    if (!open) return;
    const term = q.trim();
    const mine = ++seq.current;
    // ★ 输入一变旧结果就作废：留着它，↓ + Enter 会选中一个不匹配当前输入的货号
    setPanel({ kind: 'loading' });
    setActive(-1);
    const fetch = async () => {
      try {
        const next: Panel = term === ''
          ? await api.recentCatalog({ limit: RECENT_LIMIT }).then((r) => (
              { kind: 'recent' as const, items: r.items, unmapped: r.unmapped_claims }))
          : await api.searchCatalog({ q: term, limit: SUGGEST_LIMIT }).then((r) => (
              { kind: 'hits' as const, items: r.items, truncated: r.truncated, matched: r.matched }));
        if (mine !== seq.current) return;
        setPanel(next);
        setActive(-1);
        setErr(null);
      } catch (e) {
        if (mine !== seq.current) return;
        setPanel({ kind: 'idle' });
        setErr(e as ApiError);
      }
    };
    // 空输入不等：聚焦就该看到列表
    const t = setTimeout(() => void fetch(), term === '' ? 0 : DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q, open]);

  const items = panel.kind === 'recent' || panel.kind === 'hits' ? panel.items : [];

  function pick(item: CatalogItem) {
    onPick(item);
    setQ('');
    setOpen(false);
    setPanel({ kind: 'idle' });
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      setActive((i) => Math.min(i + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (open && active >= 0 && items[active]) pick(items[active]);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  }

  const label = panel.kind === 'recent' ? '以前计划里用过的' : '命中的货号';

  return (
    <div className="skp">
      <label className="bar__grp"><span className="bar__lbl">搜货号</span>
        <input
          className="inp inp--text"
          role="combobox"
          aria-label="搜货号"
          aria-expanded={open && items.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          placeholder="货号、品名或 msku"
          autoComplete="off"
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKey}
        />
      </label>

      {open && panel.kind !== 'idle' && (
        // ★ mousedown 先于 blur：不拦住它，点选项之前下拉就因失焦关掉了
        <div className="skp__panel" onMouseDown={(e) => e.preventDefault()}>
          {panel.kind === 'loading' && <div className="skp__note">正在取…</div>}
          {panel.kind === 'recent' && <div className="skp__head">以前计划里用过的</div>}
          {panel.kind === 'recent' && panel.items.length === 0 && (
            <div className="skp__note" data-testid="recent-empty">还没有计划用过的货号，输入货号、品名或 msku 搜索</div>
          )}
          {panel.kind === 'hits' && panel.items.length === 0 && (
            <div className="skp__note" data-testid="no-hit">没有命中的货号</div>
          )}
          {/* 放在列表上方：列表可滚，放底下要滚到底才看得见 */}
          {panel.kind === 'hits' && panel.truncated && (
            <div className="skp__note" data-testid="suggest-truncated">
              命中{panel.matched === undefined ? '' : ` ${panel.matched} 个`}，只列前 {SUGGEST_LIMIT}，继续输入缩小范围
            </div>
          )}
          {items.length > 0 && (
            <ul className="skp__list" role="listbox" id={listId} aria-label={label}>
              {items.map((item, i) => {
                const st = status(item, chosen.has(item.sku));
                return (
                  <li
                    key={item.sku}
                    id={`${listId}-${i}`}
                    role="option"
                    aria-selected={i === active}
                    className={`skp__opt${i === active ? ' is-active' : ''}${st === '已被占用' || st === '已选' ? ' is-dim' : ''}`}
                    onClick={() => pick(item)}
                    onMouseEnter={() => setActive(i)}
                  >
                    <span className="skp__sku">{item.sku}</span>
                    <span className="skp__name">{item.name}</span>
                    <span className="skp__st">{st}</span>
                  </li>
                );
              })}
            </ul>
          )}
          {panel.kind === 'recent' && panel.unmapped > 0 && (
            <div className="skp__note" data-testid="recent-unmapped">
              另有 {panel.unmapped} 条认领记录对不上货号（msku 已不在镜像里）
            </div>
          )}
        </div>
      )}

      {err && <ErrorDetail err={err} />}
    </div>
  );
}
