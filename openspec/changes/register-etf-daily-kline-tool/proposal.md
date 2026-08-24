## Why

QQ 聊天（chat 模式经 `dsh-marcus-bridge` 的 `POST /chat` 走 DSH Agent）目前没有 ETF 日线查询工具：用户问"某只 ETF 最近走势如何"时，AI 只能依赖个股实时行情（`get_stock_quote`，未必覆盖 ETF）或分钟 K 线，无法直接查看日 K 趋势。后端已存在稳定可用的 `GET /api/v1/etf/kline/{symbol}?period=day`（Tushare fund_daily，云服务器实测返回正常 OHLCV），只需在 DSH bridge 注册为原生只读工具，QQ 聊天即可直接调用。

## What Changes

- 在 `docker/dsh/bridge/lib/index.js` 注册新的 DSH 原生只读工具 **`get_etf_kline`**：参数 `symbol`（必填，支持 `510050` / `SH510050` / `SZ159995`）、`count`（可选，默认 20，返回最近 N 根日线）、`period`（可选，默认 `day`）；通过 `apiFetch('/etf/kline/' + symbol + '?period=...&count=...')` 调用现有后端接口，输出格式化为可读的日 K 摘要（日期/开高低收/涨跌幅）。
- 工具跟随现有 `registerWriteTools()` 统一注册路径（chat/trade/panel 模式共用注册；chat 模式无 restrict，注册即可见可调）。
- 在 `CHAT_SYSTEM_PROMPT` 种子（`backend/app/db/prompt_seeds.py`）补充一条 ETF 日线工具使用指引，指导 AI 在用户询问 ETF 行情/走势时调用 `get_etf_kline`（与既有黄金坑工具指引同一小节风格）。
- 部署侧：重新构建 `marcus-dsh` 镜像（`docker build -f docker/Dockerfile.dsh -t marcus-dsh:latest .`）并在云服务器重启 `marcus-dsh` 容器。

## Capabilities

### New Capabilities

- `etf-daily-kline-tool`: DSH 原生只读工具 `get_etf_kline` 的封装规范——参数定义、后端接口映射、输出渲染格式，以及各 agent 模式（chat/trade/conditions/backtest）下的可见性与权限边界。

### Modified Capabilities

（无。后端 ETF API 已是既有行为，本变更不改动其契约；仅新增 DSH 侧工具封装。）

## Impact

- **修改**：`docker/dsh/bridge/lib/index.js`（新增 `get_etf_kline` 工具定义与 execute）；`backend/app/db/prompt_seeds.py`（CHAT_SYSTEM_PROMPT 增加工具指引小节）。
- **不修改**：`backend/app/api/etf.py`（接口已存在且验证可用，不做改动）。
- **部署**：云服务器重建 `marcus-dsh` 镜像并重启容器；已有 QQ 会话（DSH agent 持久化会话）无需重置，重启后新会话即加载新工具。
- **风险**：低。纯增量只读工具注册；chat 模式本就无工具白名单限制，不影响既有写工具与做T白名单隔离（conditions/trade/backtest 的 restrict 名单按需决定是否纳入该工具）。