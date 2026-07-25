## Context

当前 `paper_engine.py` 是自研的模拟交易引擎，核心问题：

1. **持仓是用 trades 表 FIFO 重放计算的**（`_load_positions_from_db`），每次实例化都要扫全表——昂贵且容易状态漂移
2. **`paper_positions` meta 表与 FIFO 计算脱节**——buy match 时 upsert，但 sell 清仓后忘了 delete，导致 000063 仍显示而 SZ000938 缺失
3. **`MarcusVNPyExecutor` 不是单例**——每个 API 请求 / 监控器都 `new` 一个 `PaperTradingEngine`，虽然有 `FOR UPDATE` 行锁保护 account_info，但 `self.positions` 内存状态在实例间不一致
4. **VN.PY 原版 `PaperAccountApp`** 用事件驱动架构 + 内存持仓模型，FIFO 在成交时即时计算，不存在重放问题，且经过了整个社区验证

## Goals / Non-Goals

**Goals:**
- 用 VN.PY 原版 `MainEngine` + `PaperAccountApp` 替代自研 `PaperTradingEngine` 的撮合/持仓/资金管理
- 保持 `MarcusVNPyExecutor` 对外的接口不变（`buy`/`sell`/`get_account`/`get_positions`/`get_trades`）
- 将 VN.PY 事件引擎产生的事件同步写入 PostgreSQL，保证现有 API (`/portfolio`, `/trades/history`) 继续工作
- 提供数据迁移脚本，无缝迁移现有账户状态
- VN.PY 事件引擎作为 FastAPI lifespan 管理的单例 daemon 线程

**Non-Goals:**
- 不引入 VN.PY 的 GUI (PyQt5) —— 继续使用现有 React 前端
- 不接入实盘行情接口（CTP/XTP）—— 本次只替换模拟撮合层
- 不改动回测引擎 (`backtest_engine.py` / `BacktestPaperEngine`)
- 不改变现有 API 接口的 request/response 格式
- 不改动 `paper_orders`/`paper_trades` 表结构

## Decisions

### Decision 1: 事件驱动桥接层 (VNPyBridge) 替代直接调用

**选择**：新增 `backend/app/core/trading/vnpy_bridge.py`，封装 `MainEngine` + `PaperAccountApp`，暴露同步接口。

```
┌─────────────────────────────────────────────────────────────┐
│                      MarcusVNPyExecutor                     │
│  buy() / sell() / get_account() / get_positions() / ...    │
└──────────────────────────┬──────────────────────────────────┘
                           │ 调用
┌──────────────────────────▼──────────────────────────────────┐
│                     VNPyBridge (新增)                       │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  MainEngine (VN.PY 原版)                             │  │
│  │  ├─ EventEngine      ← 事件总线 (独立线程)            │  │
│  │  ├─ PaperAccountApp  ← 模拟账户撮合                   │  │
│  │  ├─ OmsEngine         ← 订单管理系统                   │  │
│  │  └─ 事件监听器 (新增)                                 │  │
│  │       ├─ OrderEventListener   → PGSQL paper_orders   │  │
│  │       ├─ TradeEventListener   → PGSQL paper_trades   │  │
│  │       ├─ AccountEventListener → PGSQL paper_account_info│
│  │       └─ PositionEventListener→ PGSQL paper_positions │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│  send_order(symbol, direction, price, volume) → order_id   │
│  get_account() → dict                                      │
│  get_positions() → List[dict]                              │
│  get_trades() → List[dict]                                 │
└────────────────────────────────────────────────────────────┘
```

**理由**：
- VN.PY 的 `MainEngine` 是事件驱动架构，`send_order` 不会立即返回成交结果——订单经过 `OmsEngine` → 撮合 → `TradeEvent` 回调
- 桥接层负责把异步事件流转换为 Marcus 系统期望的同步接口
- 监听器模式保证 PostgreSQL 同步是 Reactive 的（事件 push），而非 Polling

**备选方案**：直接 import `vnpy_paperaccount` 并调用其内部函数 —— 绕过了 VN.PY 的事件架构，且 `PaperAccountApp` 依赖 `MainEngine.event_engine` 的事件注册。已否决。

### Decision 2: VN.PY 主引擎作为 FastAPI lifespan 单例

**选择**：在 `backend/app/main.py` 的 `lifespan` 中启动 `VNPyBridge` 单例，存为 `app.state.vnpy_bridge`，所有 `MarcusVNPyExecutor` 共享同一实例。

```python
# main.py lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    bridge = VNPyBridge()
    bridge.start()                    # 启动 VN.PY 事件引擎线程
    app.state.vnpy_bridge = bridge
    # ... 启动监控器 (注入 bridge)
    yield
    # ... 停止监控器
    bridge.stop()                     # 停止事件引擎
```

**理由**：
- 当前 `MarcusVNPyExecutor` 每次 `new PaperTradingEngine()` 导致内存状态不一致
- VN.PY 的 `MainEngine` 设计上就是单例——事件引擎只有一个 event queue，多个 MainEngine 实例会导致事件路由混乱
- singleton 保证了所有调用者（API handler、4 个监控器）看到同一份持仓/账户内存状态

**备选方案**：每次请求创建新的 `MainEngine` 并从 PostgreSQL 恢复状态 —— 违背 VN.PY 的设计理念，且 `MainEngine` 构造函数会启动 EventEngine 线程，反复创建销毁有线程泄漏风险。已否决。

### Decision 3: 订单/成交数据双写（VN.PY 内部 + PostgreSQL）

**选择**：通过注册 VN.PY 事件监听器，在 `EVENT_ORDER`、`EVENT_TRADE`、`EVENT_ACCOUNT`、`EVENT_POSITION` 事件回调中同步写入 `paper_orders`、`paper_trades`、`paper_account_info`、`paper_positions`。

```
VN.PY Event              →  PGSQL Table
─────────────────────────────────────────
EVENT_ORDER (提交/成交/撤销) → paper_orders
EVENT_TRADE (每笔成交)       → paper_trades (含 profit)
EVENT_ACCOUNT (资金变动)     → paper_account_info
EVENT_POSITION (持仓变动)    → paper_positions (同步 upsert/delete)
```

**理由**：
- 所有现有 API 和前端都从 PostgreSQL 读数据，不能断
- 事件驱动的 push 模式保证了数据一致性：持仓事件触发时就是准确的，不需要事后重放
- VN.PY 内部的 `AccountData` / `PositionData` 自带所有必要字段（volume, frozen, avg_price 等），`paper_positions` 表终于可以存完整的仓位信息
- `EVENT_TRADE` 中 VN.PY 已按 FIFO 计算好 `trade.profit`，彻底消除 realized_pnl 不一致问题

**备选方案**：让 API 层直接查询 VN.PY 内存数据而不经过 PostgreSQL —— 需要 VN.PY 主循环能处理查询请求（当前不支持），且破坏了现有的 API → PostgreSQL 数据流。已否决。

### Decision 4: `paper_positions` 表结构扩充

**选择**：给 `paper_positions` 增加 `volume`、`frozen`、`avg_price` 列，使其成为权威持仓记录而非仅存 meta。

```sql
ALTER TABLE paper_positions
  ADD COLUMN volume INT DEFAULT 0,
  ADD COLUMN frozen INT DEFAULT 0,
  ADD COLUMN avg_price DOUBLE PRECISION DEFAULT 0;
```

**理由**：
- 当前 `paper_positions` 只有 `entry_date` / `highest_price`，股数和成本要从 trades 表 FIFO 重算
- VN.PY 的 `EVENT_POSITION` 事件直接输出 `PositionData(volume, frozen, avg_price)`，写入完整列就不需要事后重放
- 这是解决 "统计总是对不上" 的根本方案——持仓数据存在一个地方，只写一次，不再计算

**备选方案**：保持表结构不变，在 API 层查询时从 VN.PY 内存数据获取 —— 仍然依赖 PostgreSQL 外的数据源，前端直接查表会不一致。已否决。

### Decision 5: 迁移策略 —— 从 PostgreSQL 恢复到 VN.PY 初始状态

**选择**：提供一次性迁移脚本 `scripts/migrate_to_vnpy.py`：
1. 从 `paper_account_info` 读取 `available_cash`、`frozen_cash`、`initial_capital`
2. 从 `paper_trades` FIFO 重算当前持仓列表
3. 在 VN.PY 中逐笔 replay 历史成交（或设置初始持仓 + 现金）
4. 验证 VN.PY 账户总资产 = 原 PGSQL 的 `total_asset`

采用**只恢复最终状态**的策略（不重放历史订单），因为历史 trades 已存在于 `paper_trades` 表中供查询。

## Risks / Trade-offs

- **[风险] VN.PY 事件引擎线程与 FastAPI async loop 的生命周期竞态** → 在 `lifespan` startup 中先启动 bridge，确认事件引擎就绪后再 yield；shutdown 时先停监控器再停 bridge
- **[风险] `send_order` 异步回调导致 `buy()` 返回时订单可能尚未成交** → 桥接层 `send_order` 内部等待 `EVENT_ORDER` (status=ALLTRADED) 事件，超时 5 秒返回失败
- **[风险] VN.PY 依赖安装失败（C++ 编译、PyQt5 依赖等）** → 仅安装核心模块 `vnpy` + `vnpy_paperaccount`，不装 GUI 相关包；Docker 镜像预编译
- **[权衡] VN.PY v4.x 的 `PaperAccountApp` 不支持 T+1 规则** → 在 `MarcusVNPyExecutor.check_risk()` 中保留现有 T+1 拦截逻辑，不在引擎层处理
- **[权衡] 回测系统 (`BacktestPaperEngine`) 暂不迁移** → 回测用的是独立沙盒数据库，不需要实时的 VN.PY 事件引擎

## Migration Plan

1. **开发阶段**：`vnpy_bridge.py` 与现有 `paper_engine.py` 并存，通过 `ENGINE_BACKEND=vnpy|paper` 环境变量切换
2. **测试验证**：`ENGINE_BACKEND=vnpy` 启动，执行完整的 buy → match → sell → 查账户/持仓 流程，对比两套引擎输出
3. **数据迁移**：运行 `scripts/migrate_to_vnpy.py`，导入现有账户状态
4. **切换上线**：修改默认 `ENGINE_BACKEND` 为 `vnpy`，重启服务
5. **回退方案**：设置 `ENGINE_BACKEND=paper` 即可切回老引擎
6. **清理**：上线稳定 2 周后，删除 `paper_engine.py` 的撮合逻辑（保留数据查询方法供回测用）

## Open Questions

1. VN.PY 的 `PaperAccountApp` 是否支持通过 API 设置初始持仓（而非通过历史成交重放）？需要验证 `AccountData` 的初始化接口
2. 现有 20 笔 voided 交易如何处理？迁移时 skip voided trades 还是也迁移？
3. `paper_daily_snapshot` 的生成逻辑是否继续保留？VN.PY 没有内置的日终快照概念
