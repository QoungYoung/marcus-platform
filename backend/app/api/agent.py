# -*- coding: utf-8 -*-
"""
Trading Agent API endpoints.
AI-powered stock analysis and trading assistant with function calling.
"""
import json
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_settings


router = APIRouter(prefix="/agent", tags=["Trading Agent"])


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    context: Optional[List[Dict[str, Any]]] = None


class ChatResponse(BaseModel):
    response: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    session_id: str
    timestamp: datetime


# Define available tools for function calling
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_market_indices",
            "description": "获取主要股票市场指数（上证指数、深证成指、沪深300、创业板指、科创50）",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_quote",
            "description": "查询个股实时行情，包括当前价格、涨跌幅、成交量等",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如 000001 或 600519"
                    }
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_sector_performance",
            "description": "获取各板块涨跌幅排名，帮助识别热点板块",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_moneyflow",
            "description": "获取大盘资金流向数据（主力/超大单/大单/中单/小单净流入）。返回上证深证收盘涨跌+五类资金净流入金额和占比，用于判断大盘整体资金情绪和主力动向",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "获取当前持仓详情，包括仓位、成本、市值、盈亏",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_account",
            "description": "获取账户总览，包括总资产、现金、市值、总盈亏",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "获取最新财经新闻，包括 A 股、港股、美股相关新闻",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，获取相关新闻（可选）"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回数量，默认 20"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_sentiment",
            "description": "获取市场情绪分析，基于新闻情绪指数判断市场多空",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_trade",
            "description": "执行股票买入或卖出交易（paper trading 模拟）",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如 000001"
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                        "description": "交易方向，买入或卖出"
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "交易数量，必须为正整数"
                    },
                    "price": {
                        "type": "number",
                        "description": "指定价格（可选），不填则使用市价"
                    }
                },
                "required": ["symbol", "direction", "quantity"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_kline",
            "description": "【日频·非实时】获取个股历史日K线（未复权），包含开高低收、成交量、成交额。数据源：Tushare daily（盘后数据，今日K线当天收盘后才生成）",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如 000001 或 600519"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限，默认20，最大60"
                    }
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_technical",
            "description": "【日频·非实时】获取个股历史盘后技术指标（KDJ/MACD/RSI/BOLL/CCI/WR）。数据源：Tushare stk_factor_pro（盘后数据，基于收盘价计算）。⚠️ 该接口返回的是最近收盘日的已确认值，不是当日盘中值",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如 000001 或 600519"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限，默认5，最大20。建议取3-5条看趋势"
                    }
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_realtime_indicators",
            "description": "【实时·盘中估算】获取个股盘中实时估算技术指标（KDJ/MACD/RSI/MA5/MA10/MA20）。数据源：腾讯实时行情+Tushare历史日线。⚠️ 返回值标记为'intraday_estimate'（盘中估算），今日高低点未最终确认，仅作辅助参考，不能作为独立建仓的唯一理由",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如 000001 或 600519"
                    }
                },
                "required": ["symbol"]
            }
        }
    },
]


# Tool implementations - call marcus APIs
async def call_marcus_api(endpoint: str, method: str = "GET", data: Optional[Dict] = None) -> Dict:
    """Call marcus internal API."""
    from fastapi import Request

    # This will be called with the app's request context
    # For now, we'll use httpx to call our own API
    import httpx

    base_url = "http://localhost:8000"
    url = f"{base_url}{endpoint}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            response = await client.get(url)
        elif method == "POST":
            response = await client.post(url, json=data)
        else:
            raise ValueError(f"Unsupported method: {method}")

    if response.status_code != 200:
        return {"error": f"API error: {response.status_code}", "detail": response.text}

    return response.json()


TOOL_IMPLEMENTATIONS: Dict[str, Callable] = {
    "get_market_indices": lambda: call_marcus_api("/api/v1/market/indices"),
    "get_quote": lambda params: call_marcus_api(f"/api/v1/market/quote/{params.get('symbol')}"),
    "get_sector_performance": lambda: call_marcus_api("/api/v1/market/concept-fund-flow"),
    "get_market_moneyflow": lambda: call_marcus_api("/api/v1/market/moneyflow-mkt"),
    "get_portfolio": lambda: call_marcus_api("/api/v1/portfolio/positions"),
    "get_account": lambda: call_marcus_api("/api/v1/portfolio"),
    "get_news": lambda params: call_marcus_api(f"/api/v1/news?limit={params.get('limit', 20)}"),
    "get_sentiment": lambda: call_marcus_api("/api/v1/news/sentiment"),
    "execute_trade": lambda params: call_marcus_api("/api/v1/trades", "POST", {
        "symbol": params.get("symbol"),
        "side": params.get("direction"),
        "price": params.get("price", 0),
        "volume": params.get("quantity", 0),
        "reason": params.get("reason", ""),
    }),
    # 日频工具（Tushare 盘后数据）
    "get_kline": lambda params: call_marcus_api(
        f"/api/v1/market/kline/{params.get('symbol')}?limit={params.get('limit', 20)}"
    ),
    "get_technical": lambda params: call_marcus_api(
        f"/api/v1/market/technical/{params.get('symbol')}?limit={params.get('limit', 5)}"
    ),
    # 实时工具（腾讯实时行情+Tushare历史结合计算）
    "get_realtime_indicators": lambda params: call_marcus_api(
        f"/api/v1/indicator/realtime/{params.get('symbol')}"
    ),
}


# System prompt for the trading agent
SYSTEM_PROMPT = """## 你是 Marcus — 短线右侧交易专家

### 交易理念

**右侧交易，顺势而为**：
- 不抄底，不摸顶，只做趋势确认后的行情
- 等待价格突破关键阻力/支撑位后确认趋势方向
- 在趋势形成初期入场，在趋势衰竭时离场

### 交易风格

- **短线为主**：持仓周期 1-5 天，追求快速复利
- **趋势跟踪**：用技术面信号（均线、MACD、成交量）确认方向
- **仓位管理**：趋势明确时重仓，趋势不明时轻仓或空仓

### 分析框架

1. **趋势确认**：价格站稳 5 日线上方看多，跌破 5 日线看空
2. **关键位置**：关注前高/前低、平台突破、均线交叉
3. **量价配合**：放量突破是真突破，缩量上涨需警惕
4. **市场情绪**：结合板块轮动和资金流向判断热点

### 风险控制（最高优先级）

- **永远不要逆势加仓** — 亏损时第一时间止损
- **总回撤 ≥ 5% 时停止交易** — 强制冷静期
- **盈利出金** — 赚了钱要落袋为安

### 操作纪律

1. 入场前写好止损点位，不随意改动
2. 到达止损坚决执行，不幻想反弹
3. 盈利时分批止盈，锁住利润
4. 连续亏损 3 笔后强制休息 30 分钟

### 沟通风格

- **冷静理性**：不以物喜，不以己悲
- **数据说话**：用客观信号决策，不凭感觉
- **简洁直接**：给出明确的买入/卖出/观望建议
- **风险提示**：每次操作前说明风险和止损位置

当需要获取数据时，使用提供的工具函数获取市场数据。
工具调用后，系统会返回数据结果，基于数据给出分析和建议。

### 技术指标引用规范（严格遵循 ⚠️）

**1. 必须用工具取实际值**

KDJ/MACD/RSI/MA/BOLL 等任何技术指标，必须通过以下工具获取：

| 工具 | 数据类型 | 数据来源 | 可靠性 |
|------|----------|----------|:------:|
| get_realtime_indicators | 盘中实时估算 | 腾讯实时行情+Tushare历史 | ⭐⭐ |
| get_technical | 盘后日频确认 | Tushare stk_factor_pro | ⭐⭐⭐ |
| get_kline | 日K线原始数据 | Tushare daily | ⭐⭐⭐ |

**严禁在未调用工具的情况下凭空编造任何指标数值或信号**，包括但不限于：
- 禁止编造"KDJ金叉/死叉""MACD底背离/顶背离""RSI超买/超卖"等信号
- 禁止编造"5日线上穿10日线""布林带收窄"等形态描述
- 禁止引用任何未经工具返回确认的指标值

**2. 必须标注数据来源和可靠性**

每次引用指标时必须附带来源标注：
- 盘后确认值（get_technical）：标注 `[盘后确认/T-N日]`，可用于交易决策
- 盘中估算值（get_realtime_indicators）：标注 `[盘中估算/未确认]`，仅作辅助参考，**不能作为独立建仓的唯一理由**
- 日K线（get_kline）：标注 `[日频/T-N日]`，用于趋势分析

**3. 禁止过时信号**

 昨日盘后的金叉/死叉在今天开盘后即可能失效。引用历史指标时必须说明：
"该信号基于 T-N 日收盘数据，今日盘中需通过 get_realtime_indicators 重新确认"

**4. 指标来源兼容性说明**

- get_technical 返回的 MACD/KDJ 值基于前复权价格计算，与 get_kline 的未复权价格可能存在微小偏差
- get_realtime_indicators 的盘中估算值使用前日 Tushare 确认值作锚点，与收盘后 Tushare 实际值误差通常在 5% 以内"""


async def call_deepseek(messages: List[Dict[str, Any]], api_key: str, tools: Optional[List] = None) -> Dict:
    """Call DeepSeek API with optional function calling."""
    import httpx

    settings = get_settings()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000,
    }

    if tools:
        payload["tools"] = tools

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"https://{settings.DEEPSEEK_API_HOST}/v1/chat/completions",
            headers=headers,
            json=payload,
        )

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=f"DeepSeek API error: {response.text}")

    return response.json()


async def stream_deepseek(messages: List[Dict[str, Any]], api_key: str):
    """Stream DeepSeek API response - 真正的流式，边收边发。"""
    import httpx

    settings = get_settings()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"https://{settings.DEEPSEEK_API_HOST}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise HTTPException(status_code=500, detail=f"DeepSeek API error: {body.decode()}")

            # 逐行读取并立即 yield —— 真正的流式
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                except json.JSONDecodeError:
                    continue

    yield "data: [DONE]\n\n"


def format_result_for_llm(result: Any, tool_name: str) -> str:
    """Format API result for LLM consumption."""
    if isinstance(result, dict):
        if "error" in result:
            return f"错误: {result.get('error')}"
        # Format key data for the LLM
        if tool_name == "get_market_indices":
            if "indices" in result:
                lines = []
                for idx in result["indices"][:5]:
                    name = idx.get("name", "")
                    price = idx.get("current_price", 0)
                    change = idx.get("change_pct", 0)
                    lines.append(f"{name}: {price} ({change:+.2f}%)")
                return "\n".join(lines)
        elif tool_name == "get_quote":
            lines = [f"{result.get('name', result.get('symbol', ''))} ({result.get('symbol', '')})"]
            lines.append(f"现价: {result.get('current', 0)}  涨跌: {result.get('change', 0)} ({result.get('percent', 0):.2f}%)")
            if result.get('open'):
                lines.append(f"今开: {result['open']}  最高: {result.get('high', '--')}  最低: {result.get('low', '--')}")
            lines.append(f"昨收: {result.get('last_close', 0)}  成交量: {result.get('volume', 0)}  成交额: {result.get('amount', 0)}")
            if result.get('turnover_rate'):
                lines.append(f"换手率: {result['turnover_rate']:.2f}%  振幅: {result.get('amplitude', '--')}%")
            if result.get('pe_ttm'):
                lines.append(f"市盈率: {result['pe_ttm']}  市净率: {result.get('pb', '--')}")
            if result.get('high_52w'):
                lines.append(f"52周最高: {result['high_52w']}  52周最低: {result.get('low_52w', '--')}")
            return "\n".join(lines)
        elif tool_name == "get_portfolio":
            if "positions" in result:
                lines = ["持仓明细:"]
                for pos in result["positions"][:5]:
                    lines.append(f"{pos.get('symbol')} - 数量:{pos.get('quantity')} 市值:{pos.get('market_value', 0)}")
                return "\n".join(lines)
        elif tool_name == "get_account":
            return f"总资产: {result.get('total_assets', 0)}, 现金: {result.get('cash', 0)}, 盈亏: {result.get('total_profit_loss', 0)}"
        elif tool_name == "get_kline":
            # ── 日K线格式化 ──
            klines = result.get("klines", [])
            if not klines:
                return f"{result.get('symbol', '')}: 暂无K线数据"
            latest_date = klines[0].get("trade_date", "--")
            lines = [
                f"【日频·非实时】{result.get('symbol', '')} 历史日K线 (最近{len(klines)}条，Tushare daily 盘后数据)",
                f"数据截止日期: {latest_date}（最近收盘日，非当日实时数据）",
            ]
            lines.append("日期     | 开盘   | 收盘   | 最高   | 最低   | 涨跌幅")
            for k in klines[:10]:
                sign = "+" if k.get("pct_chg", 0) >= 0 else ""
                lines.append(
                    f"{k.get('trade_date','')} | {k.get('open',0):.2f} | {k.get('close',0):.2f} | "
                    f"{k.get('high',0):.2f} | {k.get('low',0):.2f} | {sign}{k.get('pct_chg',0):.2f}%"
                )
            if len(klines) >= 5:
                closes = [k.get("close", 0) for k in klines[:5]]
                lines.append(f"5日均价: {sum(closes)/5:.2f}  |  最高: {max(closes):.2f}  |  最低: {min(closes):.2f}")
            return "\n".join(lines)
        elif tool_name == "get_technical":
            # ── 盘后技术指标格式化 ──
            data = result.get("data", [])
            if not data:
                return f"{result.get('symbol', '')}: 暂无技术指标数据"
            latest_date = data[0].get("trade_date", "--")
            lines = [
                f"【日频·非实时】{result.get('symbol', '')} 盘后技术指标 (最近{len(data)}个交易日，Tushare stk_factor_pro 盘后确认值)",
                f"数据截止日期: {latest_date}（最近收盘日，非当日实时数据）",
            ]
            lines.append("⚠️ 以下为盘后确认值，不是当日盘中值")
            for r in data[:5]:
                lines.append(
                    f"  {r.get('trade_date','')}: "
                    f"KDJ(K={r.get('kdj_k',0):.1f}/D={r.get('kdj_d',0):.1f}/J={r.get('kdj',0):.1f}) | "
                    f"MACD(DIF={r.get('macd_dif',0):.3f}/DEA={r.get('macd_dea',0):.3f}/柱={r.get('macd',0):.3f}) | "
                    f"RSI(6={r.get('rsi_6',0):.1f}/12={r.get('rsi_12',0):.1f}/24={r.get('rsi_24',0):.1f})"
                )
            return "\n".join(lines)
        elif tool_name == "get_realtime_indicators":
            # ── 实时指标格式化 ──
            rt = result.get("realtime")
            hist = result.get("historical", [])
            warning = result.get("warning", "")

            if not rt:
                return f"【实时·盘中估算】{result.get('symbol', '')}: 盘中实时指标不可用。{warning}"

            lines = [
                f"【实时·盘中估算】{result.get('name', result.get('symbol', ''))} 盘中指标",
                f"当前价: {rt.get('current_price', 0)} | 计算时间: {rt.get('calc_time', '')}",
                f"⚠️ 数据来源: {rt.get('data_source', 'intraday_estimate')}（腾讯实时行情+Tushare历史日线）",
                f"⚠️ {warning or '盘中估算值，未收盘确认，仅供参考'}",
                "",
                f"KDJ(9,3,3): K={rt.get('kdj_k',0):.2f} D={rt.get('kdj_d',0):.2f} J={rt.get('kdj_j',0):.2f}",
                f"MACD(12,26,9): DIF={rt.get('macd_dif',0):.4f} DEA={rt.get('macd_dea',0):.4f} 柱={rt.get('macd_bar',0):.4f}",
                f"RSI: 6={rt.get('rsi_6',0):.2f} 12={rt.get('rsi_12',0):.2f} 24={rt.get('rsi_24',0):.2f}",
                f"MA: 5={rt.get('ma5',0):.2f} 10={rt.get('ma10',0):.2f} 20={rt.get('ma20',0):.2f}",
            ]

            if hist:
                lines.append("")
                lines.append("--- 最近盘后确认值（Tushare 基准，用于对比）---")
                for h in hist[:3]:
                    lines.append(
                        f"  {h.get('trade_date','')}: "
                        f"KDJ(K={h.get('kdj_k',0):.1f}/D={h.get('kdj_d',0):.1f}) | "
                        f"MACD(DIF={h.get('macd_dif',0):.3f}/DEA={h.get('macd_dea',0):.3f})"
                    )

            return "\n".join(lines)
        return json.dumps(result, ensure_ascii=False, indent=2)
    return str(result)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Chat with the trading agent with function calling support.
    """
    settings = get_settings()

    try:
        api_key = settings.get_deepseek_key()
    except EnvironmentError as e:
        raise HTTPException(status_code=500, detail=f"Configuration error: {str(e)}")

    # Build conversation context
    from datetime import datetime as _dt
    now = _dt.now()

    # 判断交易时段
    weekday = now.weekday()
    hour, minute = now.hour, now.minute
    time_minutes = hour * 60 + minute
    morning_start, morning_end = 9 * 60 + 30, 11 * 60 + 30
    afternoon_start, afternoon_end = 13 * 60, 15 * 60

    if weekday >= 5:
        trade_status = '休市（周末）'
    elif time_minutes < morning_start:
        trade_status = '未开盘（集合竞价前）'
    elif morning_start <= time_minutes <= morning_end:
        trade_status = '交易中（上午盘 9:30-11:30）'
    elif time_minutes < afternoon_start:
        trade_status = '午间休市（11:30-13:00）'
    elif afternoon_start <= time_minutes <= afternoon_end:
        trade_status = '交易中（下午盘 13:00-15:00）'
    else:
        trade_status = '已收盘'

    time_context = f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')} ({'周一' if weekday==0 else '周二' if weekday==1 else '周三' if weekday==2 else '周四' if weekday==3 else '周五' if weekday==4 else '周六' if weekday==5 else '周日'})，市场状态: {trade_status}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": time_context},
    ]

    # Add conversation history if provided
    if request.context:
        for ctx in request.context:
            messages.append(ctx)

    # Add current user message
    messages.append({"role": "user", "content": request.message})

    tool_calls_record = []

    try:
        # First call - may return tool calls
        result = await call_deepseek(messages, api_key, tools=TOOLS)
        assistant_message = result["choices"][0]["message"]

        # Handle tool calls
        while "tool_calls" in assistant_message:
            # Add assistant's tool call message
            messages.append(assistant_message)

            # Execute each tool call
            for tool_call in assistant_message["tool_calls"]:
                tool_name = tool_call["function"]["name"]
                tool_args = tool_call["function"].get("arguments", {})

                # Parse arguments if string
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except:
                        tool_args = {}

                try:
                    # Get tool implementation
                    tool_impl = TOOL_IMPLEMENTATIONS.get(tool_name)
                    if tool_impl:
                        if tool_args:
                            api_result = await tool_impl(tool_args)
                        else:
                            api_result = await tool_impl({})
                    else:
                        api_result = {"error": f"Unknown tool: {tool_name}"}
                except Exception as e:
                    api_result = {"error": str(e)}

                # Format result for LLM
                formatted_result = format_result_for_llm(api_result, tool_name)

                # Record tool call
                tool_calls_record.append({
                    "name": tool_name,
                    "args": tool_args,
                    "result": formatted_result,
                })

                # Add tool result message
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": formatted_result,
                })

            # Make next call with tool results
            result = await call_deepseek(messages, api_key, tools=TOOLS)
            assistant_message = result["choices"][0]["message"]

        # Final response
        final_response = assistant_message.get("content", "")

        return ChatResponse(
            response=final_response,
            tool_calls=tool_calls_record if tool_calls_record else None,
            session_id=request.session_id or "default",
            timestamp=datetime.now(),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")


@router.get("/tools")
async def list_tools():
    """
    List available tools for the trading agent.
    """
    return {
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
            }
            for t in TOOLS
        ]
    }


@router.get("/skills")
async def list_skills():
    """
    List available skills for the trading agent.
    """
    return {
        "skills": [
            {
                "name": "market-analysis",
                "description": "分析市场走势、板块热点、指数表现",
                "category": "analysis",
            },
            {
                "name": "stock-research",
                "description": "研究个股基本面和技术面，给出投资建议",
                "category": "analysis",
            },
            {
                "name": "trading-execute",
                "description": "执行股票交易、管理订单",
                "category": "execution",
            },
            {
                "name": "portfolio-review",
                "description": "审视投资组合，评估持仓状况和风险",
                "category": "strategy",
            },
            {
                "name": "news-sentiment",
                "description": "分析财经新闻和市场情绪",
                "category": "analysis",
            },
        ]
    }


class AnalyzeRequest(BaseModel):
    symbol: str


class AnalyzeResponse(BaseModel):
    analysis: str
    symbol: str
    timestamp: datetime


@router.post("/analyze")
async def analyze_stock(request: AnalyzeRequest):
    """
    Get AI analysis for a specific stock symbol (streaming).
    """
    settings = get_settings()

    try:
        api_key = settings.get_deepseek_key()
    except EnvironmentError as e:
        raise HTTPException(status_code=500, detail=f"Configuration error: {str(e)}")

    # Get stock quote first
    quote_data = await call_marcus_api(f"/api/v1/market/quote/{request.symbol}")

    # Build analysis prompt
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"""请分析股票 {request.symbol} 的情况。

当前行情数据：
{json.dumps(quote_data, ensure_ascii=False, indent=2)}

请给出简短的简评，包括：
1. 当前走势判断（强势/弱势/盘整）
2. 关键支撑位和压力位
3. 资金流向判断
4. 操作建议（观望/轻仓/加仓/减仓）

请用简洁专业的语言回复，100字以内。"""},
    ]

    return StreamingResponse(
        stream_deepseek(messages, api_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )