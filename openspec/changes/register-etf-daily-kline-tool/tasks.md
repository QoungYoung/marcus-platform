## 1. Bridge 插件工具注册

- [x] 1.1 在 `docker/dsh/bridge/lib/index.js` 的只读查询工具区块（`get_stock_quote` 附近）新增 `get_etf_kline` 工具定义：`name` / `description`（注明仅 ETF 代码、支持 510050/SH510050/SZ159995、走 Tushare fund_daily 日线）/ `parameters`（`symbol` 必填，`count` 默认 20，`period` 默认 day，`additionalProperties: false`）
- [x] 1.2 实现 `execute`：`apiFetch('/etf/kline/' + encodeURIComponent(symbol) + '?period=' + period + '&count=' + count)`，将返回的 `klines` 渲染为多行文本（首行标的与条数，每行一根 K 线：日期 O.. H.. L.. C.. 涨跌幅，可含成交额），风格对齐 `get_intraday_minute`
- [x] 1.3 空 K 线/异常分支：`klines` 为空返回「该代码无日线数据」；`apiFetch` 抛错返回 `{ ok: false, text: 错误信息 }`，均不编造数据
- [x] 1.4 更新注册完成日志行（`console.log('[Bridge] 只读查询工具注册完成（…get_etf_kline…）')`）
- [x] 1.5 语法校验：`node --check docker/dsh/bridge/lib/index.js` 通过

## 2. 系统提示词指引

- [x] 2.1 在 `backend/app/db/prompt_seeds.py` 的 `CHAT_SYSTEM_PROMPT` 中「黄金坑工具」小节后新增「ETF 日线工具」小节：说明用户询问 ETF 日 K 走势/趋势/涨跌时优先调用 `get_etf_kline`（示例参数 `symbol=SH510050, count=60`），并注明与个股 `get_daily_kline` 工具的区分
- [x] 2.2 核对提示词种子语法（Python 文件可正常 import 解析）

## 3. 部署与冒烟验证

- [x] 3.1 云服务器重建镜像：`docker build -f docker/Dockerfile.dsh -t marcus-dsh:latest .`
- [x] 3.2 重启 `marcus-dsh` 容器（沿用原 `docker run` 参数/env 或 `docker compose up -d dsh`）
- [x] 3.3 确认 `docker logs marcus-dsh` 出现「只读查询工具注册完成（…get_etf_kline…）」
- [x] 3.4 QQ 链路冒烟：`curl -X POST http://<host>:3001/chat -d '{"message":"查一下 SH510050 最近 5 天日线","session_id":"smoke-etf-1","mode":"chat"}'`，确认回复包含真实日线数据且未走写工具
- [x] 3.5 若线上 DB 中 `CHAT_SYSTEM_PROMPT` 已存在（种子仅空库写入），通过 API/管理端更新该 prompt 使工具指引生效，或记录说明接受延迟
- [x] 3.6 提交变更（`docker/dsh/bridge/lib/index.js`、`backend/app/db/prompt_seeds.py`），中文 commit message

## 4. 验收对照

- [x] 4.1 对照 `specs/etf-daily-kline-tool/spec.md` 逐条核对：工具注册、输出格式、chat 可见性、提示词指引均满足
- [x] 4.2 QQ 实际聊天会话验证一次 ETF 查询（含无前缀/带前缀代码各一例）