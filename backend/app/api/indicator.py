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
)
from app.models.market import RealtimeIndicatorItem, RealtimeIndicatorResponse
from app.config import get_settings

router = APIRouter(prefix="/indicator", tags=["Technical Indicators"])

# K 常数（牛股计算器经验参数，约 1.16% 通道宽度）
K_CONSTANT = 0.98848


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
