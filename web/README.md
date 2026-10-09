# web

    npm ci                        # lockfile 已入库，复现装配用 ci 不用 install
    npm run dev                   # 5173；★ 先起后端，见下
    npm test                      # vitest run
    npm run typecheck             # tsc --noEmit，静默即过
    npm run build                 # tsc -b && vite build → dist/

前端只有一种跑法：打真后端（PB-D17，没有 mock 档）。

| 变量 | 取值 | 说明 |
|---|---|---|
| `VITE_DATA_SOURCE` | ★ 已废除 | 只要设了（哪怕是 `api`），前端启动即抛错 |
| `VITE_API_BASE` | 默认 `/v1` | 只打 `api/ui/` |
| `VITE_API_TIMEOUT_MS` | 默认 `8000` | 超时抛 `ApiError(error='timeout')`，与连不上（`error='network'`）分得开 |

采购页入口 `/purchase`（首页）· `/purchase/new` · `/purchase/:poId`，页面在 `src/pages/purchase/`，路径语义见 `docs/10` §2.0。

## 起服务

两个终端：

    uv run uvicorn --factory api:create_app --port 8091     # 仓库根目录，先起
    npm run dev                                             # web/，起在 5173
    # 浏览器开 http://localhost:5173

`VITE_API_BASE` 默认 `/v1` 是**相对路径** —— 它之所以能打到后端，是因为
`vite.config.ts` 里的 `server.proxy` 把 `/v1` 转发到 `127.0.0.1:8091`。
端口抄 `config.example.toml` 的 `[api] port`，别再写死第二份。

后端没起时，代理回 502 `backend_unreachable`，页面显示「连不上后端 http://127.0.0.1:8091（ECONNREFUSED）」，
起 vite 的终端同时打一行 `[proxy] … cause_code=…`（`vite.config.ts` 的 `configure`）。

## 测试替身与 grid fixture

替身只在 `src/testing/`（内存后端 `memoryBackend.ts`、`fixtures/`），产品源码 import 不到。
`src/testing/fixtures/grid-1.json` 是 `tests/fixtures/grid_response.json`（仓库根目录，后端）的
逐字节副本，由 `src/testing/fixtures.test.ts` 与根目录 `tests/test_layering_web.py` 两边各自盯着
（任一侧单独重新生成都会红）。后端重新固化后，从仓库根目录执行：

    UPDATE_GRID_FIXTURE=1 uv run pytest -q tests/test_grid_fixture.py
    cp tests/fixtures/grid_response.json web/src/testing/fixtures/grid-1.json

两步缺一都会被上面两条测试拦下来。

## 三条门禁

- `src/e2e/sameScreen.test.tsx`（判据⑥ · 网格）：同一份 fixture 分别喂测试内存后端（`src/testing/`）与 `http`
  （`fetch` 桩），展开后整屏 DOM 文本逐字比对。
- `src/e2e/sameScreenHome.test.tsx`（判据⑥ · 首页）：同上，但桩按**后端**的规则答
  （`archived` 默认排除、两个看板只数没归档的）—— 归档语义的分叉就在这三个端点上。
- `src/e2e/planFlow.test.tsx`（判据①）：建计划 → 加货品 → 填两种量 → 提交 → 铸出 rev，
  并回查 `listRevs` 确实有那一版，全程在测试内存后端上跑通。
