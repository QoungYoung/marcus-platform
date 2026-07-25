## Why

当前自研的 `paper_engine.py` 模拟交易引擎存在持仓同步 Bug（已清仓标的仍显示、新建仓标的缺失）、已实现盈亏与 trades 表不一致、`paper_positions` meta 表与 FIFO 计算脱节等问题。每次从 trades 表重放计算持仓的本质设计导致状态容易漂移。接入 VN.PY 原版 `vnpy_paperaccount` 模块，用万人验证过的成熟引擎替代自己写的模拟撮合层，彻底消除统计不准的问题。

## What Changes

- **BREAKING**: 移除自研 `apps/paper-trading/paper_engine.py` 的撮合/持仓/账户管理逻辑，替换为 VN.PY 原版 `PaperAccountApp`
- 新增 `backend/app/core/trading/vnpy_bridge.py` 作为 VN.PY 事件引擎与 Marcus 系统的桥接层，封装 `MainEngine` + `PaperAccountApp` 的生命周期管理
- **BREAKING**: `MarcusVNPyExecutor.buy()` / `sell()` / `get_account()` / `get_positions()` 改为调用 VN.PY 原版 API
- 设计兼容现有 `paper_account_info` / `paper_orders` / `paper_trades` PostgreSQL 表结构的数据同步层，将 VN.PY 事件引擎产生的订单/成交/账户事件同步写入 PGSQL
- 现有的 4 个后台监控器（StopLoss / PositionTier / CandidatePool / LongTermPool）通过桥接层继续正常工作，无需感知底层引擎变更
- `./manage scheduler:` 管理命令功能不变；提供 `--engine [n|p]` 切换开关（默认 n）。
- 提供数据迁移脚本，将现有 PostgreSQL 中的账户状态映射到 VN.PY 初始状态

## Capabilities

### New Capabilities

- `vnpy-integration`: VN.PY 原版模拟交易引擎集成，包括事件引擎启动/停止、PaperAccount 撮合、数据同步到 PostgreSQL

### Modified Capabilities

- `trading`: 下单/成交的底层执行引擎从自研 paper_engine 切换到 VN.PY PaperAccount，接口和行为不变，但内部实现替换
- `portfolio`: 账户摘要/持仓查询的数据来源从 paper_engine 直接查表改为从 VN.PY 事件引擎同步后的 PGSQL 表读取

## Impact

- **依赖**: 新增 `vnpy>=4.0`、`vnpy_paperaccount` Python 包依赖
- **数据库**: `paper_account_info`、`paper_positions` 表结构需适配 VN.PY 的输出格式；新增 `vnpy_event_log` 表记录事件同步状态
- **API**: `/api/v1/trades`、`/api/v1/portfolio` 接口不变，但响应数据的来源和精度改善
- **部署**: Docker Compose 需增加 VN.PY 依赖安装步骤；本地开发环境需 `pip install vnpy vnpy_paperaccount`
- **风险**: VN.PY 事件引擎需在独立线程运行，与现有 FastAPI async event loop 共存
