# web

    npm ci                        # lockfile 已入库，复现装配用 ci 不用 install
    cp .env.example .env          # VITE_DATA_SOURCE=mock | api
    npm run dev                   # 5173
    npm test                      # vitest run
    npm run typecheck             # tsc --noEmit，静默即过
    npm run build                 # tsc -b && vite build → dist/

| 变量 | 取值 | 说明 |
|---|---|---|
| `VITE_DATA_SOURCE` | `mock` / `api` | 非法值在启动时抛错，不等第一个请求（见 `src/api/index.ts`） |
| `VITE_API_BASE` | 默认 `/v1` | 只打 `api/ui/` |
| `VITE_API_TIMEOUT_MS` | 默认 `8000` | 超时抛 `ApiError(error='timeout')`，与连不上（`error='network'`）分得开 |

## 打真接口跑（`VITE_DATA_SOURCE=api`）

三条命令，两个终端：

    uv run uvicorn --factory api:create_app --port 8091     # 仓库根目录，先起
    VITE_DATA_SOURCE=api npm run dev                        # web/，起在 5173
    # 浏览器开 http://localhost:5173

`VITE_API_BASE` 默认 `/v1` 是**相对路径** —— 它之所以能打到后端，是因为
`vite.config.ts` 里的 `server.proxy` 把 `/v1` 转发到 `127.0.0.1:8091`。
端口抄 `config.example.toml` 的 `[api] port`，别再写死第二份。

后端没起时前端报的是 `ApiError(error='bad_response')`「返回的不是 JSON」——
那是 Vite 的 SPA 回退把 `index.html` 当成了响应，不是接口本身出错。

## grid fixture 与后端同源

`src/api/fixtures/grid-1.json` 是 `tests/fixtures/grid_response.json`（仓库根目录，后端）的
逐字节副本，由 `src/api/fixtures.test.ts` 与根目录 `tests/test_layering_web.py` 两边各自盯着
（任一侧单独重新生成都会红）。后端重新固化后，从仓库根目录执行：

    UPDATE_GRID_FIXTURE=1 uv run pytest -q tests/test_grid_fixture.py
    cp tests/fixtures/grid_response.json web/src/api/fixtures/grid-1.json

两步缺一都会被上面两条测试拦下来。

## 三条门禁

- `src/e2e/sameScreen.test.tsx`（判据⑥ · 网格）：同一份 fixture 分别喂 `mock` 与 `http`
  （`fetch` 桩），展开后整屏 DOM 文本逐字比对。
- `src/e2e/sameScreenHome.test.tsx`（判据⑥ · 首页）：同上，但桩按**后端**的规则答
  （`archived` 默认排除、两个看板只数没归档的）—— 归档语义的分叉就在这三个端点上。
- `src/e2e/planFlow.test.tsx`（判据①）：建计划 → 加货品 → 填两种量 → 提交 → 铸出 rev，
  并回查 `listRevs` 确实有那一版，全程在 `mock` 上跑通。
