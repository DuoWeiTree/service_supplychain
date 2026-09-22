# web

    npm install
    cp .env.example .env          # VITE_DATA_SOURCE=mock | api
    npm run dev                   # 5173
    npm test                      # vitest run
    npm run typecheck             # tsc --noEmit，静默即过
    npm run build                 # tsc -b && vite build → dist/，由 FastAPI StaticFiles 托管

| 变量 | 取值 | 说明 |
|---|---|---|
| `VITE_DATA_SOURCE` | `mock` / `api` | 非法值在启动时抛错，不等第一个请求（见 `src/api/index.ts`） |
| `VITE_API_BASE` | 默认 `/v1` | 只打 `api/ui/` |
| `VITE_API_TIMEOUT_MS` | 默认 `8000` | 超时抛 `ApiError(error='timeout')`，与连不上（`error='network'`）分得开 |

## grid fixture 与后端同源

`src/api/fixtures/grid-1.json` 是 `tests/fixtures/grid_response.json`（仓库根目录，后端）的
逐字节副本，由 `src/api/fixtures.test.ts` 与根目录 `tests/test_layering_web.py` 两边各自盯着
（任一侧单独重新生成都会红）。后端重新固化后，从仓库根目录执行：

    UPDATE_GRID_FIXTURE=1 uv run pytest -q tests/test_grid_fixture.py
    cp tests/fixtures/grid_response.json web/src/api/fixtures/grid-1.json

两步缺一都会被上面两条测试拦下来。

## 两条门禁

- `src/e2e/sameScreen.test.tsx`（判据⑥）：同一份 fixture 分别喂 `mock` 与 `http`（`fetch` 桩），
  展开后整屏 DOM 文本逐字比对。
- `src/e2e/planFlow.test.tsx`（判据①）：建计划 → 加货品 → 填两种量 → 提交 → 铸出 rev，全程在 `mock` 上跑通。
