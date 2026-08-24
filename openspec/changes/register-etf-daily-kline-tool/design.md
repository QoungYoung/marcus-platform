## Context

现状（见 proposal.md - Why）：QQ 聊天走 `dsh-marcus-bridge`（`docker/dsh/bridge/lib/index.js`，marcus-dsh 容器内 DSH 插件）的 `POST /chat` → DSH Agent；工具在该插件 `registerWriteTools()` 中经 `defineTool` + `tools.register` 统一注册（`inject: ["webServer","agents","tools"]`）。只读查询工具已有成熟的先例簇（`get_stock_quote` / `get_intraday_minute` / `get_stock_technical` 等，均以 `apiFetch(MARCUS_API + path)` 调后端 `/api/v1/*` 并渲染为文本）。chat 模式会话**无 tools.restrict 白名单**，注册即对所有 QQ 会话可见；conditions/trade/backtest 模式的 restrict 名单按需决定是否纳入新工具。

后端接口 `GET /api/v1/etf/kline/{symbol}`（`backend/app/api/etf.py:323`）已验证可用：`period=day` 走 Tushare `fund_daily`，自动处理 `510050`→`510050.SH`、`159995`→`159995.SZ` 的符号标准化，返回 `{timestamp, open, high, low, close, volume, amount, change_pct}` 数组，`count` 为负时取最近 N 条（默认 284）。

## Goals / Non-Goals

**Goals:**
- 在 bridge 插件注册 `get_etf_kline` 只读工具，QQ 聊天可查询任意 ETF 日线。
- 输出格式与其他只读工具（如 `get_intraday_minute`）风格一致，紧凑可读。
- 在 `CHAT_SYSTEM_PROMPT` 种子补充工具指引，提高 AI 调用命中率。
- 提供云服务器重建 `marcus-dsh` 镜像并重启容器的部署步骤。

**Non-Goals:**
- 不改动 `backend/app/api/etf.py`（接口契约不变，直接复用）。
- 不新增后端接口、不改数据库结构。
- 不做 ETF 周/月 K 的独立封装（后端 `period` 参数已透传支持，工具可不暴露或仅暴露 `day`，由实现决定，见 Open Questions）。
- 不为该工具单独引入缓存/限流（沿用后端既有行为）。

## Decisions

**D1: 工具注册位置 —— 跟随现有只读工具簇，加入 `registerWriteTools()`**
工具定义放在 `// ═══ 只读行情/持仓/指标查询工具 ═══` 区块（`get_stock_quote` 附近），与其他 8 个只读工具并列；注册后 console 日志行同步补充工具名。
- 备选：单独建 `registerReadTools()` 函数拆分注册。未采用：现有 8 个只读工具未拆分，保持同构最小改动。

**D2: 工具名与参数 —— `get_etf_kline`，`symbol` 必填 + `count`(默认 20) + `period`(默认 day)**
- 命名跟随只读簇 `get_*` 前缀；参数与既有规范一致（`symbol` 必填，支持 `510050`/`SH510050`/`SZ159995` 格式透传后端）。
- `count` 默认 20（聊天场景一次看 20 根足够），上限不强制（后端 `-284` 兜底截断）。
- 其余只读工具均为单个必填 `symbol`，本工具新增可选 `count`/`period` 两个辅助参数，`parameters` 用 `additionalProperties: false` 严格校验（与现有工具一致）。

**D3: execute 实现 —— `apiFetch('/etf/kline/' + encodeURIComponent(symbol) + '?period=' + period + '&count=' + count)`**
- 直接拼 query 并透传；`period` 默认 `day`。渲染参考 `get_intraday_minute`：输出首行含标的与条数，随后每行一根 K 线 `日期 O.. H.. L.. C.. 涨跌%`，末尾一行统计（区间涨幅/最新收盘等，可选）。
- 错误处理：`apiFetch` 抛错时工具返回 `{ ok: false, text: '错误信息' }`，AI 如实转述；`klines` 为空时返回"该代码无日线数据"。

**D4: 提示词指引 —— 在 `CHAT_SYSTEM_PROMPT` 种子新增「ETF 日线工具」小节**
位置：`backend/app/db/prompt_seeds.py` 的 `CHAT_SYSTEM_PROMPT` 中，现有「黄金坑工具」小节之后（约 line 83 附近）。内容：用户询问 ETF 日 K 走势/趋势/涨跌时优先调用 `get_etf_kline`（参数示例：`symbol=SH510050, count=60`）。注意 DB 中已有此 prompt 的运行副本时，种子只在空库写入——部署时需在 API 侧更新该 prompt 或接受种子不覆盖现有数据（见 D5/R1）。

**D5: 部署 —— 重构建像 + 重启容器**
`docker build -f docker/Dockerfile.dsh -t marcus-dsh:latest .`（Dockerfile 第 61-62 行 COPY bridge 的 package.json 与 `lib/index.js`），然后停旧容器、rm、run（或 `docker compose up -d dsh`）。已有持久化 DSH 会话无需重置，重启后新会话自动加载新工具。提示词若需生效且 DB 已有副本，需额外 POST 更新 `/api/v1/prompts` 中 CHAT_SYSTEM_PROMPT（或删种子让其重播种）。

## Risks / Trade-offs

- [R1: 种子 prompt 不覆盖线上已有副本] → 部署步骤中明确：如线上 DB 的 CHAT_SYSTEM_PROMPT 已存在（seed 仅在空库写入），需通过管理端/API 更新该 prompt，或接受提示词指引不上线（工具本身仍可被 AI 凭描述调用）。→ Mitigation: 在 tasks/部署说明中列出两条路径。
- [R2: 腾讯/Tushare 限流] → ETF 日线走 Tushare `fund_daily`，高频聊天调用可能触发限流 → 依赖后端既有的 `time.sleep(0.6)` 节奏与 `_fetch_etf_kline_from_tushare` 的降级（沪→深重试）；非本变更引入，不额外处理。
- [R3: AI 误用工具（拿 ETF 代码查个股接口或反之）] → 工具 description 写清"仅 ETF 代码"，提示词指引补充说明与 `get_daily_kline`（个股）的区分。
- [R4: 会话白名单遗漏] → conditions 模式白名单（8 查询工具）与 trade/t 会话白名单若不加入 `get_etf_kline`，这些会话中该工具不可见；chat（QQ 主场景）不受影响。决策：本期保留 chat 可见即可，其他模式是否纳入由后续需求决定（记入 Open Questions，不阻塞）。

## Migration Plan

1. 修改 `docker/dsh/bridge/lib/index.js`：新增 `get_etf_kline` 工具定义与 execute，更新注册完成日志。
2. 修改 `backend/app/db/prompt_seeds.py`：CHAT_SYSTEM_PROMPT 增加 ETF 日线工具指引小节。
3. 本地验证：`node --check docker/dsh/bridge/lib/index.js`（语法）；可在本机临时 profile 加载 bridge 插件跑一次 `/chat` 冒烟（可选）。
4. 云服务器：`docker build -f docker/Dockerfile.dsh -t marcus-dsh:latest .` → 重启 `marcus-dsh` 容器 → `docker logs` 确认 `[Bridge] 只读查询工具注册完成（…get_etf_kline…）`。
5. 冒烟：`curl -X POST http://<host>:3001/chat -d '{"message":"查一下 SH510050 最近 5 天日线","session_id":"smoke-etf-1"}'` 确认 AI 调用新工具并返回数据。
6. 回滚：`git revert` 对应文件 → 重构建像重启即可；工具为新注册，无数据迁移。

## Open Questions

- Q1: `period` 参数是否在工具中暴露非 `day` 周期（周/月由后端走雪球源）？当前默认仅暴露 `day` 即可满足需求，扩展成本低，留给后续。
- Q2: conditions/trade 等 restrict 白名单会话是否也纳入 `get_etf_kline`？本期非必须（QQ chat 主场景已覆盖），等实际使用反馈再定。