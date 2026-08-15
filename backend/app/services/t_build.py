# -*- coding: utf-8 -*-
"""做T系统 · 底仓建仓服务（t-position-building capability）。

依据 openspec change `add-t-position-building`（specs/t-position-building + design.md D1-D10）：
- 建仓 = 独立风控的增量买入通道：`validate_build_position` + `build_gateway_execute`，
  独立于做T回转 `validate_order`/`gateway_execute`（一字不改，保护"无底仓禁低吸"红线）。
- 选股：`calc_t_quality` 四维 + 个股趋势闸门（20 日线防单边下行）+ 风险惩罚/成本减项，
  候选三级来源（user / stock 候选池 / Agent 扫描）。
- 时机：冷静期 9:45 后 + 回踩窗口（距高点回撤≥1%）∧ 量比<2.0 ∧ 分时企稳；单票当日单批。
- 规模：单笔≤净值 4/5/8%、单标累计≤10/15/20%、总底仓≤40/55/70%（按 regime 三档缩放）。
- 熔断联动：与回转共享 t_risk_state/t_daily_state；STOP_ALL/日亏熔断/连续亏损阻断建仓。
- 人工升级：B1-B9 清单（首开=human、超阈值=human、CAUTIOUS 自动=human、HALT 禁等）。
- T+1 衔接：建仓当日盘后生成 trade_date=D+1 的 t_conditions（复用 generate_conditions_for_live_pool）。
- 审计：t_build_events（独立于 t_triggers）。
- 参数：分档初值存 t_build_params，P4 ±30% 敏感度扫描后固化。
"""
import json
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.services import t_db
from app.services.t_data_sources import (_normalize_symbol, fetch_minute_bars,
                                         fetch_tencent_quote)
from app.services.t_gateway import (ACCOUNT_T, MAX_DAILY_TURNOVER_RATIO,
                                    _daily_pnl_pct, _limit_status,
                                    _near_limit_down, check_breakers,
                                    get_sellable_ledger, t_net_asset)
from app.services.t_regime import compute_regime

# ────────────────────────────────────────────────────────────────
# 建仓参数（分档初值，P4 敏感度扫描后固化；可被 t_build_params 覆盖）
# ────────────────────────────────────────────────────────────────

BUILD_PARAMS_DEFAULT = {
    # 选股
    "cand_score_min": 0.55,          # 候选短名单门槛
    "build_score_min": 0.60,         # 可建仓门槛
    "build_score_weights": {"quality": 0.8, "trend": 0.1, "source": 0.05, "risk": -0.05},
    "trend_gate": True,              # 个股趋势闸门（20 日线方向）
    # 时机
    "quiet_end": "09:45",            # 冷静期结束（9:30-9:45 不自动建仓）
    "afternoon_ban_from": "13:00",   # 午后禁自动建仓
    "drawdown_min_pct": 1.0,         # 距当日高点回撤 ≥1% 才算回踩
    "vol_ratio_max": 2.0,            # 量比 < 2.0（防异常放量追高）
    "batch_per_symbol_per_day": 1,   # 单票当日建仓批数上限（分批跨日，M1 裁定）
    "max_daily_auto": 3,             # 自动建仓 日上限（笔）
    "max_daily_manual": 5,           # 人工建仓 日上限（笔）
    "max_symbols_being_built": 5,    # 同日排队/在途建仓标的数上限
    # 规模（占 t 净值比例，保守/标准/激进）
    "single_order_pct": {"cons": 0.04, "std": 0.05, "agg": 0.08},
    "per_symbol_cap": {"cons": 0.10, "std": 0.15, "agg": 0.20},
    "total_floor_cap": {"cons": 0.40, "std": 0.55, "agg": 0.70},
    "max_floor_symbols": 10,         # 组合标的数宽松上限（实际受总量上限约束）
    "min_absolute_floor": 20000,     # 单票底仓做T划算下限（元）
    # 衔接
    "next_day_cond_gen": True,       # 建仓当日盘后生成次日条件
    "eod_scan_time": "15:05",        # Worker 盘后条件生成扫描时间
}

# regime → 参数档位
REGIME_TIER = {"ACTIVE": "std", "CAUTIOUS": "cons", "HALT": "cons"}


def _params() -> Dict[str, Any]:
    """合并 t_build_params 覆盖默认参数（DB 优先，缺省回退默认初值）。"""
    stored = t_db.get_build_params() or {}
    merged = dict(BUILD_PARAMS_DEFAULT)
    merged.update(stored)
    return merged


# ────────────────────────────────────────────────────────────────
# t 账户持仓辅助
# ────────────────────────────────────────────────────────────────

def _positions_value(symbol: Optional[str] = None) -> Tuple[float, Dict[str, Any]]:
    """t 账户持仓市值合计；symbol 给定时返回该标市值与持仓信息。"""
    ledger = get_sellable_ledger()
    total = 0.0
    info: Dict[str, Any] = {}
    for sym, item in ledger.items():
        mv = float(item.get("volume") or 0) * float(item.get("avg_price") or 0)
        total += mv
        if symbol is not None and sym == _normalize_symbol(symbol).upper() or sym == symbol:
            info = {**item, "market_value": mv}
    return round(total, 2), info


def _normalize(symbol: str) -> str:
    """统一为腾讯/新浪格式小写（如 sh600519）。"""
    return _normalize_symbol(symbol)


def log_capital_adjust(amount: float, reason: str = "") -> Optional[int]:
    """t 账户资金调额审计（t_build_events，event_type='capital_adjust'）。"""
    return t_db.insert_build_event({
        "symbol": "CASH",
        "event_type": "capital_adjust",
        "side": "adjust",
        "price": amount,
        "volume": 1,
        "amount": round(amount, 2),
        "decision_source": "human",
        "reason": reason or "资金调额",
        "regime": compute_regime().get("regime", "ACTIVE"),
        "status": "executed",
    })


def _is_trading_minute_allowed(now: Optional[datetime] = None) -> Tuple[bool, str]:
    """建仓时段护栏：冷静期 + 午后禁 + 14:45 前（与回转一致）。返回 (allowed, reason)。"""
    now = now or datetime.now()
    hm = now.hour * 100 + now.minute
    if hm < 930 or hm > 1500:
        return False, "非交易时段"
    p = _params()
    quiet = int(p["quiet_end"].replace(":", ""))
    if hm < quiet:
        return False, f"早盘冷静期（9:30-{p['quiet_end']}）不建仓"
    afternoon_ban = int(str(p["afternoon_ban_from"]).replace(":", ""))
    if hm >= afternoon_ban:
        return False, f"午后 {p['afternoon_ban_from']} 后禁自动建仓"
    return True, ""


# ────────────────────────────────────────────────────────────────
# 选股：build_score + 趋势闸门 + 候选
# ────────────────────────────────────────────────────────────────

def _to_ts_code(symbol: str) -> str:
    """股票代码 → tushare 格式（600519.SH / 000001.SZ），兼容腾讯格式（sz000636 / sh600519）。"""
    s = symbol.strip().upper()
    if "." in s:
        return s  # 已是 ts_code
    if s.startswith(("SH", "SZ")):
        return f"{s[2:]}.{s[:2]}"
    return s + (".SH" if s.startswith(("6", "9", "5")) else ".SZ")


# Tushare daily 节流（普通 token 有每分钟调用上限；0.6s 间隔防限流罚时）
_daily_last_call_ts: float = 0.0
_daily_call_lock = threading.Lock()


def _fetch_daily_bars_tushare(symbol: str, count: int = 40, as_of: Optional[str] = None) -> Optional[List[dict]]:
    """Tushare daily 日线（走 .env TUSHARE_TOKEN + TUSHARE_API_URL 代理，规避东财限流）。

    返回 {date, open, close, high, low, vol, amount}，按日期升序（取最近 count 根且 ≤ as_of）；失败返回 None。
    as_of: YYYY-MM-DD 截止日（回测用，防前视；None = 实时今天）。
    """
    global _daily_last_call_ts
    try:
        from app.core.trading._api_config import get_tushare_pro
        from datetime import date, timedelta
        with _daily_call_lock:
            wait = 0.6 - (time.time() - _daily_last_call_ts)
            if wait > 0:
                time.sleep(wait)
            _daily_last_call_ts = time.time()
        pro = get_tushare_pro()
        end = (as_of or date.today().strftime("%Y-%m-%d")).replace("-", "")
        start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=count * 2 + 20)).strftime("%Y%m%d")
        df = pro.daily(ts_code=_to_ts_code(symbol), start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return None
        bars = []
        for _, r in df.iterrows():
            try:
                bars.append({
                    "date": str(r["trade_date"]),
                    "open": float(r["open"]),
                    "close": float(r["close"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "vol": float(r["vol"]),
                    "amount": float(r.get("amount") or 0),
                })
            except (ValueError, TypeError, KeyError):
                continue
        bars.sort(key=lambda x: x["date"])
        return bars[-count:] if bars else None
    except Exception as e:
        print(f"[t-build] Tushare daily 失败 {symbol}: {str(e)[:100]}")
        return None


def _fetch_daily_bars(symbol: str, count: int = 40, as_of: Optional[str] = None) -> Optional[List[dict]]:
    """统一日线入口：Tushare daily 主源（.env 配置），失败降级东财 push2his。as_of 截止防前视。"""
    bars = _fetch_daily_bars_tushare(symbol, count=count, as_of=as_of)
    if bars:
        return bars
    return _fetch_daily_bars_eastmoney(symbol, count=count, as_of=as_of)


def _fetch_daily_bars_eastmoney(symbol: str, count: int = 40, as_of: Optional[str] = None) -> Optional[List[dict]]:
    """东财日线 K 线（klt=101，Tushare 失败时的降级源）；失败返回 None。as_of 截止防前视。"""
    import urllib.request
    secid = _normalize(symbol)
    market = "1" if secid.startswith(("sh",)) else "0"
    end = (as_of or "20500101").replace("-", "")
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={market}.{secid[2:]}&klt=101&fqt=1&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57&end={end}&lmt=" + str(count))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        klines = (data.get("data") or {}).get("klines") or []
        bars = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            if as_of and parts[0].replace("-", "") > end:
                continue
            try:
                bars.append({
                    "date": parts[0], "open": float(parts[1]), "close": float(parts[2]),
                    "high": float(parts[3]), "low": float(parts[4]), "vol": float(parts[5]),
                })
            except (ValueError, IndexError):
                continue
        return bars or None
    except Exception as e:
        print(f"[t-build] 东财日线失败 {symbol}: {str(e)[:100]}")
        return None


def trend_gate(symbol: str, bars: Optional[List[dict]] = None, as_of: Optional[str] = None) -> Tuple[bool, str]:
    """个股趋势闸门：20 日均线方向 + 均线排列，单边下行排除（乘性闸门）。

    bars 可外部传入（build_score 已拉取时避免重复请求）。数据不可得时放行（记 warn），
    由回踩企稳 + 人工升级兜底。as_of 截止防前视（bars 未传时内部拉取用）。
    """
    if bars is None:
        bars = _fetch_daily_bars(symbol, count=40, as_of=as_of)
    if not bars or len(bars) < 25:
        return True, "日线数据不足，闸门放行"
    closes = [b["close"] for b in bars]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma20_prev = sum(closes[-25:-5]) / 20
    down = ma20 < ma20_prev and ma5 < ma10 < ma20
    if down:
        return False, f"单边下行（MA5 {ma5:.2f} < MA10 {ma10:.2f} < MA20 {ma20:.2f}）"
    return True, f"MA20 方向正常（{ma20:.2f}）"


def _quality_from_daily(bars: Optional[List[dict]]) -> Dict[str, Any]:
    """历史日线简化可T质量分（回测建仓模拟专用，防前视；生产仍用 calc_t_quality 实时）。

    用近 20 日日线：平均振幅（(high-low)/pre_close，5% 附近最可T）+ 平均成交额流动性（≥8 亿）。
    返回 {score(0-1), pass_gate, reasons}，权重对齐 calc_t_quality 的量纲（0.5 基准 ± 贡献）。
    """
    if not bars or len(bars) < 5:
        return {"score": 0.0, "pass_gate": False, "reasons": ["日线不足"]}
    recent = bars[-20:]
    amps, amounts = [], []
    for i in range(1, len(recent)):
        b = recent[i]
        prev_close = recent[i - 1]["close"]
        if prev_close <= 0:
            continue
        amps.append((float(b["high"]) - float(b["low"])) / prev_close * 100)
        amounts.append(float(b.get("amount") or 0))
    if not amps:
        return {"score": 0.0, "pass_gate": False, "reasons": ["无有效日线"]}
    avg_amp = sum(amps) / len(amps)
    avg_amount = sum(amounts) / len(amounts) if amounts else 0.0
    # 振幅贡献：3%~7% 最可T → +0.2；<1% 或 >12% → -0.2
    amp_score = 0.2 if 3.0 <= avg_amp <= 7.0 else (-0.2 if avg_amp < 1.0 or avg_amp > 12.0 else 0.0)
    # 流动性贡献：成交额 ≥ 8 亿 → +0.2；< 2 亿 → -0.2（tushare daily.amount 单位千元，×1000 转元）
    avg_amount_yuan = avg_amount * 1000
    liq_score = 0.2 if avg_amount_yuan >= 8e8 else (-0.2 if avg_amount_yuan < 2e8 else 0.0)
    score = round(max(0.0, min(0.5 + amp_score + liq_score, 1.0)), 4)
    reasons = []
    if amp_score < 0:
        reasons.append(f"振幅不适宜（{avg_amp:.1f}%）")
    if liq_score < 0:
        reasons.append(f"流动性不足（日均 {avg_amount_yuan / 1e8:.1f} 亿）")
    return {"score": score, "pass_gate": score >= 0.5, "reasons": reasons}


def build_score(symbol: str, source: str = "user", as_of: Optional[str] = None,
                quality_override: Optional[Dict[str, Any]] = None,
                bars: Optional[List[dict]] = None) -> Dict[str, Any]:
    """建仓打分：quality（calc_t_quality 四维）+ 趋势 + 来源 + 风险惩罚。

    as_of: YYYY-MM-DD 截止日（回测用，日线趋势/风险历史化防前视；None = 实时）。
    quality_override: 回测传入的历史质量分（_quality_from_daily 结果）；None 时生产用 calc_t_quality。
    bars: 日线注入（回测用缓存数据，零网络）；None 时内部拉取（as_of 截止）。
    返回 {score, pass_gate, reasons, quality, trend, source}。
    """
    from app.services.t_pool import calc_t_quality
    p = _params()
    w = p["build_score_weights"]
    if quality_override is not None:
        q = quality_override
    else:
        q = calc_t_quality(symbol) or {}
    quality_score = float(q.get("score") or 0)
    pass_quality = bool(q.get("pass_gate"))

    # 日线单次拉取，供趋势闸门 + 风险惩罚共用（避免重复请求）；as_of 截止防前视
    if bars is None:
        bars = _fetch_daily_bars(symbol, count=40, as_of=as_of)

    # 趋势闸门（乘性）
    trend_ok, trend_note = trend_gate(symbol, bars=bars, as_of=as_of)
    trend_add = 0.1 if trend_ok else 0.0

    # 风险惩罚（简化：近 5 日隔夜跳空均值 > 3% 记 -0.1；涨跌停频率 > 10% 记 -0.1）
    risk_penalty = 0.0
    if bars and len(bars) >= 6:
        gaps = []
        limit_count = 0
        for i in range(1, len(bars)):
            prev_close = bars[i - 1]["close"]
            if prev_close <= 0:
                continue
            gaps.append(abs(bars[i]["open"] - prev_close) / prev_close * 100)
            chg = (bars[i]["close"] - prev_close) / prev_close * 100
            if abs(chg) >= 9.8:
                limit_count += 1
        if gaps:
            avg_gap = sum(gaps) / len(gaps)
            if avg_gap > 3.0:
                risk_penalty += 0.1
        if limit_count / max(len(bars), 1) > 0.1:
            risk_penalty += 0.1

    source_add = {"user": 0.05, "pool": 0.0, "scan": 0.0}.get(source, 0.0)
    raw = (w["quality"] * quality_score + w["trend"] * trend_add
           + w["source"] * source_add - w["risk"] * risk_penalty)
    # 归一化到 0-1 量纲（quality_score 理论 0-1）
    score = round(max(0.0, min(raw, 1.0)), 4)

    reasons = list(q.get("reasons") or [])
    if not trend_ok:
        reasons.append(trend_note)
    if risk_penalty > 0:
        reasons.append("风险惩罚（隔夜跳空/涨跌停频率高）")
    if not pass_quality:
        reasons.append("可T质量不达标")

    return {
        "symbol": symbol,
        "score": score,
        "pass_gate": pass_quality and trend_ok and score >= float(p["cand_score_min"]),
        "quality": q,
        "trend": {"ok": trend_ok, "note": trend_note},
        "risk_penalty": risk_penalty,
        "source": source,
        "reasons": reasons,
    }


def _load_candidate_symbols() -> List[str]:
    """候选来源：stock 候选池（ready 状态，前 20）+ 用户指定（外部入参）。"""
    try:
        from app.services.candidate_pool import get_candidate_pool
        pool = get_candidate_pool()
        if hasattr(pool, "get_ready"):
            cands = pool.get_ready()
        elif hasattr(pool, "ready"):
            cands = pool.ready()
        else:
            cands = pool._data.get("candidates", []) if hasattr(pool, "_data") else []
        return [str(item.get("symbol") or item) for item in (cands or [])[:20] if item]
    except Exception as e:
        print(f"[t-build] 候选池读取失败: {e}")
        return []


# ────────────────────────────────────────────────────────────────
# 全市场扫描（source="scan"）：stock_basic 全列表 → 日频粗筛 → 精筛
# 对齐设计"日频低频 ≤50 票/日"：粗筛结果当日缓存，精筛逐票限流
# ────────────────────────────────────────────────────────────────

SCAN_COARSE_MIN_AMOUNT = 8e8      # 粗筛：近端成交额 ≥ 8 亿（与 calc_t_quality 流动性口径一致）
SCAN_COARSE_AMPLITUDE = (1.0, 10.0)  # 粗筛：振幅区间（排除死票/妖票）
SCAN_MAX_DAILY = 50               # 精筛单次上限（票/日，1s 节流）
_SCAN_CACHE: Dict[str, Any] = {"date": "", "symbols": [], "active": []}


def _fetch_all_a_symbols() -> List[Dict[str, str]]:
    """全市场沪深 A 股列表（Tushare stock_basic，走 .env TUSHARE_TOKEN + TUSHARE_API_URL 代理）。

    过滤：仅上市(L)、沪深交易所、排除 ST/*ST/退市/北交所/次新(<60 天)。
    失败返回 []（scan 退化为空，不阻断其他来源）。
    """
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        df = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,symbol,name,area,industry,list_date,exchange")
        if df is None or len(df) == 0:
            return []
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=60)).strftime("%Y%m%d")
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("symbol") or "")
            exch = str(r.get("exchange") or "")
            name = str(r.get("name") or "")
            list_date = str(r.get("list_date") or "")
            # 排除北交所（BJ）/ 科创板已含(688)；排除 ST/退市/次新
            if exch == "BJ":
                continue
            if any(k in name for k in ("ST", "*ST", "退")):
                continue
            if code.startswith(("4", "8", "9")) and exch == "BJ":
                continue
            if list_date and list_date > cutoff:
                continue
            ts_code = str(r.get("ts_code") or "")
            rows.append({"symbol": code, "ts_code": ts_code, "name": name, "exchange": exch})
        print(f"[t-build] stock_basic 全市场列表: {len(rows)} 只（沪深A股，过滤 ST/北交所/次新）")
        return rows
    except Exception as e:
        print(f"[t-build] stock_basic 获取失败: {str(e)[:120]}")
        return []


def _coarse_filter_active(symbols: List[Dict[str, str]], max_batches: int = 6,
                          batch_size: int = 50) -> List[str]:
    """日频粗筛：腾讯 qt 批量拉成交额/振幅，保留 成交额≥8亿 ∧ 振幅∈[1%,10%]，按成交额降序。

    返回活跃票 symbol 列表（腾讯格式 sh600519）。max_batches×batch_size 限制单次网络量。
    """
    active: List[Dict[str, float]] = []
    for i in range(0, min(len(symbols), max_batches * batch_size), batch_size):
        batch = symbols[i:i + batch_size]
        norm = [_normalize_symbol(r["symbol"]) for r in batch]
        try:
            quotes = fetch_tencent_quote(norm)
        except Exception as e:
            print(f"[t-build] 粗筛取价失败: {str(e)[:80]}")
            continue
        for r, ns in zip(batch, norm):
            q = quotes.get(ns) or {}
            amount = float(q.get("amount") or 0) * 1e4  # qt amount 单位万 → 元
            amplitude = float(q.get("amplitude") or 0)
            if amount >= SCAN_COARSE_MIN_AMOUNT and SCAN_COARSE_AMPLITUDE[0] <= amplitude <= SCAN_COARSE_AMPLITUDE[1]:
                active.append({"symbol": ns, "amount": amount})
        time.sleep(0.3)
    active.sort(key=lambda x: x["amount"], reverse=True)
    print(f"[t-build] 粗筛活跃池: {len(active)} 只（成交额≥8亿 ∧ 振幅{SCAN_COARSE_AMPLITUDE}）")
    return [a["symbol"] for a in active]


def scan_t_candidates(limit: int = 20, source: str = "pool",
                      as_of: Optional[str] = None,
                      quality_override_fn: Optional[callable] = None) -> List[Dict[str, Any]]:
    """扫描建仓候选短名单（来源：user 指定列表 / pool 候选池 / scan 全市场粗筛）。

    - scan：stock_basic 全市场列表 → 日频粗筛（成交额/振幅，当日缓存）→ 精筛（calc_t_quality
      + 趋势闸门 + 风险惩罚），单次 ≤50 票（1s 节流）。粗筛结果当日缓存，不重复拉全市场。
    - 日频低频：调用方（Agent/前端）不应高频触发；缓存保证重复调用不重复网络请求。
    - as_of: 回测用截止日（日线历史化）；quality_override_fn: 回测质量分函数（防前视）。
    """
    if source == "user":
        raise ValueError("user 来源需显式传入 symbols")
    if source == "scan":
        today = datetime.now().strftime("%Y-%m-%d")
        if _SCAN_CACHE.get("date") != today or not _SCAN_CACHE.get("active"):
            all_syms = _fetch_all_a_symbols()
            if not all_syms:
                return []
            active = _coarse_filter_active(all_syms)
            _SCAN_CACHE.update({"date": today, "symbols": all_syms, "active": active})
        symbols = _SCAN_CACHE["active"][:max(limit, SCAN_MAX_DAILY)]
    else:
        symbols = _load_candidate_symbols()
    if not symbols:
        return []
    results = []
    for sym in symbols[:limit]:
        try:
            quality = quality_override_fn(sym) if quality_override_fn else None
            r = build_score(sym, source=source, as_of=as_of, quality_override=quality)
            results.append(r)
        except Exception as e:
            print(f"[t-build] 扫描 {sym} 失败: {e}")
        time.sleep(1.0)  # 1s 节流（分钟线/日线限流）
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ────────────────────────────────────────────────────────────────
# 建仓规模计算
# ────────────────────────────────────────────────────────────────

def build_sizing(symbol: str, price: float, net_asset: Optional[float] = None,
                 total_floor_value: Optional[float] = None,
                 symbol_value: Optional[float] = None,
                 regime: str = "ACTIVE") -> Dict[str, Any]:
    """建仓规模计算：按当前 regime 档位给出单笔/单标/总底仓上限与建议股数。

    生产（不传注入参数）：net/total_floor_value/symbol_value 取 t 账户实时状态，regime 实时。
    回测（组合引擎注入）：net_asset=组合净值、total_floor_value=组合已分配建仓市值、
    symbol_value=该标的是否已建仓、regime=历史档位——规则同源，数据历史化。
    返回 {tier, net_asset, single_max_amount, per_symbol_max_amount, total_floor_max,
          current_floor_value, symbol_value, suggest_volume, reasons, pass}。
    """
    p = _params()
    if regime == "REALTIME":
        regime = compute_regime().get("regime", "ACTIVE")
    tier = REGIME_TIER.get(regime, "std")
    net = net_asset if net_asset is not None else t_net_asset()
    if net <= 0:
        return {"pass": False, "reason": "t 账户净值不可用", "tier": tier}

    single_pct = p["single_order_pct"].get(tier, 0.05)
    per_symbol_pct = p["per_symbol_cap"].get(tier, 0.15)
    total_pct = p["total_floor_cap"].get(tier, 0.55)

    if total_floor_value is None:
        total_floor_value, _ = _positions_value()
    if symbol_value is None:
        ledger = get_sellable_ledger()
        item = ledger.get(_normalize(symbol).upper()) or ledger.get(symbol) or {}
        symbol_value = float(item.get("volume") or 0) * float(item.get("avg_price") or 0)

    single_max = net * single_pct
    per_symbol_max = net * per_symbol_pct
    total_max = net * total_pct

    reasons = []
    if symbol_value >= per_symbol_max:
        reasons.append(f"单标底仓已达上限（{symbol_value:.0f} ≥ {per_symbol_max:.0f}）")
    if total_floor_value + single_max > total_max:
        reasons.append(f"总底仓超上限（{total_floor_value:.0f} + 本笔 > {total_max:.0f}）")

    # 高价股保底：单笔上限 = max(净值×单笔%, 100股×价格)（A股最小交易单位 100 股），
    # 但仍受单标上限（净值×15%）约束——茅台(1300元)也能建 100 股，避免"建议股数不足 100"泛滥。
    if price > 0 and not reasons:
        suggest_volume = int(single_max / price / 100) * 100
        if suggest_volume < 100:
            # 保底 100 股：若 100 股金额未超单标上限则放行
            if price * 100 <= per_symbol_max:
                suggest_volume = 100
            else:
                reasons.append(f"高价股 100 股超单标上限（{price*100:.0f} > {per_symbol_max:.0f}）")
    elif price > 0:
        suggest_volume = int(single_max / price / 100) * 100

    return {
        "pass": not reasons and suggest_volume >= 100,
        "tier": tier,
        "regime": regime,
        "net_asset": round(net, 2),
        "single_max_amount": round(single_max, 2),
        "per_symbol_max_amount": round(per_symbol_max, 2),
        "total_floor_max": round(total_max, 2),
        "current_floor_value": round(total_floor_value, 2),
        "symbol_value": round(symbol_value, 2),
        "suggest_volume": suggest_volume,
        "reason": "; ".join(reasons) or ("建议股数不足 100" if suggest_volume < 100 else ""),
    }


# ────────────────────────────────────────────────────────────────
# 建仓触发确认（回踩 ∧ 量比<2 ∧ 分时企稳）
# ────────────────────────────────────────────────────────────────

def confirm_build_timing(symbol: str) -> Tuple[bool, str, Optional[dict]]:
    """建仓触发确认：现价回踩（距当日高点回撤≥1%）∧ 量比<2.0 ∧ 分时企稳。

    量比用分钟量近似（分钟量均值/近 6 日同刻均值），企稳复用 m1 是否创新低。
    返回 (ok, reason, quote)。
    """
    p = _params()
    q = fetch_tencent_quote([_normalize(symbol)]).get(_normalize(symbol)) or {}
    if not q or not float(q.get("current") or 0):
        return False, "实时行情不可用", None
    current = float(q["current"])
    high = float(q.get("high") or 0)
    if high <= 0:
        return False, "日内高点不可用", q

    drawdown = (high - current) / high * 100
    if drawdown < float(p["drawdown_min_pct"]):
        return False, f"未回踩（距高点回撤 {drawdown:.2f}% < {p['drawdown_min_pct']}%）", q

    # 量比近似：当前 m1 量 / 近 6 日同刻均值量（数据不足放行）
    vol_ratio = _approx_volume_ratio(symbol)
    if vol_ratio is not None and vol_ratio >= float(p["vol_ratio_max"]):
        return False, f"量比 {vol_ratio:.2f} ≥ {p['vol_ratio_max']}（异常放量，疑似追高）", q

    stable, stable_note = _stabilize_not_new_low(symbol, current)
    if not stable:
        return False, stable_note, q

    return True, f"回踩 {drawdown:.2f}% ∧ 量比 {vol_ratio if vol_ratio is not None else 'N/A'} ∧ 企稳", q


def _approx_volume_ratio(symbol: str) -> Optional[float]:
    """分钟量近似量比：当前时段 m5 平均量 / 近 6 日同刻 m5 平均量。数据不足返回 None。"""
    try:
        bars = fetch_minute_bars(symbol, freq="m5", count=320)
        if not bars or len(bars) < 60:
            return None
        # 近 6 个交易日 ≈ 每交易日 48 根 m5；取最近 6 日非今日同刻均值（简化：全量均值）
        vols = [float(b.get("vol") or 0) for b in bars]
        recent = vols[-6:]
        base = sum(recent) / len(recent) if recent else 0
        if base <= 0:
            return None
        return round(sum(vols) / len(vols) / base, 3)
    except Exception as e:
        print(f"[t-build] 量比近似失败 {symbol}: {e}")
        return None


def _stabilize_not_new_low(symbol: str, current: float) -> Tuple[bool, str]:
    """分时企稳：m1 分钟线当日未创新低（近 10 根最低 ≥ 当前 × 0.999）。"""
    try:
        bars = fetch_minute_bars(symbol, freq="m1", count=120)
        if not bars:
            return True, "无分钟线，企稳放行"
        today = datetime.now().strftime("%Y-%m-%d")
        today_lows = [float(b["low"]) for b in bars if str(b["time"]).startswith(today)]
        if not today_lows:
            return True, "无当日分钟线，企稳放行"
        day_low = min(today_lows)
        if current < day_low * 0.999:
            return False, f"仍在创新低（日低 {day_low:.2f}）"
        return True, "企稳"
    except Exception as e:
        print(f"[t-build] 企稳判断失败 {symbol}: {e}")
        return True, "企稳判断失败放行"


# ────────────────────────────────────────────────────────────────
# 建仓人工升级（B 清单，对齐 classify_escalation 哲学：默认自动、异常升级）
# ────────────────────────────────────────────────────────────────

def classify_build_escalation(symbol: str, amount: float, regime: str,
                              decision_source: str = "agent",
                              allow_first_open: bool = False) -> Tuple[str, str]:
    """建仓升级分类 → (mode, reason)。mode ∈ auto / human_confirm / blocked。

    B1 首开新标的=human；B2 单笔超标准档=human；B3 HALT=blocked（含人工）；
    B4 CAUTIOUS 自动=human；B5 连续亏损期=human+禁自动；B6 近跌停=human；
    B7 日亏预警=-human；B8 当日累计触犯建仓风控≥2=human。
    allow_first_open=True（每日自动选股来源）：跳过 B1 首开（仍保留 B2-B8 全部风控）。
    """
    p = _params()
    risk = t_db.get_risk_state() or {}

    # B3：HALT 全禁（含人工）
    if regime == "HALT":
        return "blocked", "regime=HALT 禁止一切建仓"

    # B5：连续亏损期
    if int(risk.get("consecutive_losses") or 0) >= 2:
        return "human_confirm", "连续触犯风控，建仓强制人工+临时禁自动"

    # B7：日亏预警线
    pnl_pct = _daily_pnl_pct()
    if pnl_pct <= -1.0:
        return "human_confirm", f"接近日亏预警线（{pnl_pct:.2f}%），建仓需人工"

    # B4：CAUTIOUS 自动建仓 → human
    if regime == "CAUTIOUS":
        return "human_confirm", "regime=CAUTIOUS 建仓仅人工确认"

    # B2：单笔超标准档阈值
    net = t_net_asset()
    std_single = net * p["single_order_pct"].get("std", 0.05)
    if amount > std_single:
        return "human_confirm", f"单笔金额 {amount:.0f} 超标准档上限 {std_single:.0f}，需人工"

    # B1：首开新标的（t 账户从未持有）——每日自动选股来源可跳过
    if not allow_first_open:
        ledger = get_sellable_ledger()
        if symbol not in ledger:
            return "human_confirm", "首次建仓新标的（首开风险），需人工确认"

    # B6：近跌停
    q = fetch_tencent_quote([_normalize(symbol)]).get(_normalize(symbol)) or {}
    if q and _near_limit_down(q):
        return "human_confirm", "近跌停（≤-8%），建仓需人工"

    # B8：当日累计建仓风控拒单 ≥ 2（近似用当日 rejected 计数）
    if t_db.count_today_builds() >= int(p.get("max_daily_auto", 3)):
        return "human_confirm", "当日建仓笔数达上限，后续需人工"

    return "auto", ""


# ────────────────────────────────────────────────────────────────
# 建仓网关校验（独立校验链，不碰 validate_order）
# ────────────────────────────────────────────────────────────────

def validate_build_position(symbol: str, price: float, volume: int,
                            reason: str = "", decision_source: str = "agent",
                            force_human: bool = False) -> Dict[str, Any]:
    """建仓校验链（独立于做T回转 validate_order）：返回 {pass, mode, level, reason, warn}。

    校验项：账户白名单 + check_breakers（STOP_ALL/人工锁/日亏熔断/连续亏损）+
    金额（单笔/单标累计/总底仓）+ regime 门 + 冷静期/午后 + 日建仓上限（自动≤3/人工≤5/单票≤1）+
    涨跌停封板 + 建仓升级分类（B 清单）+ 可选目标池白名单。
    """
    result: Dict[str, Any] = {"pass": False, "mode": "blocked", "level": "hard", "reason": "", "warn": []}
    try:
        # 0) 账户白名单
        if decision_source not in ("agent", "human", "daily_auto", "ai_led"):
            result["reason"] = "非法决策来源"
            return result

        # 1) 全局熔断（与回转共享）
        broken, why = check_breakers()
        if broken:
            result["reason"] = f"建仓被熔断: {why}"
            return result

        # 2) regime 门
        regime_state = compute_regime()
        regime = regime_state.get("regime", "ACTIVE")
        allow_first_open = decision_source in ("daily_auto", "ai_led")
        mode, up_reason = classify_build_escalation(symbol, price * volume, regime,
                                                    decision_source,
                                                    allow_first_open=allow_first_open)
        if mode == "blocked":
            result["reason"] = up_reason
            return result

        # 3) 时段护栏（自动建仓强制；人工建仓仅非交易时段拒）
        if decision_source in ("agent", "daily_auto", "ai_led"):
            ok, why = _is_trading_minute_allowed()
            if not ok:
                result["reason"] = why
                return result

        # 4) 涨跌停封板（跌停禁建/涨停禁建）
        q = fetch_tencent_quote([_normalize(symbol)]).get(_normalize(symbol)) or {}
        if q:
            if _limit_status(q, "buy") == "block":
                result["reason"] = "跌停封板禁建仓"
                return result
            if _limit_status(q, "sell") == "block":
                result["reason"] = "涨停封板禁建仓（追高）"
                return result

        # 5) 金额/规模（三档上限）
        sizing = build_sizing(symbol, price)
        if not sizing["pass"]:
            result["level"] = "ledger"
            result["reason"] = sizing["reason"] or "规模校验不通过"
            return result
        amount = price * volume
        if amount > sizing["single_max_amount"]:
            result["level"] = "ledger"
            result["reason"] = f"单笔 {amount:.0f} 超档位上限 {sizing['single_max_amount']:.0f}"
            return result

        # 6) 日建仓上限（自动≤3 / 人工≤5；单票≤1）
        p = _params()
        cap = int(p["max_daily_manual"]) if decision_source == "human" else int(p["max_daily_auto"])
        if t_db.count_today_builds() >= cap:
            result["level"] = "ledger"
            result["reason"] = f"当日建仓笔数已达上限（{cap}）"
            return result
        if t_db.count_today_builds(symbol=_normalize(symbol).upper()) >= int(p["batch_per_symbol_per_day"]):
            result["level"] = "ledger"
            result["reason"] = "单票当日已建仓，分批须跨日"
            return result

        # 7) 人工升级分流（daily_auto/ai_led 遇 human_confirm 同样升级，不自动放行）
        if mode == "human_confirm" and decision_source in ("agent", "daily_auto", "ai_led") and not force_human:
            result.update({"pass": True, "mode": "human_confirm", "level": "gate", "reason": up_reason})
            return result
        if force_human and decision_source == "human" and mode == "human_confirm":
            result.update({"pass": True, "mode": "human_confirm", "level": "gate", "reason": up_reason})
            return result

        # 8) 过（自动放行）
        warns = []
        if mode == "human_confirm":
            warns.append(up_reason)
        result.update({"pass": True, "mode": mode, "level": "ok", "reason": up_reason, "warn": warns})
        return result
    except Exception as e:
        result["reason"] = f"建仓校验异常: {e}"
        return result


# ────────────────────────────────────────────────────────────────
# 建仓执行（唯一放行者）
# ────────────────────────────────────────────────────────────────

def build_gateway_execute(symbol: str, price: float, volume: int,
                          reason: str = "", decision_source: str = "agent",
                          event_id: Optional[int] = None,
                          force_human: bool = False) -> Dict[str, Any]:
    """建仓执行唯一入口：建仓校验通过才调用执行器撮合（account_id='t'）。

    成功 → 更新日账本（建仓名义额入 daily_turnover_amount，来源 build）→ 更新审计事件。
    """
    # 0) 审计先行（记录请求）
    regime = compute_regime().get("regime", "ACTIVE")
    before = _positions_value(symbol)[1] if symbol else {}
    ev_id = event_id
    if ev_id is None:
        ev_id = t_db.insert_build_event({
            "symbol": _normalize(symbol).upper(),
            "event_type": "build_position",
            "side": "buy",
            "price": price,
            "volume": volume,
            "amount": round(price * volume, 2),
            "decision_source": decision_source,
            "reason": reason or "做T底仓建仓",
            "regime": regime,
            "position_before": before,
            "status": "pending_confirmation",
        })

    # 1) 校验
    check = validate_build_position(symbol, price, volume, reason=reason,
                                    decision_source=decision_source, force_human=force_human)
    if not check["pass"]:
        t_db.update_build_event(ev_id, status="rejected",
                                reason=f"{check['reason']}（level={check.get('level')}）")
        return {"status": "rejected", "reason": check["reason"], "level": check.get("level"), "mode": check.get("mode")}

    if check["mode"] == "human_confirm" and decision_source in ("agent", "daily_auto", "ai_led") and not force_human:
        # 升级人工：事件保持 pending_confirmation，等待人工确认端点放行
        t_db.update_build_event(ev_id, status="human_confirm",
                                reason=check.get("reason") or "人工确认")
        return {"status": "human_confirm", "event_id": ev_id, "reason": check.get("reason") or "需人工确认"}

    # 2) 撮合（复用做T执行器隔离：account_id='t'）
    try:
        from app.core.trading.marcus_trade import MarcusVNPyExecutor
        from paper_engine import PaperTradingEngine
        from workspace_detector import DATA_DIR
        engine = PaperTradingEngine(data_dir=str(DATA_DIR), account_id=ACCOUNT_T)
        executor = MarcusVNPyExecutor(engine=engine, account_id=ACCOUNT_T)
        result = executor.buy(symbol=_normalize(symbol).upper(), price=price,
                              volume=volume, reason=reason or "做T底仓建仓")
    except Exception as e:
        t_db.update_build_event(ev_id, status="rejected", reason=f"执行异常: {e}")
        return {"status": "rejected", "reason": f"执行异常: {e}"}

    ok = result.get("status") in ("success", "filled")
    if ok:
        executed_price = float(result.get("price", price) or price)
        t_db.update_build_event(ev_id, status="executed", executed_price=executed_price,
                                reason="建仓成交")
        # 更新日账本（建仓名义额入 daily_turnover_amount，来源 build）
        _update_build_ledger(symbol, price, volume)
        # 建仓成交 → 次日条件衔接（T+1：当日 sellable=0，生成 D+1 条件）
        try:
            auto_gen_conditions_for_build(symbol, executed_price)
        except Exception as e:
            print(f"[t-build] 次日条件生成失败 {symbol}: {e}")
        return {"status": "success", "event_id": ev_id, **result}
    t_db.update_build_event(ev_id, status="rejected", reason=result.get("reason") or "撮合失败")
    return {"status": "rejected", "reason": result.get("reason") or "撮合失败", **result}


def _update_build_ledger(symbol: str, price: float, volume: int):
    """建仓成交更新 t_daily_state：建仓名义额计入 daily_turnover_amount（来源 build）。"""
    try:
        daily = t_db.get_daily_state() or {}
        amount = float(daily.get("daily_turnover_amount") or 0) + price * volume
        buy_count = int(daily.get("buy_count") or 0) + 1
        t_db.upsert_daily_state({
            "daily_turnover_amount": round(amount, 2),
            "buy_count": buy_count,
        })
    except Exception as e:
        print(f"[t-build] 建仓账本更新失败: {e}")


def build_t_position(symbol: str, price: float, volume: Optional[int] = None,
                     reason: str = "", decision_source: str = "agent",
                     skip_timing: bool = False, force_human: bool = False) -> Dict[str, Any]:
    """建仓高层入口（Agent/API 调用）：触发确认 → 规模 → 升级 → 执行。

    - 自动（agent）：要求时机确认通过（回踩∧量比∧企稳）+ 规模计算 + 升级分类。
    - 人工（human）：跳过时机确认（人工已判断），但金额/熔断/时段护栏仍强制。
    """
    if volume is None:
        sizing = build_sizing(symbol, price)
        if not sizing["pass"]:
            return {"status": "rejected", "reason": sizing["reason"] or "规模校验不通过", **sizing}
        volume = sizing["suggest_volume"]
    if not volume or volume < 100:
        return {"status": "rejected", "reason": "建议股数不足 100 股"}

    if decision_source == "agent" and not skip_timing:
        ok, why, quote = confirm_build_timing(symbol)
        if not ok:
            return {"status": "rejected", "reason": f"时机未确认: {why}"}

    return build_gateway_execute(symbol, price, volume, reason=reason,
                                 decision_source=decision_source, force_human=force_human)


# ────────────────────────────────────────────────────────────────
# 建仓后次日条件衔接（T+1：建仓当日盘后生成 D+1 条件）
# ────────────────────────────────────────────────────────────────

def auto_gen_conditions_for_build(symbol: str, avg_price: float) -> bool:
    """为刚建仓标的生成次日（D+1）t_conditions（复用 generate_conditions_for_live_pool 模板）。

    建仓当日不生成当日条件（sellable=0 无法触发）；次日该标的进 live 池后可做T。
    """
    try:
        from datetime import date, timedelta
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y%m%d")
        if avg_price <= 0:
            return False
        target = round(avg_price * 0.98, 2)
        cond = {
            "account_id": ACCOUNT_T,
            "symbol": _normalize(symbol).upper(),
            "trigger_kind": "low_buy",
            "trade_date": tomorrow,
            "target_price": target,
            "reinform_price": round(target * 1.004, 2),
            "sell_target_price": round(avg_price * 1.015, 2),
            "stop_loss_price": round(target * 0.97, 2),
            "vol_ratio_thresh": 1.5,
            "stabilize_level": "not_new_low",
            "time_stop_close": "14:45",
            "start_time": "09:30",
            "end_time": "14:45",
            "regime_gate": "ALLOWED",
            "status": "active",
            "armed": 1,
        }
        cid = t_db.upsert_condition(cond)
        return cid is not None
    except Exception as e:
        print(f"[t-build] 次日条件生成失败 {symbol}: {e}")
        return False


def auto_gen_conditions_for_live_pool() -> int:
    """盘后任务：为 live 池缺失次日条件的标的补生成（复用 t_pool 模板）。"""
    try:
        from app.services.t_pool import generate_conditions_for_live_pool
        regime = compute_regime().get("regime", "ACTIVE")
        if regime == "HALT":
            return 0
        created = generate_conditions_for_live_pool(regime=regime)
        return len(created)
    except Exception as e:
        print(f"[t-build] live 池条件补生成失败: {e}")
        return 0


# ────────────────────────────────────────────────────────────────
# 每日自动选股闭环（盘后选股 → 次日盘中自动建仓 → 做T）
# ────────────────────────────────────────────────────────────────

DEFAULT_AUTO_SELECT_LIMIT = 5      # 每日自动选股数量上限
DEFAULT_AUTO_BUILD_ENABLED = True  # 次日自动建仓开关（走网关风控，仅放开 B1 首开）


def _next_trade_date() -> str:
    """下一交易日（YYYY-MM-DD，brze trade_cal 不可用时工作日近似）。"""
    try:
        from app.services.t_backtest_data import resolve_trade_days
        from datetime import date, timedelta
        days = resolve_trade_days(
            (date.today() + timedelta(days=1)).strftime("%Y-%m-%d"),
            (date.today() + timedelta(days=5)).strftime("%Y-%m-%d"))
        return (days[0][:4] + "-" + days[0][4:6] + "-" + days[0][6:8]) if days else \
            (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception as e:
        print(f"[t-build] 下一交易日计算失败: {e}")
        from datetime import date, timedelta
        return (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")


def daily_auto_select(limit: int = DEFAULT_AUTO_SELECT_LIMIT) -> List[Dict[str, Any]]:
    """盘后自动选股：全市场扫描 → 达标标的写入 t_build_scan_results（trade_date=下一交易日）。

    返回写入的候选列表。防前视：精筛用 as_of=今日的日线（趋势/风险用当日及以前数据）。
    """
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        cands = scan_t_candidates(limit=limit, source="scan", as_of=datetime.now().strftime("%Y-%m-%d"))
        passed = [c for c in cands if c.get("symbol") and c.get("pass_gate")]
        if not passed:
            print("[t-build] 每日自动选股: 无达标标的")
            return []
        next_td = _next_trade_date()
        rows = []
        db = SessionLocal()
        try:
            for c in passed:
                sym = _normalize(c["symbol"]).upper()
                # 幂等：同日同标的已存在则跳过
                exists = db.execute(text(
                    "SELECT 1 FROM t_build_scan_results WHERE trade_date=:td AND symbol=:sym"
                ), {"td": next_td, "sym": sym}).fetchone()
                if exists:
                    continue
                row = db.execute(text(
                    "INSERT INTO t_build_scan_results (trade_date, symbol, score, reasons, trend, status) "
                    "VALUES (:td, :sym, :score, :reasons, :trend, 'pending') RETURNING id"
                ), {
                    "td": next_td, "sym": sym,
                    "score": float(c.get("score") or 0),
                    "reasons": json.dumps(c.get("reasons") or [], ensure_ascii=False),
                    "trend": str((c.get("trend") or {}).get("note") or "")[:250],
                }).fetchone()
                rows.append({"id": row[0], "symbol": sym, "score": c.get("score")})
            db.commit()
        finally:
            db.close()
        print(f"[t-build] 每日自动选股完成: 达标 {len(passed)} 只，写入 {len(rows)} 条（{next_td} 执行）")
        return rows
    except Exception as e:
        print(f"[t-build] 每日自动选股失败: {e}")
        return []


def daily_auto_build() -> List[Dict[str, Any]]:
    """盘中处理今日 pending 建仓候选：时机确认 → 自动建仓（decision_source=daily_auto，仅放开 B1）。

    安全阀：confirm_build_timing（回踩/量比/企稳）+ validate_build_position 全链
    （熔断/regime/时段/涨跌停/三档资金/日上限/单票上限）；human_confirm 候选标记 skipped。
    """
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        today = datetime.now().strftime("%Y-%m-%d")
        results: List[Dict[str, Any]] = []
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT * FROM t_build_scan_results WHERE trade_date=:td AND status='pending' ORDER BY score DESC"
            ), {"td": today}).mappings().all()
            for r in rows:
                sym = str(r["symbol"])
                # 时机确认（盘中实时：回踩/量比/企稳）
                ok, why, quote = confirm_build_timing(sym)
                if not ok:
                    results.append({"symbol": sym, "action": "wait", "reason": f"时机未确认: {why}"})
                    continue
                price = float((quote or {}).get("current") or 0)
                if price <= 0:
                    results.append({"symbol": sym, "action": "skip", "reason": "实时价不可用"})
                    db.execute(text(
                        "UPDATE t_build_scan_results SET status='skipped', built_at=now() WHERE id=:id"),
                        {"id": r["id"]})
                    continue
                out = build_t_position(sym, price, decision_source="daily_auto",
                                       reason="每日自动选股自动建仓")
                if out.get("status") == "success":
                    db.execute(text(
                        "UPDATE t_build_scan_results SET status='built', built_at=now() WHERE id=:id"),
                        {"id": r["id"]})
                    results.append({"symbol": sym, "action": "built", "price": price,
                                    "reason": out.get("reason") or "建仓成交"})
                elif out.get("status") == "human_confirm":
                    db.execute(text(
                        "UPDATE t_build_scan_results SET status='skipped', built_at=now() WHERE id=:id"),
                        {"id": r["id"]})
                    results.append({"symbol": sym, "action": "skipped",
                                    "reason": f"升级人工（{out.get('reason')}）"})
                else:
                    results.append({"symbol": sym, "action": "wait",
                                    "reason": f"建仓被拒: {out.get('reason')}"})
            db.commit()
        finally:
            db.close()
        done = [r for r in results if r["action"] in ("built", "skipped")]
        if done:
            print(f"[t-build] 每日自动建仓: {len(done)}/{len(results)} 处理完成 - "
                  + "; ".join(f"{r['symbol']}:{r['action']}" for r in done[:5]))
        return results
    except Exception as e:
        print(f"[t-build] 每日自动建仓失败: {e}")
        return []


# ────────────────────────────────────────────────────────────────
# 底仓再平衡
# ────────────────────────────────────────────────────────────────

def rebalance_floors() -> List[Dict[str, Any]]:
    """底仓再平衡：跌破保留下限（市值<成本×50%）→ 只监控禁高抛；质量退化降级；达标可补建。

    返回再平衡动作列表（评估为主；自动补建受限，首开/超阈值仍人工）。
    """
    actions: List[Dict[str, Any]] = []
    try:
        from app.services.t_eod import check_floor_lower
        ledger = get_sellable_ledger()
        for sym, item in ledger.items():
            symbol = sym
            volume = int(item.get("volume") or 0)
            avg = float(item.get("avg_price") or 0)
            if volume <= 0 or avg <= 0:
                continue
            mv = volume * avg
            below_floor = check_floor_lower(symbol, mv)  # 市值<成本×0.5 → True
            if below_floor:
                actions.append({
                    "symbol": symbol, "action": "monitor_only",
                    "reason": f"底仓市值 {mv:.0f} < 成本×50%（{avg * volume * 0.5:.0f}），禁高抛转只监控",
                })
                continue
            # 质量退化 → 降级（由三层池计算自然反映，此处标记）
            from app.services.t_pool import calc_t_quality
            q = calc_t_quality(symbol) or {}
            if not q.get("pass_gate"):
                actions.append({
                    "symbol": symbol, "action": "downgrade",
                    "reason": "可T质量退化，建议降级观察池",
                })
        return actions
    except Exception as e:
        print(f"[t-build] 再平衡评估失败: {e}")
        return actions


# ────────────────────────────────────────────────────────────────
# 建仓守护线程（盘后 15:05 次日条件生成 + 日频再平衡评估）
# ────────────────────────────────────────────────────────────────

class TBuildService:
    """建仓服务 daemon 线程：盘后低频任务（对齐 TMonitor 注册模式）。"""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._status = {"running": False, "last_round": None, "last_result": ""}

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="t-build-service")
        self._thread.start()
        self._status["running"] = True
        print("[TBuildService] ✅ 做T建仓服务已启动（盘后次日条件生成 + 日频再平衡）")
        return True

    def stop(self) -> None:
        self._stop.set()
        self._status["running"] = False
        print("[TBuildService] 做T建仓服务已停止")

    def status(self) -> Dict[str, Any]:
        return dict(self._status)

    def _run(self):
        while not self._stop.is_set():
            try:
                now = datetime.now()
                p = _params()
                eod = str(p.get("eod_scan_time", "15:05"))
                eod_hm = int(eod.replace(":", ""))
                hm = now.hour * 100 + now.minute
                # 盘后 15:05-15:10 生成次日条件（含当日建仓标的）+ 每日自动选股（写次日候选）
                if hm == eod_hm and now.weekday() < 5:
                    conds = auto_gen_conditions_for_live_pool()
                    # 当日建仓标的的 D+1 条件已在 build_gateway_execute 内生成
                    auto_n = 0
                    if DEFAULT_AUTO_BUILD_ENABLED:
                        auto_n = len(daily_auto_select(DEFAULT_AUTO_SELECT_LIMIT))
                    self._status["last_result"] = (
                        f"盘后条件补生成 {conds} 条；自动选股候选 {auto_n} 条（次日执行）")
                    self._status["last_round"] = now.strftime("%Y-%m-%d %H:%M:%S")
                # 盘中：每日自动建仓（处理今日候选）+ 日频再平衡评估（每 30min 一次）
                if 930 <= hm <= 1445 and hm % 30 < 5:
                    if DEFAULT_AUTO_BUILD_ENABLED:
                        daily_auto_build()
                    acts = rebalance_floors()
                    if acts:
                        self._status["last_result"] = f"再平衡评估 {len(acts)} 项: " + "; ".join(
                            f"{a['symbol']}:{a['action']}" for a in acts[:5])
                        self._status["last_round"] = now.strftime("%Y-%m-%d %H:%M:%S")
            except Exception as e:
                print(f"[TBuildService] 轮询异常: {e}")
            self._stop.wait(60)


_build_service_instance: Optional[TBuildService] = None
_build_service_lock = threading.Lock()


def get_t_build_service() -> TBuildService:
    global _build_service_instance
    with _build_service_lock:
        if _build_service_instance is None:
            _build_service_instance = TBuildService()
        return _build_service_instance


def start_t_build_service() -> bool:
    return get_t_build_service().start()


def stop_t_build_service() -> None:
    get_t_build_service().stop()


def get_t_build_service_status() -> Dict[str, Any]:
    return get_t_build_service().status()
