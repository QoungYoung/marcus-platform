---
name: marcus-indicator-hallucination-fix
overview: 修复 Marcus Agent 技术指标幻觉问题：补全 Agent 工具链（get_kline/get_technical/get_realtime_indicator）、新增盘中实时技术指标计算模块（腾讯实时行情+Tushare历史日线→实时KDJ/RSI/MACD）、更新 System Prompt 禁止凭空引用指标信号。
todos:
  - id: create-realtime-calc
    content: 在 core/realtime_indicators.py 中新建实时指标计算模块，实现 KDJ/MACD/RSI/MA 的盘中估算算法
    status: completed
  - id: add-realtime-model
    content: 在 backend/app/models/market.py 中新增 RealtimeIndicatorItem 和 RealtimeIndicatorResponse 模型
    status: completed
  - id: add-realtime-endpoint
    content: 在 backend/app/api/indicator.py 中新增 /realtime/{symbol} 端点，集成腾讯实时行情 + Tushare 历史数据
    status: completed
    dependencies:
      - create-realtime-calc
      - add-realtime-model
  - id: add-agent-tools
    content: 在 agent.py 中注入 get_kline/get_technical/get_realtime_indicators 三个工具及其实现和格式化逻辑
    status: completed
    dependencies:
      - add-realtime-endpoint
  - id: update-system-prompt
    content: 在 agent.py 的 SYSTEM_PROMPT 末尾追加技术指标引用规范（禁止编造、标注来源、过时警告）
    status: completed
    dependencies:
      - add-agent-tools
---

## 产品概述

为 Marcus AI 交易 Agent 补齐技术指标工具链，结束其"凭空编造 KDJ/MACD/RSI 信号"的幻觉问题。核心思路：腾讯 qt.gtimg.cn 实时 OHLCV + Tushare 历史日线 → 实时估算盘中 KDJ/MACD/RSI/MA → 暴露为 API → 注入 Agent 工具列表 → System Prompt 追加防幻觉和数据来源透明化规则。

## 核心功能

- **实时技术指标计算模块**：基于腾讯实时行情（当前价/最高/最低）与 Tushare 历史日线数据，实时估算盘中 KDJ（9,3,3）、MACD（12,26,9）、RSI（6/12/24）、MA（5/10/20），并标注数据来源为"盘中估算（未收盘确认）"
- **API 端点暴露**：在 `/indicator/realtime/{symbol}` 返回实时指标，同时可降级返回最近 N 日的 Tushare 盘后历史指标作为趋势参考
- **Agent 工具注入**：向 agent.py 新增 3 个 function calling 工具：`get_kline`（日K线）、`get_technical`（历史盘后技术指标）、`get_realtime_indicators`（盘中实时估算指标）
- **System Prompt 防幻觉规则**：禁止编造任何技术指标信号，引用 KDJ/MACD/RSI/MA 必须附带工具调用的实际返回值，必须标注数据来源（盘后确认/盘中估算），盘中估算指标不能作为唯一建仓理由

## 技术选型

- **后端框架**：FastAPI（沿用现有项目架构）
- **数据源**：腾讯 qt.gtimg.cn（实时 OHLCV）+ Tushare pro（历史日线 daily + 历史指标 stk_factor_pro）
- **实时计算**：纯 Python 实现 KDJ/MACD/RSI/MA 公式，无额外依赖
- **工具注册**：沿用 agent.py 现有的 TOOLS + TOOL_IMPLEMENTATIONS 模式

## 实现方案

### 整体策略

采用"计算层 → API 层 → Agent 工具层"三层架构，复用现有基础设施（call_marcus_api、_normalize_to_ts_code、XueqiuEngine），最小化侵入现有代码。

### 关键公式

KDJ 计算需要前 N 日最高最低价——这是盘中 KDJ 的误差根源（今日 high/low 未最终确定）。因此实时指标返回时必须附带 `data_source: "intraday_estimate"` 标记，与 Tushare 盘后确认的 `"daily_confirmed"` 明确区分。

### 性能考量

- 腾讯接口已有缓存（XueqiuEngine 内存缓存 + 5 分钟 TTL），单次查询不产生额外 HTTP 请求
- Tushare 历史日线 API 调用每次约 200ms，在 `/realtime` 端点内并行获取（daily + stk_factor_pro），总耗时约 400ms
- Agent 工具格式化输出精简到关键字段，避免向 LLM 注入过长上下文（限制返回最近 3 日历史 + 当日实时估算）

## 架构设计

```mermaid
flowchart TD
    subgraph 数据层
        A[腾讯 qt.gtimg.cn] -->|实时 OHLCV| B[XueqiuEngine]
        C[Tushare daily] -->|历史日线| D[/market/kline API]
        E[Tushare stk_factor_pro] -->|盘后指标| F[/market/technical API]
    end
    
    subgraph 计算层
        G[core/realtime_indicators.py]
        B -->|current/high/low| G
        D -->|历史 OHLCV| G
        F -->|前日 KDJ/MACD 基准| G
    end
    
    subgraph API层
        H[/indicator/realtime API]
        G -->|盘中估算指标| H
        F -->|历史盘后指标| H
    end
    
    subgraph Agent工具层
        I[agent.py TOOLS]
        J[get_kline] --> D
        K[get_technical] --> F
        L[get_realtime_indicators] --> H
    end
    
    M[Marcus Agent] -->|function calling| I
    M -->|System Prompt 约束| N[防幻觉规则]
```

## 目录结构

```
f:/pythonProject/AITrade/marcus-platform/
├── core/
│   └── realtime_indicators.py          # [NEW] 实时技术指标计算模块
│       实现 KDJ/MACD/RSI/MA 的盘中估算算法。
│       输入：今日实时 OHLCV dict + 历史日线 DataFrame + 前日已确认指标 dict
│       输出：RealtimeIndicatorResult dataclass（含 data_source 标记）
│       纯函数设计，不依赖 FastAPI，可被 backend 和 jobs 复用。
│
├── backend/app/
│   ├── models/
│   │   └── market.py                   # [MODIFY] 新增 RealtimeIndicatorResponse 模型
│   │       新增类：
│   │       - RealtimeIndicatorItem：单条实时指标（kdj_k/kdj_d/kdj_j/macd_dif/macd_dea/macd_bar/rsi_6/rsi_12/rsi_24/ma5/ma10/ma20 + data_source 标记）
│   │       - RealtimeIndicatorResponse：symbol + realtime(RealtimeIndicatorItem) + historical(List[TechnicalData], 最近3日)
│   │
│   ├── api/
│   │   ├── indicator.py                # [MODIFY] 新增 /realtime/{symbol} 端点
│   │   │   新增 `get_realtime_indicators()` 函数：
│   │   │   - 并行获取：腾讯实时行情 + Tushare daily(60日) + stk_factor_pro(35日)
│   │   │   - 调用 core/realtime_indicators 进行实时指标估算
│   │   │   - 返回 RealtimeIndicatorResponse（实时估算 + 最近3日历史盘后指标）
│   │   │   - 降级逻辑：腾讯接口不可用时仅返回历史盘后指标 + 警告
│   │   │
│   │   └── agent.py                    # [MODIFY] 3 处修改
│   │       TOOLS 列表 (L34-L175)：
│   │         + get_kline：获取个股日K线，参数 symbol/start_date/end_date/limit
│   │         + get_technical：获取个股历史盘后技术指标（KDJ/MACD/RSI/BOLL）
│   │         + get_realtime_indicators：获取个股盘中实时估算技术指标
│   │       TOOL_IMPLEMENTATIONS (L204-L220)：
│   │         + get_kline → /api/v1/market/kline/{symbol}
│   │         + get_technical → /api/v1/market/technical/{symbol}
│   │         + get_realtime_indicators → /api/v1/indicator/realtime/{symbol}
│   │       format_result_for_llm (L353-L390)：
│   │         + 新增 get_kline/get_technical/get_realtime_indicators 的 LLM 格式化逻辑
│   │       SYSTEM_PROMPT (L224-L267)：
│   │         在"数据说话"沟通风格后，追加技术指标引用规范段落
│   │
│   └── main.py                         # 无需修改（indicator.router 已注册）
```

## 关键代码结构

### RealtimeIndicatorItem 模型定义

在 `backend/app/models/market.py` 中新增：

```python
class RealtimeIndicatorItem(BaseModel):
    """盘中实时估算技术指标（单条）"""
    symbol: str                          # 股票代码
    current_price: float                 # 当前价
    data_source: str                     # "intraday_estimate" 或 "daily_confirmed"
    calc_time: datetime                  # 计算时间
    # KDJ (9,3,3)
    kdj_k: float
    kdj_d: float
    kdj_j: float
    # MACD (12,26,9)
    macd_dif: float
    macd_dea: float
    macd_bar: float
    # RSI
    rsi_6: float
    rsi_12: float
    rsi_24: float
    # MA
    ma5: float
    ma10: float
    ma20: float

class RealtimeIndicatorResponse(BaseModel):
    """实时指标查询响应"""
    symbol: str
    name: str = ""
    realtime: RealtimeIndicatorItem      # 实时估算指标
    historical: List[TechnicalData] = [] # 最近3日盘后确认指标
    warning: str = ""                     # 盘中估算警告信息
    updated_at: datetime
```

### System Prompt 追加规则

在 `agent.py` 的 `SYSTEM_PROMPT` 末尾追加：

```
### 技术指标引用规范（严格遵循）

1. **必须用工具取实际值**：KDJ/MACD/RSI/MA/BOLL 等任何技术指标，必须通过以下工具获取：
   - get_realtime_indicators(symbol)：获取盘中实时估算指标（标注"盘中估算"）
   - get_technical(symbol)：获取最近交易日盘后确认指标（标注"盘后确认"）
   - get_kline(symbol)：获取日K线原始数据
   严禁在未调用工具的情况下凭空编造任何指标数值或信号（如"KDJ金叉""MACD底背离"等）。

2. **必须标注数据来源**：每次引用指标时，必须附带 data_source 标记：
   - 盘中估算值：仅作为辅助参考，不能作为独立建仓的唯一理由
   - 盘后确认值：可用于交易决策，但需结合当日实时行情判断是否仍有效

3. **禁止过时信号**：昨日盘后的金叉/死叉在今天开盘后即可能失效，引用时必须说明"该信号基于 T-N 日收盘数据，今日盘中需重新确认"
```

## Agent Extensions

### SubAgent

- **code-explorer**
- 用途：在实现实时指标计算模块时，搜索 Tushare daily 接口的字段定义和 stk_factor_pro 接口参数，确认 KDJ/MACD/RSI 公式所需的原始字段名，以及在 agent.py 中定位 TOOLS 注入点和 format_result 扩展点
- 预期结果：确认所有数据源的字段映射关系，确保计算模块不会写错字段名