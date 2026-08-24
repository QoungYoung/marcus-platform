## Purpose

定义 DSH 模块（marcus-dsh 容器，替代 Pi Server 的 Agent 桥接服务）中 ETF 日线查询工具 `get_etf_kline` 的封装规范，使 QQ 聊天（chat 模式）可以直接查询任意 ETF 的日 K 线数据，支撑走势分析与趋势判断。

## ADDED Requirements

### Requirement: ETF 日线查询工具
DSH 模块 SHALL 注册只读工具 `get_etf_kline`，参数为 `symbol`（必填，ETF 代码，支持 `510050`/`SH510050`/`SZ159995` 格式）、`count`（可选，返回最近 N 根日线，默认 20，最大 284）、`period`（可选，K 线周期，默认 `day`）；工具 SHALL 调用后端 `GET /api/v1/etf/kline/{symbol}`（period=day 时走 Tushare fund_daily），并将返回的 K 线序列渲染为可读文本。

#### Scenario: 查询 ETF 日线
- **WHEN** 用户在聊天中询问"510050 最近走势如何"或给出任意 ETF 代码
- **THEN** AI 调用 `get_etf_kline` 传入该代码，工具返回最近 N 根日线（日期/开高低收/成交量/成交额/涨跌幅），AI 基于数据回答

#### Scenario: 指定返回条数
- **WHEN** 用户要求"看近 60 天日线"或 AI 判断需要更多数据
- **THEN** AI 传入 `count=60` 调用工具，工具返回对应数量的日线数据

#### Scenario: 代码格式兼容
- **WHEN** 用户传入 `510050`、`SH510050` 或 `SZ159995` 等不同前缀/无前缀格式的 ETF 代码
- **THEN** 工具透传该代码给后端接口，后端按既有规则标准化为 Tushare 格式（`510050.SH`/`159995.SZ`）并成功返回数据

#### Scenario: 无数据或接口失败
- **WHEN** 后端返回空 K 线或接口异常（404/500）
- **THEN** 工具向 AI 呈现可理解的错误信息（如"该代码无日线数据"或"接口错误"），AI 如实告知用户，不编造行情数据

### Requirement: ETF 日线工具输出格式
`get_etf_kline` 工具 SHALL 将 K 线序列渲染为紧凑可读的多行文本：每行一根 K 线，含交易日期、开、高、低、收、涨跌幅（成交量/成交额在数据可用时展示），并给出数据条数与数据源说明；返回内容 SHALL 控制长度，避免单次回复超长。

#### Scenario: 渲染 K 线摘要
- **WHEN** 工具取得 K 线数据
- **THEN** 输出包含数据条数、每根 K 线的日期与 OHLC/涨跌幅信息，供 AI 直接引用

#### Scenario: 长序列截断
- **WHEN** `count` 较大（如 284）
- **THEN** 工具输出仍保持紧凑（如仅展开最近若干根、其余统计性概括），AI 可按需分批查看

### Requirement: chat 模式工具可见性
QQ 聊天的 chat 模式会话 SHALL 能看到并调用 `get_etf_kline` 工具；该工具为只读查询，MUST NOT 触发任何交易写操作。

#### Scenario: QQ 聊天可直接调用
- **WHEN** 用户通过 QQ 向机器人询问 ETF 行情/走势
- **THEN** chat 会话的工具列表包含 `get_etf_kline`，AI 可正常调用并返回日线数据

#### Scenario: 模式权限边界
- **WHEN** chat/做T/回测等会话发起工具调用
- **THEN** `get_etf_kline` 仅执行后端只读查询，不产生下单、撤单、建仓等写副作用；对已配置 restrict 白名单的会话（如做T条件生成），该工具只在被显式列入 allow 名单时可见

### Requirement: 系统提示词指引
`CHAT_SYSTEM_PROMPT`（`backend/app/db/prompt_seeds.py` 种子及 API 可更新的运行版本）SHALL 包含 ETF 日线工具的使用指引，说明在用户询问 ETF 日 K 走势、趋势、均线位置时优先调用 `get_etf_kline`。

#### Scenario: AI 识别 ETF 话题
- **WHEN** 用户消息涉及具体 ETF（如"看看半导体 ETF 走势"）或日线/日K关键词
- **THEN** AI 依据提示词指引优先调用 `get_etf_kline` 而非凭经验作答或误用个股接口