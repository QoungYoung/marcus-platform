# -*- coding: utf-8 -*-
"""
Technical indicator API endpoints.
Fibonacci retracement & daily K-channel (牛股计算器策略).
"""
import logging
from datetime import datetime
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Query

from app.models.indicator import (
    FibonacciRequest, FibonacciLevel, FibonacciResponse,
    DailyChannelResponse,
    TradeAdviceRequest, TradeAdviceResponse,
    CalcPositionRequest, CalcPositionResponse,
    CalcPositionQuantity, CalcPositionStopLoss, CalcPositionValidation,
    EntryCheckRequest, EntryCheckResponse,
    EntryCheckTechDetail, EntryCheckCapitalDetail,
    EntryBuyConfirmation, LayerResult,
)
from app.models.market import RealtimeIndicatorItem, RealtimeIndicatorResponse, QuoteResponse

# ── 安全垫检查模型 ──
from pydantic import BaseModel as PydanticBaseModel
class SafetyMarginResponse(PydanticBaseModel):
    symbol: str
    entry_price: float
    current_price: float
    atr: float
    stop_distance: float        # 止损距离 = max(5%, ATR*1.5)
    intraday_risk: float        # 日内剩余波动风险
    rating: str                 # 安全/偏紧/危险
    rating_description: str
    updated_at: datetime
from app.config import get_settings

router = APIRouter(prefix="/indicator", tags=["Technical Indicators"])

# K 常数（牛股计算器经验参数，约 1.16% 通道宽度）
K_CONSTANT = 0.98848


def _safe_float(v, default: float = 0.0) -> float:
    """将值转为安全的 float，NaN/Inf 统一替换为 default 以确保 JSON 序列化兼容。"""
    import math
    try:
        f = float(v) if v is not None and v != '' else default
    except (ValueError, TypeError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _normalize_to_ts_code(symbol: str) -> str:
    """将各种格式的股票代码转换为 Tushare ts_code 格式"""
    symbol = symbol.strip().upper()
    # 已经是 ts_code 格式
    if symbol.endswith('.SH') or symbol.endswith('.SZ') or symbol.endswith('.BJ'):
        return symbol
    # 带前缀 SH/SZ/BJ
    if symbol.startswith('SH') or symbol.startswith('SZ') or symbol.startswith('BJ'):
        return f"{symbol[2:]}.{symbol[:2]}"
    # 纯数字，判断交易所
    if symbol.isdigit() and len(symbol) == 6:
        if symbol.startswith(('6', '9')):
            return f"{symbol}.SH"
        elif symbol.startswith(('0', '3')):
            return f"{symbol}.SZ"
        elif symbol.startswith(('4', '8')):
            return f"{symbol}.BJ"
    return symbol


def _make_xueqiu_symbol(ts_code: str) -> str:
    """将 Tushare ts_code 转为雪球格式 SH600519 / SZ000001"""
    if '.' in ts_code:
        code, market = ts_code.split('.')
        return f"{market}{code}"
    return ts_code


def _fetch_kline_high_low(ts_code: str, days: int = 90) -> tuple:
    """从 Tushare 获取阶段最高/最低价
    
    Returns:
        (high, low, current_close) 或引发 HTTPException
    """
    try:
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        pro = ts.pro_api(token)

        from datetime import datetime as dt, timedelta
        end_date = dt.now().strftime("%Y%m%d")
        start_date = (dt.now() - timedelta(days=days)).strftime("%Y%m%d")

        df = pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

        if df is None or df.empty:
            raise HTTPException(status_code=404, detail=f"未获取到 {ts_code} 的K线数据")

        df = df.sort_values("trade_date", ascending=True)
        high = float(df["high"].max())
        low = float(df["low"].min())
        current_close = float(df.iloc[-1]["close"])

        return high, low, current_close

    except HTTPException:
        raise
    except ImportError:
        raise HTTPException(status_code=503, detail="Tushare 模块不可用")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取K线数据失败: {str(e)}")


def _get_current_price(xq_symbol: str) -> float:
    """从雪球获取当前实时价格"""
    try:
        settings = get_settings()
        xueqiu_dir = settings.workspace_path / "core"
        xueqiu_config = xueqiu_dir / "config.json"

        import sys as _sys
        if str(xueqiu_dir) not in _sys.path:
            _sys.path.insert(0, str(xueqiu_dir))
        from xueqiu_engine import XueqiuEngine
        engine = XueqiuEngine(config_file=str(xueqiu_config))

        quote = engine.get_stock_quote(xq_symbol)
        if quote and quote.get('current'):
            return float(quote['current'])
    except Exception:
        pass
    return 0.0


def _calculate_position_zone(now: float, f382: float, f618: float, f786: float) -> str:
    """判断当前价格所处的斐波那契区间"""
    # f500 (50% midline) for zone judgment
    f500 = (f382 + f618) / 2

    if now < f786:
        return "深坑/放弃观察（跌破0.786，套牢盘极重）"
    elif now < f618 * 0.99:
        return "弱势区（跌破0.618生死线，趋势转弱）"
    elif now <= f500 * 1.02:
        return "强防生死线（0.5~0.618区间，多空争夺）"
    elif now <= f382 * 1.03:
        return "常规买点区域（0.382附近，强势龙头首阴/浅回踩）"
    else:
        return "高位观望（超过0.382回撤位，追高风险大）"


def _calculate_zone_suggestion(zone: str) -> str:
    """根据区间给出操作建议"""
    if "深坑" in zone:
        return "建议放弃观察，等待重新站上0.786后再考虑"
    elif "弱势" in zone:
        return "观望为主，若持有多单建议减仓或止损"
    elif "生死线" in zone:
        return "观察能否企稳0.618，放量反弹可试探性建仓"
    elif "常规买点" in zone:
        return "右侧交易者可在此区间寻找入场信号"
    else:
        return "不建议追高，等待回踩确认后再入场"


# ──────────────────────── 端点 ────────────────────────


@router.post("/fibonacci", response_model=FibonacciResponse)
async def calculate_fibonacci(req: FibonacciRequest):
    """
    计算斐波那契回撤价位（0.382 / 0.618 / 0.786）。
    
    若未提供 high/low，则自动从近90天K线中提取阶段最高/最低价。
    同时获取实时价格以判断当前所处区间。
    """
    ts_code = _normalize_to_ts_code(req.symbol)
    xq_symbol = _make_xueqiu_symbol(ts_code)

    # 获取或使用提供的 high/low
    if req.high is not None and req.low is not None:
        high, low = req.high, req.low
        current_close = _get_current_price(xq_symbol)
    else:
        high, low, current_close = _fetch_kline_high_low(ts_code)

    if high <= low:
        raise HTTPException(status_code=400, detail=f"阶段顶部({high})必须大于底部({low})")

    diff = high - low

    # 计算三个关键回撤价位
    levels = [
        FibonacciLevel(
            ratio=0.382,
            price=round(high - diff * 0.382, 3),
            label="强势龙头首阴/浅回踩买点",
        ),
        FibonacciLevel(
            ratio=0.618,
            price=round(high - diff * 0.618, 3),
            label="波段多空生死线（跌破则趋势转弱）",
        ),
        FibonacciLevel(
            ratio=0.786,
            price=round(high - diff * 0.786, 3),
            label="深坑/放弃观察（套牢盘极重，大概率A杀）",
        ),
    ]

    # 获取实时价格（优先使用实时报价）
    current_price = _get_current_price(xq_symbol)
    if current_price <= 0:
        current_price = current_close

    zone = _calculate_position_zone(
        current_price, levels[0].price, levels[1].price, levels[2].price
    )

    return FibonacciResponse(
        symbol=ts_code,
        high=round(high, 3),
        low=round(low, 3),
        diff=round(diff, 3),
        current_price=round(current_price, 3),
        levels=levels,
        position_zone=zone,
        zone_suggestion=_calculate_zone_suggestion(zone),
    )


@router.get("/daily-channel/{symbol}", response_model=DailyChannelResponse)
async def calculate_daily_channel(
    symbol: str,
    avg_price: Optional[float] = Query(None, description="分时均价（不传则从行情自动获取）"),
):
    """
    计算日内 K 值通道（压力线 & 支撑线）。
    
    K = 0.98848，基于分时均价计算对称通道：
    - 压力线 = 分时均价 / K
    - 支撑线 = 分时均价 × K
    """
    ts_code = _normalize_to_ts_code(symbol)
    xq_symbol = _make_xueqiu_symbol(ts_code)

    # 获取当前价格
    current_price = _get_current_price(xq_symbol)

    # 获取或计算分时均价
    if avg_price is not None and avg_price > 0:
        avg = avg_price
    else:
        # 尝试从行情数据估算分时均价（约等于 (当前价+昨收)/2 或使用当前价）
        try:
            settings = get_settings()
            xueqiu_dir = settings.workspace_path / "core"
            xueqiu_config = xueqiu_dir / "config.json"

            import sys as _sys
            if str(xueqiu_dir) not in _sys.path:
                _sys.path.insert(0, str(xueqiu_dir))
            from xueqiu_engine import XueqiuEngine
            engine = XueqiuEngine(config_file=str(xueqiu_config))

            quote = engine.get_stock_quote(xq_symbol)
            if quote:
                open_price = float(quote.get('open', 0))
                high_price = float(quote.get('high', 0))
                low_price = float(quote.get('low', 0))
                current = float(quote.get('current', 0))
                # 估算分时均价 = (开盘+最高+最低+当前)/4
                if all([open_price, high_price, low_price, current]):
                    avg = (open_price + high_price + low_price + current) / 4
                elif current > 0:
                    avg = current
                else:
                    raise HTTPException(status_code=400, detail="无法获取分时均价，请手动提供 avg_price 参数")
            else:
                raise HTTPException(status_code=400, detail="无法获取行情数据，请手动提供 avg_price 参数")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取行情数据失败: {str(e)}")

    # 计算 K 值通道
    top_line = round(avg / K_CONSTANT, 3)
    bottom_line = round(avg * K_CONSTANT, 3)
    channel_width_pct = round((top_line - bottom_line) / bottom_line * 100, 2)

    # 判断当前位置
    if current_price > 0:
        if current_price >= top_line:
            position = "突破压力线（强势，但需警惕假突破）"
        elif current_price <= bottom_line:
            position = "跌破支撑线（弱势，考虑止损）"
        else:
            pos_in_channel = round((current_price - bottom_line) / (top_line - bottom_line) * 100, 1) if top_line != bottom_line else 50
            position = f"通道内（{pos_in_channel}%位置）"
    else:
        position = "无法获取实时价格"

    return DailyChannelResponse(
        symbol=ts_code,
        avg_price=round(avg, 3),
        constant_k=K_CONSTANT,
        top_line=top_line,
        bottom_line=bottom_line,
        channel_width_pct=channel_width_pct,
        current_price=round(current_price, 3),
        position=position,
    )


# ──────────────────────── 操作建议端点 ────────────────────────

def _count_trading_days_since(date_str: str) -> int:
    """计算从某日期到今天的交易日数（剔除周末近似）"""
    if not date_str:
        return 0
    try:
        start = datetime.strptime(date_str, "%Y-%m-%d")
        today = datetime.now()
        if start.date() >= today.date():
            return 0
        total_days = (today - start).days
        return max(0, int(total_days * 5 / 7))
    except Exception:
        return 0


@router.post("/advice", response_model=TradeAdviceResponse)
async def get_trade_advice(req: TradeAdviceRequest):
    """
    获取完整的操作建议（牛股计算器决策树）。

    根据是否提供成本价，自动切换：
    - **持仓模式** (cost > 0)：破底止损 → -6%止损 → 时间证伪 → 突破新高 → 持有
    - **观察模式** (cost = 0)：破位严禁 → 放弃极弱 → 跌破618 → 强防生死线 → 常规买点 → 高位观望
    """
    ts_code = _normalize_to_ts_code(req.symbol)
    xq_symbol = _make_xueqiu_symbol(ts_code)

    # ── 1. 获取实时行情 ──
    current_price = _get_current_price(xq_symbol)
    change_pct = 0.0
    stock_name = ""
    try:
        settings = get_settings()
        xueqiu_dir = settings.workspace_path / "core"
        xueqiu_config = xueqiu_dir / "config.json"
        import sys as _sys
        if str(xueqiu_dir) not in _sys.path:
            _sys.path.insert(0, str(xueqiu_dir))
        from xueqiu_engine import XueqiuEngine
        engine = XueqiuEngine(config_file=str(xueqiu_config))
        quote = engine.get_stock_quote(xq_symbol)
        if quote:
            change_pct = float(quote.get('percent', 0))
            stock_name = quote.get('name', '')
    except Exception:
        pass

    # ── 2. 获取阶段高/低点 ──
    if req.high is not None and req.low is not None:
        high, low = req.high, req.low
    else:
        try:
            high, low, _close = _fetch_kline_high_low(ts_code)
        except HTTPException:
            high, low = 0, 0

    if current_price <= 0 and high > 0:
        current_price = _close if '_close' in dir() else 0

    # ── 3. 计算斐波那契价位 ──
    diff = high - low if high > low else 0
    fib_382 = round(high - diff * 0.382, 3) if diff > 0 else 0
    fib_618 = round(high - diff * 0.618, 3) if diff > 0 else 0
    fib_786 = round(high - diff * 0.786, 3) if diff > 0 else 0

    # ── 4. 计算 K 值通道 ──
    if req.avg_price is not None and req.avg_price > 0:
        avg = req.avg_price
    else:
        avg = current_price if current_price > 0 else 0
    k_top = round(avg / K_CONSTANT, 3) if avg > 0 else 0
    k_bottom = round(avg * K_CONSTANT, 3) if avg > 0 else 0
    k_width = round((k_top - k_bottom) / k_bottom * 100, 2) if k_bottom > 0 else 0

    # ── 5. High Water Mark 追踪 ──
    hwm_price = None
    days_since_high = None
    try:
        from core.utils.strategy_chain import StrategyChain
        chain = StrategyChain()
        if current_price > 0:
            chain.update_high_water_mark(ts_code, current_price)
        hwm = chain.get_high_water_mark(ts_code)
        if hwm:
            hwm_price = hwm.get('high_price')
            days_since_high = hwm.get('days_since_high')
    except Exception:
        pass

    # ── 6. 决策树 ──
    cost = req.cost
    signal = ""
    signal_class = ""
    signal_details = []
    risk_flags = []
    mode = "holding" if (cost and cost > 0) else "observing"
    hold_days = None
    is_new_high = False

    # 动态顶部检测（突破新高自动更新）
    if high > 0 and current_price > high:
        is_new_high = True
        high = current_price
        hwm_price = current_price
        days_since_high = 0

    if mode == "holding":
        hold_days = _count_trading_days_since(req.buy_date) if req.buy_date else 0

        # 优先级 1：破底止损（跌破阶段底部 3%）
        if low > 0 and current_price < low * 0.97:
            signal = "破底止损"
            signal_class = "danger"
            signal_details.append(f"当前价 {current_price} 跌破阶段底部 {low} 的3%容错线 ({round(low*0.97,3)})")
            risk_flags.append("破底")

        # 优先级 2：智能成本止损（分级判断，替代一刀切 -6%）
        if not signal and cost and cost > 0:
            max_profit_pct = (
                round((hwm_price - cost) / cost * 100, 2)
                if hwm_price and hwm_price > cost else 0
            )

            # 场景 2a：曾大盈(≥5%) 转亏损 → 保本离场
            if max_profit_pct >= 5 and current_price < cost * 0.99:
                signal = "大盈转亏(-1%)"
                signal_class = "danger"
                signal_details.append(f"曾浮盈 +{max_profit_pct}%（最高 {hwm_price}）→ 现价已跌破成本")
                signal_details.append("赚钱变亏钱是最大错误，建议保本离场")
                risk_flags.append("大盈转亏")

            # 场景 2b：曾小盈(≥3%) 转亏损超 3%
            elif max_profit_pct >= 3 and current_price <= cost * 0.97:
                loss_pct = round((current_price - cost) / cost * 100, 2)
                signal = f"小盈转亏({loss_pct}%)"
                signal_class = "danger"
                signal_details.append(f"曾浮盈 +{max_profit_pct}% → 现亏损超 3% 止损线")
                risk_flags.append("小盈转亏")

            # 场景 2c：从未盈利 → -4% 快速止损
            elif max_profit_pct < 3 and current_price <= cost * 0.96:
                loss_pct = round((current_price - cost) / cost * 100, 2)
                signal = f"止损({loss_pct}%)"
                signal_class = "danger"
                signal_details.append(f"从未盈利，亏损 {loss_pct}% 触及 -4% 快速止损线")
                risk_flags.append("深亏")

            # 场景 2d：无 HWM 数据 → -6% 保守底线
            elif hwm_price is None and current_price <= cost * 0.94:
                loss_pct = round((current_price - cost) / cost * 100, 2)
                signal = f"止损({loss_pct}%)"
                signal_class = "danger"
                signal_details.append(f"当前价已跌破成本价 -6% 保守止损线")
                risk_flags.append("深亏")

        # 优先级 3：时间证伪（13个交易日不创新高）
        if not signal and days_since_high is not None and days_since_high >= 13 and not is_new_high:
            signal = f"时间证伪(>{days_since_high}天)"
            signal_class = "warning"
            signal_details.append(f"已 {days_since_high} 个交易日未创新高（阈值13天）")
            signal_details.append(f"持仓期间最高价：{hwm_price}")
            risk_flags.append("时间证伪")

        # 优先级 4：突破新高
        if not signal and (is_new_high or (current_price == high and high > 0)):
            signal = "突破新高 🏆"
            signal_class = "gold"
            signal_details.append("当前价突破历史最高价，自动重置时间计数")
            risk_flags.append("强势")

        # 默认：持有
        if not signal:
            days_text = f"{hold_days}天" if hold_days else ""
            signal = f"持有({days_text})" if days_text else "持有"
            signal_class = "blue"
            # 附加风险提示
            if days_since_high is not None and days_since_high >= 8:
                signal_details.append(f"⚠️ 已 {days_since_high} 天未创新高，接近时间证伪阈值(13天)")
                signal_class = "cyan"
            if cost and current_price > cost:
                profit_pct = round((current_price - cost) / cost * 100, 2)
                signal_details.append(f"浮盈 {profit_pct}%")

    else:
        # ── 观察模式决策树 ──
        if low > 0 and current_price < low:
            signal = "破位严禁"
            signal_class = "danger"
            signal_details.append(f"当前价 {current_price} 跌破阶段底部 {low}，严禁建仓")
            risk_flags.append("破位")

        elif is_new_high:
            signal = "突破跟进"
            signal_class = "danger"
            signal_details.append("价格突破阶段顶部创新高，激进者可轻仓跟进")
            risk_flags.append("追高")

        elif diff > 0 and low > 0:
            if current_price < fib_786:
                signal = "放弃(极弱)"
                signal_class = "normal"
                signal_details.append(f"当前价低于0.786深坑位 {fib_786}，套牢盘极重")
                risk_flags.append("极弱")

            elif current_price < fib_618 * 0.99:
                signal = "跌破618(弱)"
                signal_class = "warning"
                signal_details.append(f"当前价低于0.618生死线 {fib_618}，趋势转弱")
                risk_flags.append("弱势")

            elif current_price <= (high - diff * 0.5) * 1.02:
                signal = "强防生死线"
                signal_class = "blue"
                signal_details.append(f"当前价在0.5~0.618区间，多空争夺")
                signal_details.append(f"观察能否放量企稳 {fib_618}")

            elif current_price <= fib_382 * 1.03:
                signal = "常规买点"
                signal_class = "cyan"
                signal_details.append(f"当前价接近0.382常规买点 {fib_382}")
                signal_details.append("强势龙头首阴/浅回踩，右侧交易可关注入场信号")

            else:
                signal = "高位观望"
                signal_class = "normal"
                signal_details.append(f"当前价超过0.382回撤位，追高风险大")
                risk_flags.append("高位")

        else:
            signal = "观望"
            signal_class = "normal"
            signal_details.append("缺少阶段高低点数据，无法判断")

    # ── 7. 构建响应 ──
    return TradeAdviceResponse(
        symbol=ts_code,
        name=stock_name,
        current_price=round(current_price, 3),
        change_pct=round(change_pct, 2),
        mode=mode,
        fib_382=fib_382,
        fib_618=fib_618,
        fib_786=fib_786,
        k_channel_top=k_top,
        k_channel_bottom=k_bottom,
        k_channel_width_pct=k_width,
        cost=round(cost, 3) if cost else None,
        hold_days=hold_days,
        days_since_high=days_since_high,
        high_water_mark=round(hwm_price, 3) if hwm_price else None,
        signal=signal,
        signal_class=signal_class,
        signal_details=signal_details,
        risk_flags=risk_flags,
    )


# ──────────────────────── 盘中实时技术指标 ────────────────────────

# core/ is already on sys.path from the top of this file (as _sys.path via _core_dir)
# Re-confirm it's there for the realtime endpoint


@router.get("/realtime/{symbol}", response_model=RealtimeIndicatorResponse)
async def get_realtime_indicators(
    symbol: str,
    limit: int = Query(3, ge=1, le=5, description="返回最近N日盘后确认指标作为基准"),
):
    """
    获取个股盘中实时估算技术指标（KDJ/MACD/RSI/MA）。

    数据来源：
    - 腾讯 qt.gtimg.cn 实时 OHLCV（当前价/最高/最低/开盘/昨收）
    - Tushare daily 历史日K线（未复权，≥35条）
    - Tushare stk_factor_pro 前日盘后确认指标（KDJ/MACD锚点）

    返回：
    - realtime: 盘中估算值（data_source='intraday_estimate'，⚠️ 未收盘确认）
    - historical: 最近N日 Tushare 盘后确认指标（data_source='daily_confirmed'）

    可靠性：
    - 盘中估算仅作辅助参考，不能作为独立建仓的唯一理由
    - 决策应基于 historical 中的盘后确认数据 + 当日行情综合判断
    """
    ts_code = _normalize_to_ts_code(symbol)
    xq_symbol = _make_xueqiu_symbol(ts_code)

    settings = get_settings()
    token = settings.get_tushare_token()
    xueqiu_config = str(settings.workspace_path / "core" / "config.json")

    import asyncio

    # ── 并行获取所有数据源 ──

    # 1. 腾讯实时行情
    async def _fetch_realtime_quote():
        try:
            from xueqiu_engine import XueqiuEngine
            engine = XueqiuEngine(config_file=xueqiu_config)
            return engine.get_stock_quote(xq_symbol)
        except Exception as e:
            logger.warning(f"腾讯实时行情获取失败: {e}")
            return None

    # 2. Tushare 历史日K线（≥35条，用于 MACD 初始化 + 滚动窗口）
    async def _fetch_daily_bars():
        try:
            import tushare as ts
            from datetime import datetime as dt, timedelta
            pro = ts.pro_api(token)
            end_d = dt.now().strftime("%Y%m%d")
            start_d = (dt.now() - timedelta(days=60)).strftime("%Y%m%d")
            df = pro.daily(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=35)
            if df is not None and not df.empty:
                return df.sort_values("trade_date", ascending=True)
        except Exception as e:
            logger.warning(f"Tushare daily 获取失败: {e}")
        return None

    # 3. Tushare stk_factor_pro 最近 5 条（用于构建 PrevIndicators 锚点）
    async def _fetch_stk_factor():
        try:
            import tushare as ts
            from datetime import datetime as dt, timedelta
            pro = ts.pro_api(token)
            end_d = dt.now().strftime("%Y%m%d")
            start_d = (dt.now() - timedelta(days=35)).strftime("%Y%m%d")
            df = pro.stk_factor_pro(
                ts_code=ts_code,
                start_date=start_d,
                end_date=end_d,
                fields='ts_code,trade_date,close,macd_qfq,macd_dif_qfq,macd_dea_qfq,'
                       'kdj_qfq,kdj_k_qfq,kdj_d_qfq,'
                       'rsi_qfq_6,rsi_qfq_12,rsi_qfq_24,'
                       'boll_upper_qfq,boll_mid_qfq,boll_lower_qfq,atr_qfq,cci_qfq,wr_qfq',
            )
            if df is not None and not df.empty:
                return df.sort_values("trade_date", ascending=True)
        except Exception as e:
            logger.warning(f"Tushare stk_factor_pro 获取失败: {e}")
        return None

    quote, daily_df, factor_df = await asyncio.gather(
        _fetch_realtime_quote(),
        _fetch_daily_bars(),
        _fetch_stk_factor(),
    )

    # ── 构建 historical（最近 N 日盘后确认指标）──
    historical: list = []
    prev_indicators = None

    if factor_df is not None and not factor_df.empty:
        from app.models.market import TechnicalData
        factor_desc = factor_df.sort_values("trade_date", ascending=False)
        for _, row in factor_desc.head(limit).iterrows():
            historical.append(TechnicalData(
                ts_code=str(row["ts_code"]),
                trade_date=str(row["trade_date"]),
                close=float(row.get("close", 0) or 0),
                macd=float(row.get("macd_qfq", 0) or 0),
                macd_dif=float(row.get("macd_dif_qfq", 0) or 0),
                macd_dea=float(row.get("macd_dea_qfq", 0) or 0),
                kdj=float(row.get("kdj_qfq", 0) or 0),
                kdj_k=float(row.get("kdj_k_qfq", 0) or 0),
                kdj_d=float(row.get("kdj_d_qfq", 0) or 0),
                rsi_6=float(row.get("rsi_qfq_6", 0) or 0),
                rsi_12=float(row.get("rsi_qfq_12", 0) or 0),
                rsi_24=float(row.get("rsi_qfq_24", 0) or 0),
                boll_upper=float(row.get("boll_upper_qfq", 0) or 0),
                boll_mid=float(row.get("boll_mid_qfq", 0) or 0),
                boll_lower=float(row.get("boll_lower_qfq", 0) or 0),
                atr=float(row.get("atr_qfq", 0) or 0),
                cci=float(row.get("cci_qfq", 0) or 0),
                wr=float(row.get("wr_qfq", 0) or 0),
            ))

        # 提取最新一条作为 PrevIndicators 种子
        if len(factor_desc) > 0:
            latest_tech = factor_desc.iloc[0]
            prev_close = float(latest_tech.get("close", 0) or 0) or 0
            prev_kdj_k = float(latest_tech.get("kdj_k_qfq", 0) or 0) or 50.0
            prev_kdj_d = float(latest_tech.get("kdj_d_qfq", 0) or 0) or 50.0
            prev_macd_dif = float(latest_tech.get("macd_dif_qfq", 0) or 0) or 0.0
            prev_macd_dea = float(latest_tech.get("macd_dea_qfq", 0) or 0) or 0.0

            from core.realtime_indicators import PrevIndicators
            # Reconstruct ema12/ema26 from DIF and close:
            # DIF = ema12 - ema26, and roughly ema26 ≈ close (当日)
            # So ema12 ≈ close + DIF, ema26 ≈ close
            prev_indicators = PrevIndicators(
                trade_date=str(latest_tech["trade_date"]),
                kdj_k=prev_kdj_k,
                kdj_d=prev_kdj_d,
                macd_dea=prev_macd_dea,
                macd_ema12=prev_close + prev_macd_dif if prev_close > 0 and prev_macd_dif else 0.0,
                macd_ema26=prev_close if prev_close > 0 else 0.0,
            )

    # ── 计算实时指标 ──
    warning_msg = ""
    realtime = None
    stock_name = ""

    if quote:
        stock_name = quote.get("name", "")
    else:
        warning_msg = "⚠️ 腾讯实时行情不可用，无法计算盘中实时指标。以下仅为最近盘后确认数据。"

    if quote and daily_df is not None and not daily_df.empty:
        from core.realtime_indicators import DailyBar, calculate_realtime_indicators

        bars = [
            DailyBar(
                trade_date=str(r["trade_date"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                vol=float(r["vol"]),
            )
            for _, r in daily_df.iterrows()
        ]

        result = calculate_realtime_indicators(
            symbol=ts_code,
            realtime_quote=quote,
            historical_bars=bars,
            prev_indicators=prev_indicators,
        )

        warning_msg = result.warning

        realtime = RealtimeIndicatorItem(
            symbol=ts_code,
            current_price=result.current_price,
            data_source=result.data_source,
            calc_time=result.calc_time,
            prev_trade_date=result.prev_trade_date,
            used_prev_indicators=result.used_prev_indicators,
            kdj_k=result.kdj_k,
            kdj_d=result.kdj_d,
            kdj_j=result.kdj_j,
            macd_dif=result.macd_dif,
            macd_dea=result.macd_dea,
            macd_bar=result.macd_bar,
            rsi_6=result.rsi_6,
            rsi_12=result.rsi_12,
            rsi_24=result.rsi_24,
            ma5=result.ma5,
            ma10=result.ma10,
            ma20=result.ma20,
            warning=result.warning,
        )
    elif quote and (daily_df is None or daily_df.empty):
        warning_msg = "⚠️ Tushare 历史K线不可用，无法计算实时指标。请稍后重试。"

    return RealtimeIndicatorResponse(
        symbol=ts_code,
        name=stock_name,
        realtime=realtime,
        historical=historical,
        warning=warning_msg,
        updated_at=datetime.now(),
    )


# ──────────────────────── 建仓前安全垫检查 ────────────────────────

@router.get("/safety-margin/{symbol}", response_model=SafetyMarginResponse)
async def check_safety_margin(
    symbol: str,
    entry_price: float = Query(..., gt=0, description="计划建仓价格"),
):
    """
    建仓前安全垫检查：判断建仓价位是否会在日内被正常波动击穿止损。

    计算逻辑：
    - 止损距离 = entry_price * max(5%, ATR × 1.5)
    - 日内剩余波动风险 = ATR × sqrt(剩余交易分钟 / 240)
    - 安全垫评级 = 止损距离 / 日内剩余波动风险

    评级标准：
    - 评级 > 2 → 安全（止损距离是日内波动的 2 倍以上）
    - 评级 1-2 → 偏紧（日内波动可能触及止损）
    - 评级 < 1 → 危险（正常日内波动就能击穿止损，禁止建仓）
    """
    ts_code = _normalize_to_ts_code(symbol)
    xq_symbol = _make_xueqiu_symbol(ts_code)

    # ── 1. 获取当前价格 ──
    current_price = _get_current_price(xq_symbol)
    if current_price <= 0:
        current_price = entry_price  # fallback

    # ── 2. 获取 ATR ──
    atr = 0.0
    try:
        settings = get_settings()
        token = settings.get_tushare_token()
        import tushare as ts
        from datetime import datetime as dt, timedelta
        pro = ts.pro_api(token)
        end_d = dt.now().strftime("%Y%m%d")
        start_d = (dt.now() - timedelta(days=30)).strftime("%Y%m%d")
        df = pro.stk_factor_pro(
            ts_code=ts_code,
            start_date=start_d,
            end_date=end_d,
            fields='ts_code,trade_date,atr_qfq',
            limit=1,
        )
        if df is not None and not df.empty:
            atr = float(df.iloc[0].get("atr_qfq", 0) or 0)
    except Exception:
        pass

    if atr <= 0:
        # 无法获取 ATR 时使用保守估算：当前价 × 3%
        atr = current_price * 0.03

    # ── 3. 计算安全垫 ──
    stop_distance = entry_price * max(0.05, (atr / entry_price) * 1.5 if entry_price > 0 else 0.05)

    # 日内剩余波动风险
    import math
    now = datetime.now()
    total_trading_minutes = 240.0  # 9:30-11:30 (120) + 13:00-15:00 (120)
    current_minutes = now.hour * 60 + now.minute
    morning_start, morning_end = 9 * 60 + 30, 11 * 60 + 30
    afternoon_start, afternoon_end = 13 * 60, 15 * 60

    elapsed = 0.0
    if current_minutes >= afternoon_end:
        elapsed = total_trading_minutes
    elif current_minutes >= afternoon_start:
        elapsed = 120.0 + (current_minutes - afternoon_start)
    elif current_minutes >= morning_end:
        elapsed = 120.0
    elif current_minutes >= morning_start:
        elapsed = current_minutes - morning_start

    remaining_minutes = max(5.0, total_trading_minutes - elapsed)
    intraday_risk = atr * math.sqrt(remaining_minutes / total_trading_minutes)

    # 评级
    if intraday_risk > 0:
        ratio = stop_distance / intraday_risk
    else:
        ratio = 999.0

    if ratio > 2.0:
        rating = "安全"
        rating_desc = f"止损距离({stop_distance:.2f})为日内波动({intraday_risk:.2f})的{ratio:.1f}倍，建仓安全垫充足"
    elif ratio >= 1.0:
        rating = "偏紧"
        rating_desc = f"止损距离({stop_distance:.2f})仅{ratio:.1f}倍于日内波动({intraday_risk:.2f})，建议降仓至≤5%试探仓"
    else:
        rating = "危险"
        rating_desc = f"日内正常波动({intraday_risk:.2f})即可击穿止损({stop_distance:.2f})，建议放弃建仓"

        return SafetyMarginResponse(
        symbol=ts_code,
        entry_price=round(entry_price, 3),
        current_price=round(current_price, 3),
        atr=round(atr, 3),
        stop_distance=round(stop_distance, 3),
        intraday_risk=round(intraday_risk, 3),
        rating=rating,
        rating_description=rating_desc,
        updated_at=datetime.now(),
    )


# ──────────────────────── 仓位计算 ────────────────────────

def _get_single_stock_cap(strength: str) -> float:
    """信号强度 → 单票上限%"""
    return {"low": 10.0, "medium": 18.0, "high": 25.0}.get(strength, 18.0)


def _get_role_cap(role: str) -> float:
    """产业链角色 → 环节上限%"""
    return {"upstream": 15.0, "mid": 10.0, "downstream": 5.0}.get(role, 10.0)


def _get_total_cap(stance: str) -> float:
    """市场立场 → 总仓上限%"""
    return {"green": 60.0, "yellow": 50.0, "red": 20.0}.get(stance, 50.0)


def _get_tier_condition(tier: str) -> str:
    """加仓层级 → 前仓条件描述"""
    return {
        "probe": "无（首仓试探）",
        "confirm": "浮盈 ≥ 1%",
        "sprint": "浮盈 ≥ 3%",
    }.get(tier, "无")


def _get_tier_profit_threshold(tier: str) -> float:
    """加仓层级 → 所需浮盈阈值(%)"""
    return {"probe": 0.0, "confirm": 1.0, "sprint": 3.0}.get(tier, 0.0)


def _calculate_amplitude_from_kline(klines: list) -> float:
    """从K线数据计算近N日日均振幅。
    
    振幅 = (最高 - 最低) / 收盘价 * 100
    """
    if not klines:
        return 0.0
    amplitudes = []
    for k in klines:
        if k.close > 0:
            amp = (k.high - k.low) / k.close * 100
            amplitudes.append(amp)
    if not amplitudes:
        return 0.0
    return round(sum(amplitudes) / len(amplitudes), 2)


def _get_amplitude_tier(amplitude: float) -> str:
    """振幅 → 档位"""
    if amplitude < 3.0:
        return "低波"
    elif amplitude <= 6.0:
        return "中波"
    else:
        return "高波"


def _get_dynamic_stop_pct(index_pct: float, amplitude: float = 0.0) -> float:
    """大盘涨跌幅 + 振幅 → 动态止损率(%)
    
    动态止损率 = max(f(大盘涨跌), 近5日日均振幅 × 0.4)
    - 大盘感知保证系统性风险收紧
    - 振幅因子保证高波动个股不会被窄止损频繁击穿
    """
    # 大盘感知基本阈值
    if index_pct < -2.0:
        market_rate = 1.5
    elif index_pct <= 1.0:
        market_rate = 2.0
    elif index_pct <= 2.0:
        market_rate = 3.0
    else:
        market_rate = 4.0
    
    # 振幅因子：至少 40% 的日均振幅作为止损空间
    amp_rate = amplitude * 0.4 if amplitude > 0 else 0.0
    
    return round(max(market_rate, amp_rate), 2)


def _get_iron_rule2(amplitude_tier: str) -> dict:
    """根据振幅档位返回铁律二的各级保护线（统一版本 v2.0）。

    铁律二：盈利单不能变亏损（移动止盈保护）。
    不同振幅档位下触发阈值不同：
    - 低波（<3%）：浮动小，阈值紧凑
    - 中波（3-6%）：标准阈值
    - 高波（>6%）：放宽阈值，避免被震出
    
    统一表：
    | 波动档 | T1:浮盈→保本 | T2:浮盈→成本+X% | T3:浮盈→成本+Y% |
    |:------:|:-----------:|:--------------:|:--------------:|
    | 低波<3% | ≥1%        | ≥3%→+1%        | ≥5%→+2%        |
    | 中波3-6%| ≥2%        | ≥5%→+2%        | ≥8%→+4%        |
    | 高波>6% | ≥3%        | ≥7%→+3%        | ≥10%→+5%       |
    """
    if amplitude_tier == "低波":
        return {
            "t1_pct": 1.0, "t1_desc": "浮盈≥1%→成本价",
            "t2_pct": 3.0, "t2_plus_pct": 1.0, "t2_desc": "浮盈≥3%→成本价+1%",
            "t3_pct": 5.0, "t3_plus_pct": 2.0, "t3_desc": "浮盈≥5%→成本价+2%",
        }
    elif amplitude_tier == "中波":
        return {
            "t1_pct": 2.0, "t1_desc": "浮盈≥2%→成本价",
            "t2_pct": 5.0, "t2_plus_pct": 2.0, "t2_desc": "浮盈≥5%→成本价+2%",
            "t3_pct": 8.0, "t3_plus_pct": 4.0, "t3_desc": "浮盈≥8%→成本价+4%",
        }
    else:  # 高波
        return {
            "t1_pct": 3.0, "t1_desc": "浮盈≥3%→成本价",
            "t2_pct": 7.0, "t2_plus_pct": 3.0, "t2_desc": "浮盈≥7%→成本价+3%",
            "t3_pct": 10.0, "t3_plus_pct": 5.0, "t3_desc": "浮盈≥10%→成本价+5%",
        }


@router.post("/calc-position", response_model=CalcPositionResponse)
async def calc_position(req: CalcPositionRequest):
    """
    仓位计算工具 — 根据信号强度、产业链角色、加仓层级、市场立场，
    综合计算建议仓位数量、止损价位和风险验证。

    内部自动获取：
    - get_portfolio() → 总资产、可用资金、持仓市值
    - get_quote(symbol) → 当前价格
    - get_daily_kline(limit=5) → 近5日日均振幅
    - get_market_indices() → 大盘涨跌幅（动态止损依据）
    - get_technical(limit=5) → ATR 交叉验证（可选）
    """
    symbol = req.symbol.strip().upper()
    ts_code = _normalize_to_ts_code(symbol)
    xq_symbol = _make_xueqiu_symbol(ts_code)

    # ── Layer 1: 获取基础数据 ──
    warnings = []

    # 1a. 获取账户信息
    from app.api.portfolio import calculate_positions_from_db
    try:
        position_list, account = calculate_positions_from_db()
        available_cash = account.get("available_cash", 0)
        initial_capital = account.get("initial_capital", 1000000)
        # 计算当前持仓市值
        position_value = 0.0
        for p in position_list:
            try:
                price_data = _get_current_price(p["symbol"])
                if price_data <= 0:
                    price_data = p["avg_price"]
                position_value += p["volume"] * price_data
            except Exception:
                position_value += p["volume"] * p["avg_price"]
        total_asset = available_cash + account.get("frozen_cash", 0) + position_value
        position_ratio = round(position_value / total_asset * 100, 2) if total_asset > 0 else 0
    except Exception as e:
        logger.warning(f"获取账户信息失败: {e}")
        total_asset = 1000000.0
        available_cash = 1000000.0
        position_value = 0.0
        position_ratio = 0.0
        warnings.append(f"⚠️ 账户数据不可用，使用默认值(总资产={total_asset})")

    # 1b. 获取当前价格
    current_price = _get_current_price(xq_symbol)
    stock_name = ""
    change_pct = 0.0
    try:
        settings = get_settings()
        xueqiu_dir = settings.workspace_path / "core"
        xueqiu_config = xueqiu_dir / "config.json"
        import sys as _sys
        if str(xueqiu_dir) not in _sys.path:
            _sys.path.insert(0, str(xueqiu_dir))
        from xueqiu_engine import XueqiuEngine
        engine = XueqiuEngine(config_file=str(xueqiu_config))
        quote = engine.get_stock_quote(xq_symbol)
        if quote:
            current_price = float(quote.get("current", current_price))
            stock_name = quote.get("name", "")
            change_pct = float(quote.get("percent", 0))
    except Exception as e:
        logger.warning(f"获取行情失败: {e}")

    if current_price <= 0:
        raise HTTPException(status_code=400, detail=f"无法获取 {symbol} 的实时价格")

    # 1c. 获取近5日K线计算振幅
    amplitude = 0.0
    try:
        from app.config import get_settings
        from datetime import datetime, timedelta
        settings = get_settings()
        token = settings.get_tushare_token()
        if token:
            import tushare as ts
            pro = ts.pro_api(token)
            end_d = datetime.now().strftime("%Y%m%d")
            start_d = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
            df = pro.daily(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=5)
            if df is not None and not df.empty:
                klines = [
                    {"close": float(r.get("close", 0) or 0),
                     "high": float(r.get("high", 0) or 0),
                     "low": float(r.get("low", 0) or 0)}
                    for _, r in df.iterrows()
                ]
                amplitude = _calculate_amplitude_from_kline([type("K", (), k) for k in klines])
    except Exception as e:
        logger.warning(f"获取K线振幅失败: {e}")
        warnings.append("⚠️ K线振幅数据不可用，使用默认振幅0%")

    # 1d. 获取大盘指数涨跌幅
    index_pct = 0.0
    try:
        from app.api.market import get_market_indices as _get_indices
        indices_response = await _get_indices()
        indices = indices_response.indices if hasattr(indices_response, 'indices') else []
        # 取上证指数涨跌幅为主要参考
        for idx in indices:
            if hasattr(idx, 'symbol') and '000001' in idx.symbol:
                index_pct = idx.change_pct
                break
        if index_pct == 0.0 and indices:
            index_pct = indices[0].change_pct
    except Exception as e:
        logger.warning(f"获取大盘指数失败: {e}")
        warnings.append("⚠️ 大盘指数数据不可用，使用默认涨跌幅0%")

    # ── Layer 2: 加载约束条件 ──
    single_cap_pct = _get_single_stock_cap(req.signal_strength)
    role_cap_pct = _get_role_cap(req.chain_role)
    total_cap_pct = _get_total_cap(req.stance)
    tier_condition = _get_tier_condition(req.tier)
    amplitude_tier = _get_amplitude_tier(amplitude)
    dynamic_stop_pct = _get_dynamic_stop_pct(index_pct, amplitude)

    # ── Layer 3: 计算数量 ──
    effective_single_cap = min(single_cap_pct, role_cap_pct) / 100.0 * total_asset
    total_remaining = total_cap_pct / 100.0 * total_asset - position_value
    cash_reserve_line = total_asset * 0.25
    cash_available_for_buy = available_cash - cash_reserve_line
    max_usable = min(effective_single_cap, max(total_remaining, 0), max(cash_available_for_buy, 0))

    # 手数计算（A股100股/手）
    def _round_lot(shares: float) -> int:
        return max(0, int(shares // 100) * 100)

    max_shares = _round_lot(max_usable / current_price)
    max_amount = round(max_shares * current_price, 2)

    rec_amount_raw = min(role_cap_pct / 100.0 * total_asset, max_usable)
    rec_shares = _round_lot(rec_amount_raw / current_price)
    rec_amount = round(rec_shares * current_price, 2)
    rec_pct = round(rec_amount / total_asset * 100, 2) if total_asset > 0 else 0

    probe_amount_raw = min(0.10 * total_asset, max_usable)
    probe_shares = _round_lot(probe_amount_raw / current_price)
    probe_amount = round(probe_shares * current_price, 2)
    probe_pct = round(probe_amount / total_asset * 100, 2) if total_asset > 0 else 0

    # ── Layer 4: 计算止损 ──
    hard_stop_price = round(current_price * (1 - dynamic_stop_pct / 100.0), 3)
    max_loss_per_share = round(current_price - hard_stop_price, 3)
    total_max_loss = round(rec_shares * max_loss_per_share, 2)

    iron_rule = _get_iron_rule2(amplitude_tier)

    # ── Layer 5: 逐条验证 ──
    validation_checks = []

    # 单票上限
    single_cap_actual_pct = round(rec_amount / total_asset * 100, 2) if total_asset > 0 else 0
    single_cap_ok = rec_amount <= effective_single_cap
    single_cap_detail = f"建议{rec_amount}({single_cap_actual_pct}%) ≤ 上限{round(effective_single_cap,2)}({min(single_cap_pct, role_cap_pct)}%)"

    # 总仓位
    new_total_position = position_value + rec_amount
    new_total_ratio = round(new_total_position / total_asset * 100, 2) if total_asset > 0 else 0
    total_position_ok = new_total_position <= total_cap_pct / 100.0 * total_asset
    total_position_detail = f"建仓后{new_total_ratio}% ≤ 上限{total_cap_pct}%"

    # 现金底线
    new_cash = available_cash - rec_amount
    new_cash_ratio = round(new_cash / total_asset * 100, 2) if total_asset > 0 else 0
    cash_reserve_ok = new_cash >= cash_reserve_line
    cash_reserve_detail = f"建仓后现金{new_cash_ratio}% ≥ 底线25%"

    # 单笔亏损
    loss_ratio = round(total_max_loss / total_asset * 100, 2) if total_asset > 0 else 0
    max_loss_ok = total_max_loss <= total_asset * 0.02
    max_loss_detail = f"单笔亏损{total_max_loss}({loss_ratio}%) ≤ 上限{round(total_asset*0.02,2)}(2%)"

    # 前仓条件（仅 confirm/sprint 层级需要检查已有持仓浮盈）
    pre_condition_ok = None
    pre_condition_detail = None
    threshold = _get_tier_profit_threshold(req.tier)
    if threshold > 0:
        # 检查是否已有该股持仓
        existing_profit = None
        try:
            for p in position_list:
                p_symbol = p.get("symbol", "")
                # 匹配符号（兼容不同格式）
                if symbol.replace("SH", "").replace("SZ", "").replace("BJ", "") == \
                   p_symbol.replace("SH", "").replace("SZ", "").replace("BJ", ""):
                    avg_price = p.get("avg_price", 0)
                    existing_profit = round((current_price - avg_price) / avg_price * 100, 2)
                    break
        except Exception:
            pass

        if existing_profit is None:
            pre_condition_ok = False
            pre_condition_detail = f"无该股持仓，{req.tier}层级需要已有持仓且浮盈≥{threshold}%"
            warnings.append(f"⚠️ 前仓条件不满足: {req.tier}层级需要已有该股持仓且浮盈≥{threshold}%")
        elif existing_profit < threshold:
            pre_condition_ok = False
            pre_condition_detail = f"当前浮盈{existing_profit}% < 所需{threshold}%"
            warnings.append(f"⚠️ 前仓条件不满足: 当前浮盈{existing_profit}% < 所需{threshold}%")
        else:
            pre_condition_ok = True
            pre_condition_detail = f"当前浮盈{existing_profit}% ≥ 所需{threshold}%"

    # 汇总验证
    all_pass = single_cap_ok and total_position_ok and cash_reserve_ok and max_loss_ok
    if pre_condition_ok is not None and not pre_condition_ok:
        all_pass = False

    validation = CalcPositionValidation(
        single_cap_ok=single_cap_ok,
        single_cap_detail=single_cap_detail,
        total_position_ok=total_position_ok,
        total_position_detail=total_position_detail,
        cash_reserve_ok=cash_reserve_ok,
        cash_reserve_detail=cash_reserve_detail,
        max_loss_ok=max_loss_ok,
        max_loss_detail=max_loss_detail,
        pre_condition_ok=pre_condition_ok,
        pre_condition_detail=pre_condition_detail,
    )

    # 构建降级建议
    if not all_pass:
        suggestions = []
        if not single_cap_ok:
            cap_shares = _round_lot(effective_single_cap / current_price)
            suggestions.append(f"单票超标→降为{cap_shares}股")
        if not total_position_ok:
            remaining_shares = _round_lot(max(total_remaining, 0) / current_price)
            suggestions.append(f"总仓超标→最多再买{remaining_shares}股")
        if not cash_reserve_ok:
            max_for_cash = _round_lot(max(cash_available_for_buy, 0) / current_price)
            suggestions.append(f"现金不足→最多买{max_for_cash}股")
        if not max_loss_ok:
            suggestions.append(f"亏损超标→减少数量至亏损≤{round(total_asset*0.02,2)}")
        if suggestions:
            warnings.insert(0, f"🔴 验证不通过: {'; '.join(suggestions)}")

    quantity = CalcPositionQuantity(
        max_shares=max_shares,
        max_amount=max_amount,
        rec_shares=rec_shares,
        rec_amount=rec_amount,
        rec_pct=rec_pct,
        probe_shares=probe_shares,
        probe_amount=probe_amount,
        probe_pct=probe_pct,
    )

    stop_loss = CalcPositionStopLoss(
        volatility_tier=amplitude_tier,
        dynamic_stop_pct=dynamic_stop_pct,
        hard_stop_price=hard_stop_price,
        max_loss_per_share=max_loss_per_share,
        total_max_loss=total_max_loss,
        iron_rule2_t1_pct=iron_rule["t1_pct"],
        iron_rule2_t2_pct=iron_rule["t2_pct"],
        iron_rule2_t2_plus_pct=iron_rule["t2_plus_pct"],
        iron_rule2_t3_pct=iron_rule["t3_pct"],
        iron_rule2_t3_plus_pct=iron_rule["t3_plus_pct"],
    )

    return CalcPositionResponse(
        symbol=ts_code,
        name=stock_name,
        total_asset=round(total_asset, 2),
        available_cash=round(available_cash, 2),
        position_value=round(position_value, 2),
        position_ratio=position_ratio,
        signal_strength=req.signal_strength,
        single_stock_cap_pct=single_cap_pct,
        chain_role=req.chain_role,
        role_cap_pct=role_cap_pct,
        tier=req.tier,
        tier_condition=tier_condition,
        stance=req.stance,
        total_cap_pct=total_cap_pct,
        amplitude=amplitude,
        amplitude_tier=amplitude_tier,
        index_pct=round(index_pct, 2),
        current_price=round(current_price, 3),
        quantity=quantity,
        stop_loss=stop_loss,
        validation=validation,
        warnings=warnings,
        all_pass=all_pass,
    )


# ──────────────────────── 入场过滤三层检查 ────────────────────────

def _check_macd_dif_converging(indicators_data: dict) -> bool:
    """检查 MACD DIF 是否连续2日收敛（DIF 绝对值缩小）。
    
    从 historical 数据中取最近2日的 MACD DIF 进行比较。
    """
    historical = indicators_data.get("historical", [])
    if len(historical) < 2:
        return False
    h0 = historical[0]  # 最近一日（最新）
    h1 = historical[1]  # 前一日
    dif0 = abs(getattr(h0, "macd_dif", 0) or 0)
    dif1 = abs(getattr(h1, "macd_dif", 0) or 0)
    return dif0 < dif1


@router.post("/check-entry-filters", response_model=EntryCheckResponse)
async def check_entry_filters(req: EntryCheckRequest):
    """
    入场过滤三层检查 — 对建仓计划表中的每只标的执行技术面、主力行为、超买过滤。

    内部自动获取：
    - get_realtime_indicators(symbol) → MA5/MA20/MACD/RSI6/KDJ-J
    - get_quote(symbol) → 价格/涨幅/分时均价/RSR/日内分位
    - get_moneyflow(symbol) → 今日/5日/10日主力资金流向
    """
    ts_code = _normalize_to_ts_code(req.symbol)
    xq_symbol = _make_xueqiu_symbol(ts_code)
    settings = get_settings()

    # ══════════════════════════════════════
    # Stage 1: 并行获取所有数据源
    # ══════════════════════════════════════
    import asyncio

    # 1a. 获取行情价格
    quote_data = {}
    stock_name = ""
    change_pct = 0.0
    turnover_rate = 0.0
    amplitude_val = 0.0
    current_price = 0.0
    rsr = None
    intraday_percentile = None
    avg_price = None
    volume_ratio = req.volume_ratio

    try:
        xueqiu_config = str(settings.workspace_path / "core" / "config.json")
        import sys as _sys
        if str(settings.workspace_path / "core") not in _sys.path:
            _sys.path.insert(0, str(settings.workspace_path / "core"))
        from xueqiu_engine import XueqiuEngine
        engine = XueqiuEngine(config_file=xueqiu_config)
        quote = engine.get_stock_quote(xq_symbol)
        if quote:
            current_price = float(quote.get("current", 0))
            stock_name = quote.get("name", "")
            change_pct = float(quote.get("percent", 0))
            turnover_rate = float(quote.get("turnover_rate", 0) or 0)
            amplitude_val = float(quote.get("amplitude", 0) or 0)
            rsr = quote.get("rsr")
            intraday_percentile = quote.get("intraday_percentile")
            avg_price = quote.get("avg_price")
            # 估算量比（volume / avg_volume，简化用换手率参照）
            if volume_ratio is None and turnover_rate > 0:
                # 粗略估算：量比 ≈ 换手率 / 历史日均换手率(约2%)
                volume_ratio = round(turnover_rate / 2.0, 2) if turnover_rate > 0 else None
    except Exception as e:
        logger.warning(f"获取行情失败: {e}")

    if current_price <= 0:
        raise HTTPException(status_code=400, detail=f"无法获取 {req.symbol} 的实时价格")

    # 1b. 获取实时技术指标 (MA5/MA20/MACD/KDJ/RSI)
    indicators_data = {"realtime": None, "historical": []}
    try:
        from app.api.indicator import get_realtime_indicators as _get_rt
        rt_response = await _get_rt(symbol=req.symbol, limit=3)
        indicators_data["realtime"] = rt_response.realtime
        indicators_data["historical"] = rt_response.historical
    except Exception as e:
        logger.warning(f"获取实时指标失败: {e}")

    # 1c. 获取资金流向
    moneyflow_data = None
    try:
        from app.api.market import get_stock_moneyflow as _get_mf
        mf_response = await _get_mf(symbol=req.symbol)
        moneyflow_data = mf_response
    except Exception as e:
        logger.warning(f"获取资金流向失败: {e}")

    # ══════════════════════════════════════
    # Stage 2: 提取指标值
    # ══════════════════════════════════════
    rt = indicators_data.get("realtime")
    ma5 = getattr(rt, "ma5", 0) or 0 if rt else 0
    ma20 = getattr(rt, "ma20", 0) or 0 if rt else 0
    rsi6 = getattr(rt, "rsi_6", 0) or 0 if rt else 0
    kdj_j = getattr(rt, "kdj_j", 0) or 0 if rt else 0

    # MACD 状态判断
    macd_status = "未知"
    macd_dif_converging = False
    if rt:
        macd_dif = getattr(rt, "macd_dif", 0) or 0
        macd_dea = getattr(rt, "macd_dea", 0) or 0
        if macd_dif > macd_dea:
            macd_status = "金叉"
        elif macd_dif < macd_dea:
            macd_status = "死叉"
        else:
            macd_status = "持平"
        macd_dif_converging = _check_macd_dif_converging(indicators_data)

    # 资金流向数据
    capital_efficiency = None
    today_main_net = 0.0
    d5_main_net = 0.0
    d10_main_net = 0.0
    xs_net = 0.0
    mf_data_available = False
    if moneyflow_data:
        try:
            today_main_net = getattr(moneyflow_data, "main_net", 0) or 0
            d5_main_net = getattr(moneyflow_data, "d5_main_net", 0) or 0
            d10_main_net = getattr(moneyflow_data, "d10_main_net", 0) or 0
            xs_net = getattr(moneyflow_data, "xs_net", 0) or 0
            capital_efficiency = getattr(moneyflow_data, "capital_efficiency", None)
            mf_data_available = True
        except Exception:
            pass

    # ══════════════════════════════════════
    # Stage 3: 三层过滤
    # ══════════════════════════════════════
    tech_details = []
    capital_details = []
    overbought_details = []
    downgrade_multiplier = 1.0
    hard_block = False
    hard_block_reasons = []

    # ── Layer 1: 技术面 ──
    layer1_passed = True
    layer1_grade = "✅通过"
    layer1_downgrade = ""
    layer1_action = ""

    # 1a. MA 检查
    if ma5 > 0 and ma20 > 0:
        if ma5 > ma20:
            tech_details.append(f"✅ MA5({ma5:.2f}) > MA20({ma20:.2f}) — 通过")
        else:
            tech_details.append(f"⚠️ MA5({ma5:.2f}) < MA20({ma20:.2f}) — 趋势待确认")
            # 启用备用检查
            price_above_vwap = current_price > avg_price if avg_price and avg_price > 0 else None
            sector_ok = req.sector_net_inflow is not None and req.sector_net_inflow > 0
            if price_above_vwap and sector_ok:
                tech_details.append("  备用检查: 重新你你你5✅ + 板块资金净流入✅ → 仅试探仓≤5%")
                layer1_grade = "⚠️降级"
                layer1_downgrade = "MA5<MA20 趋势待确认"
                layer1_action = "仅试探仓≤5%"
                downgrade_multiplier = min(downgrade_multiplier, 0.5)
            elif price_above_vwap is False:
                tech_details.append("  备用检查: 价格跌破分时均价❌ → 从计划表移除")
                layer1_passed = False
                layer1_grade = "🚫排除"
                layer1_downgrade = "MA5<MA20 且价格跌破分时均价"
                layer1_action = "从计划表移除"
                downgrade_multiplier = 0.0
            elif not sector_ok:
                tech_details.append("  备用检查: 板块资金净流入不可用或≤0 → 从计划表移除")
                layer1_passed = False
                layer1_grade = "🚫排除"
                layer1_downgrade = "MA5<MA20 且板块无资金支撑"
                layer1_action = "从计划表移除"
                downgrade_multiplier = 0.0
    else:
        tech_details.append("⚠️ MA数据不可用，跳过MA检查")

    # 1b. MACD 检查
    if macd_status == "金叉":
        tech_details.append(f"✅ MACD金叉(DIF>DEA) — 通过")
    elif macd_status == "死叉":
        if macd_dif_converging:
            tech_details.append(f"⚠️ MACD死叉但DIF连续2日收敛 → 可观察")
        else:
            tech_details.append(f"⚠️ MACD死叉且DIF未收敛 → 趋势转弱")
            layer1_grade = "⚠️降级"
            layer1_downgrade = "MACD死叉+未收敛"
            layer1_action = "降仓50%或放观察"
            downgrade_multiplier = min(downgrade_multiplier, 0.5)

    # 1c. RSR 检查
    if rsr is not None:
        if rsr < 0.8:
            tech_details.append(f"⚠️ RSR({rsr:.2f}) < 0.8 → 弱势，降仓50%")
            downgrade_multiplier = min(downgrade_multiplier, 0.5)
            layer1_grade = "⚠️降级"
            layer1_downgrade = "RSR<0.8弱势"
            layer1_action = "降仓50%"
        else:
            tech_details.append(f"✅ RSR({rsr:.2f}) ≥ 0.8 — 通过")

    # 1d. 日内分位检查
    if intraday_percentile is not None:
        if intraday_percentile > 80:
            tech_details.append(f"🚫 日内分位({intraday_percentile:.0f}%) > 80% → 追高风险，硬禁止建仓")
            layer1_passed = False
            layer1_grade = "🚫排除"
            layer1_downgrade = "日内分位>80%硬禁止"
            layer1_action = "硬禁止建仓，等回调至分位≤50%"
            downgrade_multiplier = 0.0
            hard_block = True
            hard_block_reasons.append(f"日内分位{intraday_percentile:.0f}%>80%")
        elif intraday_percentile > 60:
            tech_details.append(f"⚠️ 日内分位({intraday_percentile:.0f}%) 60-80% → 仅试探仓")
            layer1_grade = "⚠️降级"
            layer1_downgrade = "日内分位偏高"
            layer1_action = "仅试探仓≤5%"
            downgrade_multiplier = min(downgrade_multiplier, 0.5)
        else:
            tech_details.append(f"✅ 日内分位({intraday_percentile:.0f}%) ≤ 60% — 通过")

    # 1e. 资金效率检查
    if capital_efficiency is not None:
        if capital_efficiency < 5:
            tech_details.append(f"⚠️ 资金效率({capital_efficiency:.1f}) < 5% → 涨幅缺乏主力背书，降仓50%")
            downgrade_multiplier = min(downgrade_multiplier, 0.5)
            layer1_grade = "⚠️降级"
            layer1_downgrade = "资金效率<5%"
            layer1_action = "降仓50%"
        else:
            tech_details.append(f"✅ 资金效率({capital_efficiency:.1f}) ≥ 5% — 通过")

    layer1 = LayerResult(
        passed=layer1_passed,
        grade=layer1_grade if layer1_passed else "🚫排除",
        details=tech_details,
        downgrade_reason=layer1_downgrade,
        downgrade_action=layer1_action,
    )

    # ── Layer 2: 主力行为 ──
    layer2_passed = True
    layer2_grade = "✅通过"
    layer2_downgrade = ""
    layer2_action = ""

    if mf_data_available:
        # 2a. 5日主力检查
        if d5_main_net < 0:
            capital_details.append(f"🚫 5日主力净流入({d5_main_net/1e8:.2f}亿) < 0 → 直接排除")
            layer2_passed = False
            layer2_grade = "🚫排除"
            layer2_downgrade = "5日主力持续净流出"
            layer2_action = "直接排除"
            downgrade_multiplier = 0.0
        else:
            capital_details.append(f"✅ 5日主力({d5_main_net/1e8:.2f}亿) > 0 — 通过")

        # 2b. 5日 > 10日 加速
        if layer2_passed and d5_main_net > 0 and d10_main_net > 0 and d5_main_net > d10_main_net:
            capital_details.append(f"✅ 5日主力({d5_main_net/1e8:.2f}亿) > 10日({d10_main_net/1e8:.2f}亿) → 加速建仓，加分")
        elif layer2_passed and d5_main_net > 0 and d10_main_net > 0:
            capital_details.append(f"⚠️ 5日主力({d5_main_net/1e8:.2f}亿) ≤ 10日({d10_main_net/1e8:.2f}亿) → 减速中")

        # 2c. 今日出货检查
        if layer2_passed and today_main_net < 0:
            capital_details.append(f"⚠️ 今日主力({today_main_net/1e8:.2f}亿) < 0 →「今日出货」，降仓50%或放观察")
            downgrade_multiplier = min(downgrade_multiplier, 0.5)
            layer2_grade = "⚠️降级"
            layer2_downgrade = "今日主力出货"
            layer2_action = "降仓50%或放观察"

        # 2d. 10日主力排除（双条件+豁免）
        if layer2_passed and d10_main_net < -500000000:
            # 条件A: 10日主力流出 > 5亿
            # 条件B: 5日主力 < 10日主力×0.5（流出加速中）
            accelerating_outflow = d5_main_net < d10_main_net * 0.5
            # 豁免: 5日主力 > 0 且 5日 > 10日主力（趋势逆转中）
            trend_reversing = d5_main_net > 0 and d5_main_net > d10_main_net
            
            if accelerating_outflow and not trend_reversing:
                capital_details.append(f"🚫 10日主力({d10_main_net/1e8:.2f}亿) < -5亿 + 5日主力({d5_main_net/1e8:.2f}亿) < 10日×0.5({d10_main_net*0.5/1e8:.2f}亿) → 排除")
                layer2_passed = False
                layer2_grade = "🚫排除"
                layer2_downgrade = "10日主力持续大幅流出且加速"
                layer2_action = "直接排除"
                downgrade_multiplier = 0.0
            elif trend_reversing:
                capital_details.append(f"⚠️ 10日主力({d10_main_net/1e8:.2f}亿) < -5亿，但5日主力({d5_main_net/1e8:.2f}亿) > 0且>10日 → 趋势逆转，豁免排除")
            elif not accelerating_outflow:
                capital_details.append(f"⚠️ 10日主力({d10_main_net/1e8:.2f}亿) < -5亿，但5日主力({d5_main_net/1e8:.2f}亿) ≥ 10日×0.5 → 流出减速中，暂不排除")

        # 2e. 小单净流出加分
        if layer2_passed and xs_net < 0:
            capital_details.append(f"✅ 小单净流出 → 散户离场，加分")
    else:
        capital_details.append("⚠️ 资金流向数据不可用，跳过主力行为检查")
        layer2_grade = "⚠️数据缺失"

    layer2 = LayerResult(
        passed=layer2_passed,
        grade=layer2_grade,
        details=capital_details,
        downgrade_reason=layer2_downgrade,
        downgrade_action=layer2_action,
    )

    # ── Layer 3: 超买过滤 ──
    layer3_passed = True
    layer3_grade = "✅通过"
    layer3_downgrade = ""
    layer3_action = ""

    rsi_blocked = rsi6 >= 90
    rsi_probe_only = 85 <= rsi6 < 90
    kdj_blocked = kdj_j >= 110
    kdj_probe_only = 105 <= kdj_j < 110

    if rsi_blocked:
        overbought_details.append(f"🚫 RSI6({rsi6:.0f}) ≥ 90 → 严重超买，禁止建仓")
        layer3_passed = False
        layer3_grade = "🚫排除"
        layer3_downgrade = "RSI6严重超买"
        layer3_action = "禁止建仓"
        downgrade_multiplier = 0.0
    elif rsi_probe_only:
        overbought_details.append(f"⚠️ RSI6({rsi6:.0f}) 85-90 → 仅试探仓")
        layer3_grade = "⚠️仅试探仓"
        layer3_downgrade = "RSI6偏高"
        layer3_action = "仅试探仓"
        downgrade_multiplier = min(downgrade_multiplier, 0.5)
    else:
        overbought_details.append(f"✅ RSI6({rsi6:.0f}) < 85 — 正常")

    if kdj_blocked:
        overbought_details.append(f"🚫 KDJ-J({kdj_j:.0f}) ≥ 110 → 严重超买，禁止建仓")
        layer3_passed = False
        layer3_grade = "🚫排除"
        layer3_downgrade = "KDJ-J严重超买"
        layer3_action = "禁止建仓"
        downgrade_multiplier = 0.0
    elif kdj_probe_only:
        overbought_details.append(f"⚠️ KDJ-J({kdj_j:.0f}) 105-110 → 仅试探仓")
        layer3_grade = "⚠️仅试探仓"
        layer3_downgrade = "KDJ-J偏高"
        layer3_action = "仅试探仓"
        downgrade_multiplier = min(downgrade_multiplier, 0.5)
    else:
        overbought_details.append(f"✅ KDJ-J({kdj_j:.0f}) < 105 — 正常")

    layer3 = LayerResult(
        passed=layer3_passed,
        grade=layer3_grade,
        details=overbought_details,
        downgrade_reason=layer3_downgrade,
        downgrade_action=layer3_action,
    )

    # ── 硬拦截判定（不可被产业链信号豁免）──
    if rsi6 >= 95:
        hard_block = True
        hard_block_reasons.append(f"RSI6={rsi6:.0f}>=95(动量耗尽)")
        overbought_details.append(f"🔴 硬拦截: RSI6({rsi6:.0f}) >= 95 → 动量耗尽，不可被产业链信号豁免")
        layer3_passed = False
        layer3_grade = "🔴硬禁止"
        layer3_downgrade = "RSI6>=95硬拦截"
        layer3_action = "硬禁止"
        downgrade_multiplier = 0.0

    if kdj_j >= 120:
        hard_block = True
        hard_block_reasons.append(f"J={kdj_j:.0f}>=120(极端超买)")
        overbought_details.append(f"🔴 硬拦截: KDJ-J({kdj_j:.0f}) >= 120 → 极端超买，不可被产业链信号豁免")
        layer3_passed = False
        layer3_grade = "🔴硬禁止"
        layer3_downgrade = "KDJ-J>=120硬拦截"
        layer3_action = "硬禁止"
        downgrade_multiplier = 0.0

    # ══════════════════════════════════════
    # Stage 4: 综合判定
    # ══════════════════════════════════════
    all_layers_pass = layer1_passed and layer2_passed and layer3_passed

    if downgrade_multiplier <= 0:
        final_decision = "🚫禁止建仓"
        final_grade = "blocked"
        max_position_pct = 0.0
    elif downgrade_multiplier <= 0.5:
        final_decision = "⚠️仅试探仓（≤5%）"
        final_grade = "probe_only"
        max_position_pct = 5.0
    elif downgrade_multiplier < 1.0:
        final_decision = "⚠️降仓建仓"
        final_grade = "downgraded"
        max_position_pct = round(10.0 * downgrade_multiplier, 1)
    else:
        final_decision = "✅可建仓"
        final_grade = "pass"
        max_position_pct = 10.0  # 默认首仓上限

    # ══════════════════════════════════════
    # Stage 5: 买入确认规则
    # ══════════════════════════════════════
    buy_action = ""
    buy_wait = 0
    vol_ok = None

    if change_pct < 3:
        buy_action = "直接入场"
        buy_wait = 0
    elif change_pct <= 5:
        buy_action = "等3-5分钟横盘不破均线"
        buy_wait = 3
    elif change_pct <= 8:
        buy_action = "等2-3分钟，量比>1.5才入场"
        buy_wait = 2
        if volume_ratio is not None:
            vol_ok = volume_ratio > 1.5
    else:
        buy_action = "放弃（涨幅>8%，不追涨）"
        buy_wait = 0
        downgrade_multiplier = 0.0
        final_decision = "🚫涨幅>8%放弃"
        final_grade = "blocked"
        max_position_pct = 0.0

    buy_confirmation = EntryBuyConfirmation(
        change_pct=round(change_pct, 2),
        action=buy_action,
        wait_minutes=buy_wait,
        volume_ratio=volume_ratio,
        volume_ratio_ok=vol_ok,
    )

    # ══════════════════════════════════════
    # Stage 6: 构建响应
    # ══════════════════════════════════════
    ma_status = ""
    if ma5 > 0 and ma20 > 0:
        ma_status = "MA5>MA20" if ma5 > ma20 else "MA5<MA20"

    tech = EntryCheckTechDetail(
        ma5=round(ma5, 2),
        ma20=round(ma20, 2),
        ma_status=ma_status,
        macd_status=macd_status,
        macd_dif_converging=macd_dif_converging,
        rsr=rsr,
        intraday_percentile=intraday_percentile,
        capital_efficiency=capital_efficiency,
        rsi6=round(rsi6, 1),
        kdj_j=round(kdj_j, 1),
        current_price=round(current_price, 3),
        avg_price=round(avg_price, 3) if avg_price else None,
    )

    capital = EntryCheckCapitalDetail(
        today_main_net=round(today_main_net, 2),
        d5_main_net=round(d5_main_net, 2),
        d10_main_net=round(d10_main_net, 2),
        d5_gt_d10=(d5_main_net > 0 and d10_main_net > 0 and d5_main_net > d10_main_net),
        today_selling=(today_main_net < 0),
        xs_net=round(xs_net, 2),
        xs_outflow=(xs_net < 0),
        data_available=mf_data_available,
    )

    # 生成摘要
    summary_parts = []
    if final_grade == "pass":
        summary_parts.append("✅ 三层过滤全部通过，可按建议仓位建仓")
    elif final_grade == "probe_only":
        summary_parts.append("⚠️ 仅允许试探仓（≤5%总资产）")
    elif final_grade == "downgraded":
        summary_parts.append(f"⚠️ 降仓至{max_position_pct}%")
    else:
        summary_parts.append("🚫 禁止建仓")
    if buy_wait > 0:
        summary_parts.append(f"入场规则: {buy_action}")
    summary = " | ".join(summary_parts)

    # ── 跨窗口候选池：时机性拒绝自动入池 ──
    try:
        from app.services.candidate_pool import get_candidate_pool
        pool = get_candidate_pool()
        captured = pool.maybe_capture(
            symbol=ts_code,
            name=stock_name,
            final_grade=final_grade,
            downgrade_multiplier=downgrade_multiplier,
            hard_block=hard_block,
            layer1_passed=layer1_passed,
            layer2_passed=layer2_passed,
            layer3_passed=layer3_passed,
            macd_status=macd_status,
            macd_dif_converging=macd_dif_converging,
            change_pct=change_pct,
            layer1_downgrade=layer1_downgrade,
            layer2_downgrade=layer2_downgrade,
            layer3_downgrade=layer3_downgrade,
        )
        if captured:
            logger.info(f"[CandidatePool] Captured {ts_code} ({stock_name})")
    except Exception:
        pass

    return EntryCheckResponse(
        symbol=ts_code,
        name=stock_name,
        tech=tech,
        layer1_tech=layer1,
        layer2_capital=layer2,
        layer3_overbought=layer3,
        final_decision=final_decision,
        final_grade=final_grade,
        max_position_pct=max_position_pct,
        downgrade_multiplier=round(downgrade_multiplier, 2),
        hard_block=hard_block,
        hard_block_reasons=hard_block_reasons,
        buy_confirmation=buy_confirmation,
        all_layers_pass=all_layers_pass,
        summary=summary,
    )


# ──────────────────────── 主营业务构成 ────────────────────────

@router.get("/fina-mainbz/{symbol}")
async def get_fina_mainbz(
    symbol: str,
    period: Optional[str] = Query(None, description="报告期 YYYYMMDD，如 20231231，默认最近报告期"),
    limit: int = Query(10, ge=1, le=50, description="返回条数上限"),
):
    """
    获取个股主营业务构成（产品/行业/地区维度收入占比）。

    数据源: Tushare fina_mainbz 接口。

    返回字段:
    - ts_code: 股票代码
    - end_date: 报告期
    - bz_item: 业务项（产品/行业/地区分类）
    - bz_sales: 营业收入（元）
    - bz_profit: 营业利润（元）
    - bz_cost: 营业成本（元）
    - curr_type: 币种
    - type: 类型（P=产品/I=行业/R=地区）

    参数示例:
    - GET /indicator/fina-mainbz/000001.SZ → 平安银行最主营业务构成
    - GET /indicator/fina-mainbz/SH600519?limit=20 → 贵州茅台主营业务构成（最多20条）
    """
    ts_code = _normalize_to_ts_code(symbol)

    try:
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        pro = ts.pro_api(token)

        kw = {"ts_code": ts_code, "limit": limit}
        if period:
            kw["end_date"] = period

        df = pro.fina_mainbz(**kw)

        if df is None or df.empty:
            return {
                "symbol": ts_code,
                "records": [],
                "count": 0,
                "message": f"{ts_code} 暂无主营业务构成数据",
            }

        # 按报告期降序，取最新一期
        df = df.sort_values("end_date", ascending=False)
        latest_period = str(df.iloc[0]["end_date"]) if len(df) > 0 else ""

        records = []
        for _, row in df.iterrows():
            records.append({
                "end_date": str(row.get("end_date", "") or ""),
                "bz_item": str(row.get("bz_item", "") or ""),
                "bz_sales": _safe_float(row.get("bz_sales")),
                "bz_profit": _safe_float(row.get("bz_profit")),
                "bz_cost": _safe_float(row.get("bz_cost")),
                "curr_type": str(row.get("curr_type", "") or ""),
                "type": str(row.get("type", "") or ""),
            })

        return {
            "symbol": ts_code,
            "report_period": latest_period,
            "records": records,
            "count": len(records),
            "data_source": "Tushare(fina_mainbz)",
        }

    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=f"Tushare 配置错误: {str(e)}")
    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取主营业务构成失败: {str(e)}")


# ──────────────────────── 业绩快报 ────────────────────────

@router.get("/express/{symbol}")
async def get_express(
    symbol: str,
    period: Optional[str] = Query(None, description="报告期 YYYYMMDD，如 20231231，默认最近报告期"),
    limit: int = Query(5, ge=1, le=50, description="返回条数上限"),
):
    """
    获取个股业绩快报数据。

    数据源: Tushare express 接口。

    返回字段:
    - ts_code: 股票代码
    - end_date: 报告期
    - revenue: 营业收入（元）
    - operate_profit: 营业利润（元）
    - total_profit: 利润总额（元）
    - n_income: 净利润（元）
    - basic_eps: 每股收益
    - weighted_roe: 净资产收益率(%)
    - yoy_revenue: 营收同比增长(%)
    - yoy_operate_profit: 营业利润同比增长(%)
    - yoy_n_income: 净利润同比增长(%)
    - total_assets: 总资产（元）
    - announce_date: 公告日期

    参数示例:
    - GET /indicator/express/000001.SZ → 平安银行最新业绩快报
    - GET /indicator/express/SH600519?limit=5 → 贵州茅台最近5期业绩快报
    """
    ts_code = _normalize_to_ts_code(symbol)

    try:
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        pro = ts.pro_api(token)

        kw = {"ts_code": ts_code, "limit": limit + 5}  # 多取一些用于过滤空值
        if period:
            kw["end_date"] = period

        df = pro.express(**kw)

        if df is None or df.empty:
            return {
                "symbol": ts_code,
                "records": [],
                "count": 0,
                "message": f"{ts_code} 暂无业绩快报数据",
            }

        df = df.sort_values("end_date", ascending=False)

        records = []
        for _, row in df.iterrows():
            rec = {
                "end_date": str(row.get("end_date", "") or ""),
                "revenue": _safe_float(row.get("revenue")),
                "operate_profit": _safe_float(row.get("operate_profit")),
                "total_profit": _safe_float(row.get("total_profit")),
                "n_income": _safe_float(row.get("n_income")),
                "basic_eps": _safe_float(row.get("basic_eps")),
                "weighted_roe": _safe_float(row.get("weighted_roe")),
                "yoy_revenue": _safe_float(row.get("yoy_revenue")),
                "yoy_operate_profit": _safe_float(row.get("yoy_operate_profit")),
                "yoy_n_income": _safe_float(row.get("yoy_n_income")),
                "total_assets": _safe_float(row.get("total_assets")),
                "announce_date": str(row.get("announce_date", "") or ""),
            }
            records.append(rec)

        return {
            "symbol": ts_code,
            "records": records[:limit],
            "count": min(len(records), limit),
            "data_source": "Tushare(express)",
        }

    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=f"Tushare 配置错误: {str(e)}")
    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取业绩快报失败: {str(e)}")


# ═══════════════════════════════════════════════════════════════
# 条件诊断端点（实盘）：加仓条件 / 建仓条件
# ═══════════════════════════════════════════════════════════════

def _get_trading_period() -> dict:
    """判断当前所处交易时段"""
    from datetime import datetime as dtime
    now = dtime.now()
    t = now.hour * 60 + now.minute

    if t < 9 * 60 + 30:
        return {"period": "pre_market", "label": "盘前", "is_trading": False, "is_blocked": True}
    elif t < 9 * 60 + 45:
        return {"period": "morning_quiet", "label": "早盘冷静期 (09:30-09:45)", "is_trading": True, "is_blocked": True}
    elif t < 11 * 60 + 30:
        return {"period": "morning", "label": "上午交易", "is_trading": True, "is_blocked": False}
    elif t < 13 * 60:
        return {"period": "lunch", "label": "午休 (11:30-13:00)", "is_trading": False, "is_blocked": True}
    elif t < 14 * 60 + 30:
        return {"period": "afternoon", "label": "下午交易", "is_trading": True, "is_blocked": False}
    elif t < 15 * 60:
        return {"period": "closing", "label": "尾盘 (14:30-15:00)", "is_trading": True, "is_blocked": True}
    else:
        return {"period": "after_market", "label": "收盘后", "is_trading": False, "is_blocked": True}


# ── Tushare 日频数据辅助函数（非交易时段回退用） ──

def _get_tushare_pro():
    """统一获取 Tushare pro_api 实例"""
    from app.core.trading._api_config import get_tushare_pro as _gtp
    return _gtp()


def _normalize_sym(symbol: str) -> str:
    """将 symbol 标准化为 Tushare ts_code 格式"""
    return _normalize_to_ts_code(symbol)


def _fetch_tushare_daily_df(symbol: str, days: int = 60) -> dict:
    """从 Tushare 获取日K线数据，返回 {date: {open,high,low,close,volume}} 或空dict"""
    try:
        pro = _get_tushare_pro()
        ts_code = _normalize_sym(symbol)
        from datetime import datetime as _dt, timedelta as _td
        end = _dt.now().strftime("%Y%m%d")
        start = (_dt.now() - _td(days=days + 10)).strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                       fields="trade_date,open,high,low,close,vol")
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            result[row["trade_date"]] = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["vol"]),
            }
        return result
    except Exception:
        return {}


def _fetch_tushare_close_prices(symbol: str, days: int = 25) -> list:
    """从 Tushare 获取最近N个交易日收盘价列表（从旧到新，不含当日）"""
    daily = _fetch_tushare_daily_df(symbol, days=days + 5)
    if not daily:
        return []
    sorted_dates = sorted(daily.keys())
    closes = [daily[d]["close"] for d in sorted_dates[-days:]]
    return closes


def _calc_rsi_tushare(closes: list, period: int = 6) -> float:
    """从收盘价列表计算 RSI"""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains.append(diff); losses.append(0)
        else:
            gains.append(0); losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _calc_amp_tushare(daily: dict) -> float:
    """从日K线字典计算近5日平均振幅"""
    sorted_dates = sorted(daily.keys())[-5:]
    amps = []
    for d in sorted_dates:
        bar = daily[d]
        if bar["low"] > 0:
            amps.append((bar["high"] - bar["low"]) / bar["low"])
    return sum(amps) / len(amps) if amps else 0.03


def _check_trend_tushare(symbol: str, closes: list, daily: dict) -> dict:
    """基于 Tushare 日频数据检查趋势强度

    Returns:
        {passed, failed_items, checks: {ma5_slope, volume_ratio, ma_align, moneyflow}}
    """
    checks = {}

    # MA5 斜率
    if len(closes) >= 10:
        ma5_now = sum(closes[-5:]) / 5
        ma5_prev = sum(closes[-10:-5]) / 5
        if ma5_prev > 0:
            slope = (ma5_now - ma5_prev) / ma5_prev
            checks["ma5_slope"] = {
                "passed": slope > 0,
                "value": f"{slope:.2%}",
                "threshold": "> 0",
                "detail": f"MA5 {ma5_now:.2f} vs 前期{ma5_prev:.2f}",
            }
        else:
            checks["ma5_slope"] = {"passed": False, "value": "N/A", "threshold": "> 0", "detail": "MA5计算异常"}
    else:
        checks["ma5_slope"] = {"passed": False, "value": "N/A", "threshold": "> 0",
                                "detail": f"数据不足(需≥10条,当前{len(closes)}条)"}

    # 量比
    sorted_dates = sorted(daily.keys())
    vols = [daily[d]["volume"] for d in sorted_dates[-6:] if daily[d]["volume"] > 0]
    if len(vols) >= 6:
        vol_ratio = vols[-1] / (sum(vols[:-1]) / 5) if sum(vols[:-1]) > 0 else 0
        checks["volume_ratio"] = {
            "passed": vol_ratio > 0.8,
            "value": f"{vol_ratio:.2f}",
            "threshold": "> 0.8",
        }
    else:
        checks["volume_ratio"] = {"passed": True, "value": "N/A", "threshold": "> 0.8", "detail": "数据不足,跳过"}

    # MA5 > MA20
    if len(closes) >= 20:
        ma5 = sum(closes[-5:]) / 5
        ma20 = sum(closes[-20:]) / 20
        checks["ma_align"] = {
            "passed": ma5 > ma20 and ma5 > 0,
            "value": f"MA5={ma5:.2f} MA20={ma20:.2f}",
            "threshold": "MA5 > MA20",
        }
    else:
        checks["ma_align"] = {"passed": False, "value": "N/A", "threshold": "MA5 > MA20",
                               "detail": f"数据不足(需≥20条,当前{len(closes)}条)"}

    # 主力资金流向 (5日)
    try:
        mf = _fetch_tushare_moneyflow(symbol, days=5)
        main_net_5d = 0.0
        if not mf.empty:
            for _, row in mf.iterrows():
                lg = (float(row.get("buy_elg_amount", 0) or 0) -
                      float(row.get("sell_elg_amount", 0) or 0))
                md = (float(row.get("buy_lg_amount", 0) or 0) -
                      float(row.get("sell_lg_amount", 0) or 0))
                main_net_5d += (lg + md)
        checks["moneyflow"] = {
            "passed": main_net_5d > 0,
            "value": f"{main_net_5d / 1e8:.2f}亿",
            "threshold": "> 0",
            "detail": "5日主力净流入(Tushare)",
        }
    except Exception:
        checks["moneyflow"] = {"passed": True, "value": "N/A", "threshold": "> 0", "detail": "数据获取跳过"}

    failed_items = [k for k, v in checks.items() if not v["passed"]]
    return {"passed": len(failed_items) == 0, "failed_items": failed_items, "checks": checks}


def _fetch_tushare_moneyflow(symbol: str, days: int = 10):
    """从 Tushare 获取个股资金流向 DataFrame（最近N天）"""
    try:
        pro = _get_tushare_pro()
        ts_code = _normalize_sym(symbol)
        from datetime import datetime as _dt, timedelta as _td
        end = _dt.now().strftime("%Y%m%d")
        start = (_dt.now() - _td(days=days + 10)).strftime("%Y%m%d")
        df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date", ascending=False)
        return df.head(days)
    except Exception:
        return None


def _build_tier_gates_tushare(symbol: str, cur_price: float, avg_price: float,
                               float_pnl_pct: float, current_tier: str,
                               total_asset: float, pi_stance: str,
                               closes: list, daily: dict) -> dict:
    """基于 Tushare 日频数据构建层级+门控检查结果

    Returns:
        {tier_info, gates, missing_conditions, can_add}
    """
    tier_info = {}
    missing_conditions = []
    gates = []

    # ── 层级评估 ──
    from app.services.position_tier_monitor import get_position_tier_monitor
    monitor = get_position_tier_monitor()
    try:
        evaluation = monitor.evaluate_position_tier(symbol, float_pnl_pct, current_tier)
    except Exception:
        # 简易层级判断
        from app.services.position_tier_monitor import TierEvaluation
        class _Eval:
            action = "HOLD"
            current_tier = current_tier
            target_tier = None
            signal = ""
        evaluation = _Eval()
        if current_tier in ("probe", "unknown", ""):
            if float_pnl_pct >= 1.0:
                evaluation = type('_E', (), {'action': 'UPGRADE', 'current_tier': 'probe',
                                              'target_tier': 'confirm', 'signal': 'probe→confirm'})()
        elif current_tier == "confirm":
            if float_pnl_pct >= 3.0:
                evaluation = type('_E', (), {'action': 'UPGRADE', 'current_tier': 'confirm',
                                              'target_tier': 'sprint', 'signal': 'confirm→sprint'})()

    tier_info = {
        "action": evaluation.action,
        "current_tier": evaluation.current_tier,
        "target_tier": evaluation.target_tier if evaluation.target_tier else None,
        "next_tier": evaluation.target_tier or None,
        "signal": evaluation.signal,
    }
    if evaluation.action == "HOLD":
        tier_info["need_pnl"] = 0.01 if current_tier in ("probe", "unknown", "") else 0.03

    if evaluation.action in ("HOLD", "MAX_TIER"):
        if evaluation.action == "MAX_TIER":
            missing_conditions.append("已达最高层级(冲刺仓)，无法继续加仓")
        else:
            need = tier_info.get("need_pnl", 0)
            next_tier = tier_info.get("next_tier", "")
            missing_conditions.append(
                f"浮盈不足: 当前{float_pnl_pct}%, 需要≥{round(need * 100, 1)}%触发{next_tier}升级"
            )
        return {
            "tier_evaluation": tier_info,
            "gates": gates,
            "missing_conditions": missing_conditions,
            "can_add": False,
        }

    # ── 门控检查（Tushare 日频版，部分检查标记为 N/A） ──
    can_add = True

    # 1. Pi 立场
    stance_pass = pi_stance != "red"
    gates.append({"name": "Pi立场", "passed": stance_pass,
                   "detail": f"Pi立场={pi_stance}" + (" (red禁止加仓)" if not stance_pass else "")})
    if not stance_pass:
        can_add = False
        missing_conditions.append("Pi立场=red，禁止加仓")

    # 2. 总回撤 (无法从日频数据获取实时账户，标记通过)
    gates.append({"name": "总回撤<5%", "passed": True,
                   "detail": "N/A (日频数据无法计算实时回撤, 默认通过)"})

    # 3. 保护线 (T1/T2，无法从日频获取实时动态止损价)
    gates.append({"name": "保护线", "passed": True,
                   "detail": "N/A (日频数据无T1/T2价格, 默认通过)"})

    # 4. 单日加仓次数 (无法获取当日执行记录)
    gates.append({"name": "单日加仓上限", "passed": True,
                   "detail": "N/A (日频数据无当日执行记录, 默认通过)"})

    # 5. 趋势强度
    trend = _check_trend_tushare(symbol, closes, daily)
    for name, check in trend.get("checks", {}).items():
        label_map = {"ma5_slope": "MA5斜率", "volume_ratio": "量比", "ma_align": "均线排列", "moneyflow": "主力资金"}
        gates.append({"name": f"趋势-{label_map.get(name, name)}", "passed": check["passed"],
                       "detail": f"{check.get('value', '')} {check.get('threshold', '')}"})
    if not trend["passed"]:
        can_add = False
        missing_conditions.append(f"趋势强度不足: {', '.join(trend['failed_items'])}")

    # 6. T+1 锁 (无法从日频数据获取)
    gates.append({"name": "T+1锁", "passed": True, "detail": "N/A (日频数据无当日执行记录, 默认通过)"})

    return {
        "tier_evaluation": tier_info,
        "gates": gates,
        "missing_conditions": missing_conditions,
        "can_add": can_add,
    }


def _check_entry_tushare(symbol: str, closes: list, daily: dict) -> dict:
    """基于 Tushare 日频数据检查3层入场过滤

    Returns:
        {all_pass, l1_pass, l2_pass, l3_pass, l1_reasons, l2_reasons, l3_reasons,
         rsi, ma5, ma20, main_net_5d, cur_price}
    """
    sorted_dates = sorted(daily.keys())
    last_date = sorted_dates[-1] if sorted_dates else None
    cur_price = daily[last_date]["close"] if last_date else 0

    # L1 技术面: MA5 > MA20 + MACD > 0 + RSI6 < 80
    l1_reasons = []
    if len(closes) >= 20:
        ma5 = sum(closes[-5:]) / 5
        ma20 = sum(closes[-20:]) / 20
    elif closes:
        ma5 = sum(closes[-min(5, len(closes)):]) / min(5, len(closes))
        ma20 = sum(closes) / len(closes)
    else:
        ma5 = ma20 = cur_price

    l1_pass = True
    if ma5 <= ma20:
        l1_pass = False
        l1_reasons.append(f"MA5({ma5:.2f}) ≤ MA20({ma20:.2f})")

    # MACD (简化: DIF=EMA12-EMA26, DEA=EMA(DIF,9))
    macd_pass = True
    if len(closes) >= 26:
        def _ema(data, n):
            k = 2.0 / (n + 1)
            result = [data[0]]
            for x in data[1:]:
                result.append(x * k + result[-1] * (1 - k))
            return result
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        dif = [ema12[i] - ema26[i] for i in range(len(closes))]
        if len(dif) >= 9:
            dea = _ema(dif, 9)
            macd_val = 2 * (dif[-1] - dea[-1])
            if macd_val <= 0:
                macd_pass = False
                l1_reasons.append(f"MACD({macd_val:.4f}) ≤ 0")

    rsi = _calc_rsi_tushare(closes, 6)
    if rsi >= 80:
        l1_pass = False
        l1_reasons.append(f"RSI6({rsi:.1f}) ≥ 80 (超买)")

    # L2 主力资金: 5日主力净流入 > 0
    l2_pass = True
    l2_reasons = []
    main_net_5d = 0.0
    try:
        mf = _fetch_tushare_moneyflow(symbol, days=5)
        if mf is not None and not mf.empty:
            for _, row in mf.iterrows():
                lg = (float(row.get("buy_elg_amount", 0) or 0) -
                      float(row.get("sell_elg_amount", 0) or 0))
                md = (float(row.get("buy_lg_amount", 0) or 0) -
                      float(row.get("sell_lg_amount", 0) or 0))
                main_net_5d += (lg + md)
            if main_net_5d <= 0:
                l2_pass = False
                l2_reasons.append(f"5日主力净流入({main_net_5d / 1e8:.2f}亿) ≤ 0")
        else:
            l2_reasons.append("无法获取资金流向数据")
    except Exception as e:
        l2_reasons.append(f"资金流向获取异常: {e}")

    # L3 超买: RSI6 < 80
    l3_pass = rsi < 80
    l3_reasons = [] if l3_pass else [f"RSI6({rsi:.1f}) ≥ 80"]

    all_pass = l1_pass and l2_pass and l3_pass

    return {
        "all_pass": all_pass,
        "l1_pass": l1_pass, "l2_pass": l2_pass, "l3_pass": l3_pass,
        "l1_reasons": l1_reasons, "l2_reasons": l2_reasons, "l3_reasons": l3_reasons,
        "rsi": round(rsi, 1), "ma5": round(ma5, 2), "ma20": round(ma20, 2),
        "main_net_5d": round(main_net_5d, 2), "cur_price": round(cur_price, 2),
    }


@router.get("/position-add-conditions")
async def position_add_conditions_live(symbol: str = Query(None)):
    """实盘模式：查询持仓距离加仓还差哪些条件。

    逐只检查当前持仓的层级评估和门控状态，返回缺失条件清单。
    非交易时段仍可查询，但会标注数据可能滞后。
    """
    from app.services.position_tier_monitor import get_position_tier_monitor

    trading = _get_trading_period()

    monitor = get_position_tier_monitor()
    tier_status = monitor.get_tier_status()
    positions = tier_status.get("positions", [])

    if symbol:
        # 兼容带/不带交易所前缀的代码格式（SZ002714 ↔ 002714）
        positions = [p for p in positions if p["symbol"] == symbol or p["symbol"][2:] == symbol]

    if not positions:
        return {
            "positions": [],
            "summary": f"未找到标的 {symbol} 的持仓" if symbol else "当前无持仓",
            "mode": "live",
        }

    try:
        executor = monitor.executor
        account = executor.get_account() if executor else {}
        total_asset = float(account.get("total_asset", 100000))
    except Exception:
        total_asset = 100000

    pi_stance = "yellow"
    try:
        from core.utils.strategy_chain import StrategyChain
        chain = StrategyChain()
        pi_conf = chain.get_pi_confirmation()
        if pi_conf:
            pi_stance = pi_conf.get("stance", "yellow")
    except Exception:
        pass

    use_tushare = not trading["is_trading"]

    results = []
    for pos in positions:
        sym = pos["symbol"]
        cur_price = pos.get("current_price", 0)
        avg_price = pos.get("avg_price", 0)
        float_pnl_pct = pos.get("float_pnl_pct", 0)
        current_tier = pos.get("tier", "probe")
        data_source = "tushare_daily" if use_tushare else "realtime"

        if use_tushare:
            # ── 非交易时段：Tushare 日频数据 ──
            daily = _fetch_tushare_daily_df(sym, days=60)
            closes = [daily[d]["close"] for d in sorted(daily.keys())] if daily else []
            if daily:
                sorted_dates = sorted(daily.keys())
                last_close = daily[sorted_dates[-1]]["close"]
                if cur_price == 0:
                    cur_price = last_close
            diag = _build_tier_gates_tushare(
                sym, cur_price, avg_price, float_pnl_pct, current_tier,
                total_asset, pi_stance, closes, daily
            )
            tier_info = diag["tier_evaluation"]
            gates = diag["gates"]
            missing_conditions = diag["missing_conditions"]
            can_add = diag["can_add"]
            t1_locked = None  # 日频数据无法获取
        else:
            # ── 交易时段：实时数据 ──
            evaluation = monitor.evaluate_position_tier(sym, float_pnl_pct, current_tier)

            tier_info = {
                "action": evaluation.action,
                "current_tier": evaluation.current_tier,
                "target_tier": evaluation.target_tier if evaluation.target_tier else None,
                "next_tier": evaluation.target_tier or None,
                "signal": evaluation.signal,
            }
            if evaluation.action == "HOLD":
                tier_info["need_pnl"] = 0.01 if current_tier in ("probe", "unknown", "") else 0.03

            missing_conditions = []
            gates = []
            t1_locked = False

            if evaluation.action in ("HOLD", "MAX_TIER"):
                if evaluation.action == "MAX_TIER":
                    missing_conditions.append("已达最高层级(冲刺仓)，无法继续加仓")
                else:
                    need = tier_info.get("need_pnl", 0)
                    next_tier = tier_info.get("next_tier", "")
                    missing_conditions.append(
                        f"浮盈不足: 当前{float_pnl_pct}%, 需要≥{round(need*100,1)}%触发{next_tier}升级"
                    )
                can_add = False
            else:
                # 门控检查
                try:
                    gate = monitor.can_execute_add(
                        sym, evaluation, cur_price, avg_price, account, pi_stance
                    )
                    can_add = gate.allowed
                    for name, detail in gate.checks:
                        passed = name not in ("BLOCKED", "FAILED")
                        gates.append({"name": name, "passed": passed, "detail": detail})
                    if not can_add:
                        blocked = [c[1] for c in gate.checks if c[0] in ("BLOCKED", "FAILED")]
                        missing_conditions = blocked
                except Exception:
                    can_add = False
                    missing_conditions = ["门控检查异常"]

        results.append({
            "symbol": sym,
            "current_price": round(cur_price, 2),
            "avg_cost": round(avg_price, 2),
            "float_pnl_pct": round(float_pnl_pct, 2),
            "current_tier": current_tier,
            "t1_locked": t1_locked,
            "data_source": data_source,
            "tier_evaluation": tier_info,
            "gates": gates,
            "missing_conditions": missing_conditions,
            "can_add": can_add,
        })

    warning = None
    if trading["is_blocked"]:
        if trading["period"] == "closing":
            warning = "尾盘时段：仅止损，不加仓。以下诊断仅供参考"
        elif not trading["is_trading"]:
            warning = f"非交易时段（{trading['label']}）：使用Tushare日频数据，部分门控标记为N/A，结果仅供参考"

    return {
        "total_asset": round(total_asset, 2),
        "pi_stance": pi_stance,
        "trading_period": trading,
        "warning": warning,
        "positions": results,
        "summary": (
            f"共{len(results)}只持仓, "
            f"{sum(1 for r in results if r['can_add'])}只满足加仓条件, "
            f"{sum(1 for r in results if not r['can_add'])}只条件不足"
        ),
        "mode": "live",
    }


@router.get("/candidate-entry-conditions")
async def candidate_entry_conditions_live(symbol: str = Query(None)):
    """实盘模式：查询候选池股票距离建仓还差哪些条件。

    逐只检查入场过滤三层、Pi立场、午后限制、涨幅确认，
    区分长期池(>3天)和短期池(≤3天)。
    非交易时段仍可查询，但入场过滤依赖实时行情，盘后可能无法通过。
    """
    import asyncio
    from datetime import date as dt_date, datetime as dt_datetime, timedelta
    from app.services.candidate_pool import get_candidate_pool
    from app.models.indicator import EntryCheckRequest

    trading = _get_trading_period()

    # ── 短期候选池 ──
    pool = get_candidate_pool()
    waiting = pool.get_waiting()
    if symbol:
        # 兼容带/不带交易所前缀（SH603259 ↔ 603259）
        waiting = [
            c for c in waiting
            if c.get("symbol") == symbol or c.get("symbol", "")[2:] == symbol
        ]

    # ── 长期候选池 ──
    from app.services.long_term_pool import get_long_term_pool
    ltp = get_long_term_pool()
    lt_active = ltp.get_active()
    if symbol:
        lt_active = [
            c for c in lt_active
            if c.get("symbol") == symbol or c.get("symbol", "")[2:] == symbol
        ]

    if not waiting and not lt_active:
        return {
            "candidates": [],
            "long_term": [],
            "short_term": [],
            "summary": f"候选池无 {symbol} 的 waiting/active 状态记录" if symbol else "候选池无 waiting/active 状态标的",
            "mode": "live",
        }

    # Pi 立场
    pi_stance = "yellow"
    try:
        from core.utils.strategy_chain import StrategyChain
        chain = StrategyChain()
        pi_conf = chain.get_pi_confirmation()
        if pi_conf:
            pi_stance = pi_conf.get("stance", "yellow")
    except Exception:
        pass

    use_tushare = not trading["is_trading"]
    # ── 长期候选池：标准化为与短期池一致的 dict 格式 ──
    for lt in lt_active:
        added_at = lt.get("added_at", "")
        added_trade_day = added_at[:10] if added_at else ""
        waiting.append({
            "symbol": lt.get("symbol", ""),
            "name": lt.get("name", ""),
            "added_trade_day": added_trade_day,
            "checks_count": lt.get("checks_count", 0),
            "reject_reasons": [],
            "chain_name": lt.get("chain_name", ""),
            "chain_role": lt.get("chain_role", ""),
            "_pool_source": "long_term",  # 标记来源
        })

    if not waiting:
        return {
            "candidates": [],
            "long_term": [],
            "short_term": [],
            "summary": f"候选池无 {symbol} 的 waiting/active 状态记录" if symbol else "候选池无 waiting/active 状态标的",
            "mode": "live",
        }

    stance_pass = pi_stance != "red"

    now = dt_datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    is_afternoon = now.hour >= 13

    results = []
    for candidate in waiting:
        sym = candidate.get("symbol", "")
        name = candidate.get("name", "")
        pool_source = candidate.get("_pool_source", "short_term")
        if pool_source == "long_term":
            pool_type = "长期池"
        else:
            pool_type = _classify_pool_type(candidate)
        added_date = candidate.get("added_trade_day", "")
        checks_count = candidate.get("checks_count", 0)
        data_source = "tushare_daily" if use_tushare else "realtime"

        if use_tushare:
            # ── 非交易时段：Tushare 日频数据 ──
            daily = _fetch_tushare_daily_df(sym, days=60)
            closes = [daily[d]["close"] for d in sorted(daily.keys())] if daily else []
            entry = _check_entry_tushare(sym, closes, daily)

            l1_pass = entry["l1_pass"]
            l2_pass = entry["l2_pass"]
            l3_pass = entry["l3_pass"]
            all_filter_pass = entry["all_pass"]
            cur_price = entry["cur_price"]
            rsi_val = entry["rsi"]
            change_pct = 0.0

            # 买入确认：Tushare 日频无法获取实时涨跌幅，跳过
            bc_pass = True
            bc_action = "N/A (日频数据无实时涨跌幅)"

            # 午后限制：无法获取分时数据
            afternoon_pass = True
            afternoon_failed = []

            # 汇总缺失条件
            missing_conditions = []
            if not l1_pass:
                for reason in entry["l1_reasons"]:
                    missing_conditions.append(f"[技术面] fail: {reason}")
            if not l2_pass:
                for reason in entry["l2_reasons"]:
                    missing_conditions.append(f"[主力资金] fail: {reason}")
            if not l3_pass:
                for reason in entry["l3_reasons"]:
                    missing_conditions.append(f"[超买] fail: {reason}")
            if not stance_pass:
                missing_conditions.append(f"[Pi立场] red 禁止建仓")

            can_entry = all_filter_pass and stance_pass

            # 构建 filters 详情
            filters = {
                "layer1_tech": {
                    "passed": l1_pass,
                    "grade": "pass" if l1_pass else "fail",
                    "failed": entry["l1_reasons"],
                },
                "layer2_capital": {
                    "passed": l2_pass,
                    "grade": "pass" if l2_pass else "fail",
                    "failed": entry["l2_reasons"],
                },
                "layer3_overbought": {
                    "passed": l3_pass,
                    "grade": "pass" if l3_pass else "fail",
                    "failed": entry["l3_reasons"],
                },
            }

            results.append({
                "symbol": sym,
                "name": name,
                "pool_type": pool_type,
                "added_date": added_date,
                "current_price": cur_price,
                "change_pct": change_pct,
                "rsi": rsi_val,
                "ma5": entry.get("ma5", 0),
                "ma20": entry.get("ma20", 0),
                "main_net_5d": entry.get("main_net_5d", 0),
                "last_reject_reason": candidate.get("reject_reasons", []),
                "checks_count": checks_count,
                "data_source": data_source,
                "filters": filters,
                "stance_check": {"passed": stance_pass,
                                 "detail": f"Pi立场={pi_stance}" + (" (red禁止)" if not stance_pass else "")},
                "buy_confirmation": {"passed": bc_pass, "action": bc_action, "failed": []},
                "afternoon_check": {"passed": afternoon_pass, "is_afternoon": is_afternoon, "failed": afternoon_failed},
                "missing_conditions": missing_conditions,
                "can_entry": can_entry,
            })
            continue

        # ── 交易时段：实时数据 ──
        # 重新运行入场过滤
        try:
            async def _run():
                from app.api.indicator import check_entry_filters
                return await check_entry_filters(EntryCheckRequest(symbol=sym))
            loop = asyncio.new_event_loop()
            try:
                filter_result = loop.run_until_complete(_run())
            finally:
                loop.close()
        except Exception as e:
            results.append({
                "symbol": sym, "name": name,
                "error": f"入场过滤检查失败: {e}",
                "missing_conditions": [f"入场过滤检查失败: {e}"],
                "can_entry": False,
                "pool_type": pool_type,
                "added_date": added_date,
                "checks_count": checks_count,
                "data_source": data_source,
            })
            continue

        l1 = filter_result.layer1_tech
        l2 = filter_result.layer2_capital
        l3 = filter_result.layer3_overbought

        l1_pass = l1.grade == "pass"
        l2_pass = l2.grade == "pass"
        l3_pass = l3.grade == "pass"
        all_filter_pass = filter_result.final_grade == "pass" and not filter_result.hard_block

        # 买入确认
        bc = filter_result.buy_confirmation
        bc_pass = bc.action != "放弃（涨幅>8%，不追涨）"

        # 午后检查
        afternoon_pass = True
        afternoon_failed = []
        if is_afternoon:
            if bc.change_pct > 3:
                afternoon_pass = False
                afternoon_failed.append(f"午后涨幅{bc.change_pct}% > 3%")
            ip = filter_result.tech.intraday_percentile if filter_result.tech else None
            if ip is not None and ip > 60:
                afternoon_pass = False
                afternoon_failed.append(f"午后分位{ip:.0f}% > 60%")

        # 汇总
        missing_conditions = []
        if not l1_pass:
            missing_conditions.append(f"[技术面] {l1.grade}: {getattr(l1, 'downgrade_reason', '') or '未通过'}")
        if not l2_pass:
            missing_conditions.append(f"[主力资金] {l2.grade}: {getattr(l2, 'downgrade_reason', '') or '未通过'}")
        if not l3_pass:
            missing_conditions.append(f"[超买] {l3.grade}: {getattr(l3, 'downgrade_reason', '') or '未通过'}")
        if not stance_pass:
            missing_conditions.append(f"[Pi立场] red 禁止建仓")
        if not bc_pass:
            missing_conditions.append(f"[买入确认] {bc.action}")
        if not afternoon_pass:
            missing_conditions.append(f"[午后限制] {', '.join(afternoon_failed)}")

        can_entry = all_filter_pass and stance_pass and bc_pass and afternoon_pass

        cur_price = filter_result.tech.current_price if filter_result.tech else 0

        results.append({
            "symbol": sym,
            "name": name,
            "pool_type": pool_type,
            "added_date": added_date,
            "current_price": round(cur_price, 2),
            "change_pct": round(bc.change_pct, 2) if bc.change_pct else 0,
            "last_reject_reason": candidate.get("reject_reasons", []),
            "checks_count": checks_count,
            "data_source": data_source,
            "filters": {
                "layer1_tech": {
                    "passed": l1_pass,
                    "grade": l1.grade,
                    "failed": [] if l1_pass else [getattr(l1, 'downgrade_reason', '') or '未通过'],
                },
                "layer2_capital": {
                    "passed": l2_pass,
                    "grade": l2.grade,
                    "failed": [] if l2_pass else [getattr(l2, 'downgrade_reason', '') or '未通过'],
                },
                "layer3_overbought": {
                    "passed": l3_pass,
                    "grade": l3.grade,
                    "failed": [] if l3_pass else [getattr(l3, 'downgrade_reason', '') or '未通过'],
                },
            },
            "stance_check": {"passed": stance_pass, "detail": f"Pi立场={pi_stance}" + (" (red禁止)" if not stance_pass else "")},
            "buy_confirmation": {"passed": bc_pass, "action": bc.action, "failed": [] if bc_pass else [bc.action]},
            "afternoon_check": {"passed": afternoon_pass, "is_afternoon": is_afternoon, "failed": afternoon_failed},
            "missing_conditions": missing_conditions,
            "can_entry": can_entry,
        })

    long_term = [r for r in results if r["pool_type"] in ("长期", "长期池")]
    short_term = [r for r in results if r["pool_type"] == "短期"]

    warning = None
    if trading["is_blocked"]:
        if trading["period"] == "closing":
            warning = "尾盘时段：14:30后不建仓。以下诊断仅供参考"
        elif trading["period"] == "morning_quiet":
            warning = "早盘冷静期（09:30-09:45）：不自动建仓。以下诊断仅供参考"
        elif not trading["is_trading"]:
            warning = f"非交易时段（{trading['label']}）：使用Tushare日频数据，买入确认/午后限制跳过，结果仅供参考"

    return {
        "pi_stance": pi_stance,
        "trading_period": trading,
        "warning": warning,
        "is_afternoon": is_afternoon,
        "candidates": results,
        "long_term": long_term,
        "short_term": short_term,
        "summary": (
            f"长期候选池 {len(long_term)} 只 / 短期候选池 {len(short_term)} 只, "
            f"共 {sum(1 for r in results if r['can_entry'])} 只满足建仓条件, "
            f"{sum(1 for r in results if not r['can_entry'])} 只条件不足"
        ),
        "mode": "live",
    }


def _classify_pool_type(candidate: dict) -> str:
    """根据入池天数分类长期/短期"""
    from datetime import date as dt_date
    added = candidate.get("added_trade_day", "")
    if not added:
        return "短期"
    try:
        added_dt = dt_date.fromisoformat(added)
        days = (dt_date.today() - added_dt).days
        return "短期" if days <= 3 else "长期"
    except Exception:
        return "短期"
