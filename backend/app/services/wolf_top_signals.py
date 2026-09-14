# -*- coding: utf-8 -*-
"""wolf_top_signals.py — G1「顶部判据做宽」：用**他自己的原话判据**判定"顶部阶段"。

原机制（wolf_boll_levels.market_top()）用"上证近 120 日区间分位 ≥ 0.8"代表顶部阶段
—— 那是**我们的代理**；本模块把**他的原话判据**接进来（做宽 = 并集，不是替代）。

他的顶部/见顶判据（docs/wolf-exit-evidence.md，1,105 条 dsh 精读、逐字可核）:
  · **S1** 2026-01-12「接盘的量不够了就会调整 那就是**放量转缩量，收黑K跌破5日线**那就是**短期见顶**，
    而且只是短期，到时候**减仓避一下**就行」（xls2025.md:1657 / xls2026.md:67）
  · **S2** 2025-05-06「**放量上影线，2倍10日均量以上** 不出这个就很难见顶」（xls2025.md:1079）
  · **S3** 2025-05-15「**银保证券出长上影，放量，然后大盘缩量**，这就是**顶部结构形成的重要条件**
    现在唯一不充分的是总体量能缺一个恐慌盘而已了」（xls2025.md:1098-1099）
  · 不可算（不用）：2025-02-21「顶部现象=金融类猛拉/小票连板/亲戚朋友鼓吹」；
    2025-07-29 + 2026-06-16「加速段/小作文频出」（情绪面，暂不纳入）。

口径（逐条对应原话）:
  · **并列条件组**：他 2025-05-15 自己就是多条件并列（"唯一不充分的是…缺一个恐慌盘"）
    → 默认 **WOLF_TOP_MIN_SIGNALS=2** 条成立即算顶部阶段；
  · **做宽 = 并集**：他的判据 ∨ 我们原有代理（WOLF_TOP_INCLUDE_LEGACY=1 默认开）；
  · 动作强度分层（见 2026-09-14 落差审计与经验条）：**他的原话是"减仓避一下"** → 仅由信号触发的顶部
    只做**减半**（不动底仓）；只有"顶部阶段 + 中轨"那条原话（2025-05-13）才走**完全止盈**。

数据：上证指数日线（tushare index_daily，与 market_top 同源）；
      银行/券商用 **ETF 代理**（512800.SH 银行ETF / 512000.SH 券商ETF，relay fund_daily），
      取不到则该条信号记 unknown（不参与计数，不猜）。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

DATA = os.environ.get("DATA_DIR", "/app/data")
CACHE_FILE = os.path.join(DATA, "wolf_top_signals_cache.json")
IDX = "000001.SH"
BANK_ETF = "512800.SH"
BROKER_ETF = "512000.SH"


def _env_f(name: str, dflt: float) -> float:
    try:
        return float(os.getenv(name, str(dflt)))
    except (TypeError, ValueError):
        return dflt


def _env_i(name: str, dflt: int) -> int:
    try:
        return int(os.getenv(name, str(dflt)))
    except (TypeError, ValueError):
        return dflt


def enabled() -> bool:
    """WOLF_TOP_SIGNALS=0 可整体关掉（退回只认原有 120 日分位代理）。"""
    return os.getenv("WOLF_TOP_SIGNALS", "1").strip() not in ("0", "false", "no")


# ── 数据层 ─────────────────────────────────────────────────────────────
def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_cache(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception as e:
        print(f"[top] 缓存写盘失败: {e}")


def _fetch_index(code: str, start: str, end: str) -> List[dict]:
    from app.services.t_backtest_data import _fetch_tushare_index_daily
    return _fetch_tushare_index_daily(code, start, end) or []


_RELAY = None


def _relay():
    """取中继模块。**必须按绝对文件路径加载**：

    进程 sys.path 里 /app/app 一旦排在 /app 前面，`import core.tushare_relay` 会命中
    **/app/app/core（backend/app/core，命名空间包，没有 tushare_relay）** →
    ModuleNotFoundError（2026-09-14 在 marcus-worker 实测踩到）。按文件路径加载与 sys.path 无关。
    """
    global _RELAY
    if _RELAY is not None:
        return _RELAY
    import importlib.util as _ilu
    here = os.path.dirname(os.path.abspath(__file__))
    # (sys 在模块顶部已导入)
    cands = ["/app/core/tushare_relay.py",
             os.path.normpath(os.path.join(here, "..", "..", "..", "core", "tushare_relay.py")),
             os.path.normpath(os.path.join(here, "..", "..", "core", "tushare_relay.py"))]
    for p in cands:
        if not os.path.exists(p):
            continue
        try:
            spec = _ilu.spec_from_file_location("_wolf_relay", p)
            if spec is None or spec.loader is None:
                continue
            mod = _ilu.module_from_spec(spec)
            # ⚠️ 必须先注册进 sys.modules 再 exec：tushare_relay 里有 @dataclass，
            # dataclasses 会去 sys.modules[cls.__module__] 取模块，未注册 → None → AttributeError
            # （'NoneType' object has no attribute '__dict__'，2026-09-14 实测踩到）。
            sys.modules.setdefault("_wolf_relay", mod)
            spec.loader.exec_module(mod)
            _RELAY = mod
            return _RELAY
        except Exception as e:
            print(f"[top] relay 加载失败 {p}: {type(e).__name__}: {str(e)[:60]}")
    return None


def _fetch_etf(code: str, start: str, end: str) -> List[dict]:
    """ETF 日线（银行/券商代理）：relay fund_daily → 统一成与 index 同构的 bar 字典。"""
    try:
        _m = _relay()
        if _m is None:
            print("[top] relay 不可用 → ETF 取数跳过（该信号记 unknown，不猜）")
            return []
        df = _m.relay_query("fund_daily", ts_code=code, start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return []
        out = [{"trade_date": str(r["trade_date"]), "open": float(r["open"]), "close": float(r["close"]),
                "high": float(r["high"]), "low": float(r["low"]), "vol": float(r.get("vol") or 0)}
               for _, r in df.iterrows()]
        out.sort(key=lambda b: b["trade_date"])
        return out
    except Exception as e:
        print(f"[top] {code} 取数失败: {type(e).__name__}: {str(e)[:60]}")
        return []


def bars_for(code: str, lookback_days: int = 400) -> List[dict]:
    """带磁盘缓存的日线（当日只取一次；取数失败时保留旧缓存，避免把抖动当"没有信号"）。"""
    import datetime as _dt
    today = _dt.date.today().strftime("%Y%m%d")
    cache = _load_cache()
    ent = cache.get(code) or {}
    if ent.get("as_of") == today and ent.get("bars"):
        return ent["bars"]
    start = (_dt.date.today() - _dt.timedelta(days=int(lookback_days * 1.6) + 30)).strftime("%Y%m%d")
    is_index = code.startswith("0000") or code.startswith("3990") or code.endswith(".SI")
    bars = _fetch_index(code, start, today) if is_index else _fetch_etf(code, start, today)
    if not bars:
        return ent.get("bars") or []
    cache[code] = {"as_of": today, "bars": bars}
    _save_cache(cache)
    return bars


def index_bars(lookback_days: int = 400) -> List[dict]:
    return bars_for(IDX, lookback_days)


# ── 判据（纯函数） ──────────────────────────────────────────────────────
def _ma(bars: List[dict], i: int, n: int, field: str = "close") -> Optional[float]:
    """截至 i（含）的 n 日均值；样本不足返回 None。"""
    if i + 1 < n:
        return None
    win = [float(b.get(field) or 0) for b in bars[i - n + 1:i + 1]]
    return sum(win) / n if win else None


def sig_s1(bars: List[dict], i: int) -> Optional[bool]:
    """S1（2026-01-12）：放量转缩量 ∧ 收黑K ∧ 收盘跌破 5 日线 → 短期见顶（减仓避一下）。

    放量：前一日量 ≥ 1.2×其前 5 日均量；转缩量：当日量 < 前一日量；
    收黑K：收盘 < 开盘；跌破 5 日线：收盘 < 前 5 日（不含当日）均线。
    """
    if i < 7:
        return None
    v0, v1 = float(bars[i].get("vol") or 0), float(bars[i - 1].get("vol") or 0)
    prev5 = [float(b.get("vol") or 0) for b in bars[i - 6:i - 1]]
    if v0 <= 0 or v1 <= 0 or not prev5:
        return None
    vol_expand = v1 >= 1.2 * (sum(prev5) / len(prev5))
    shrink = v0 < v1
    black = float(bars[i]["close"]) < float(bars[i]["open"])
    ma5_prev = _ma(bars, i - 1, 5)
    below = (ma5_prev is not None) and float(bars[i]["close"]) < ma5_prev
    return bool(vol_expand and shrink and black and below)


def sig_s2(bars: List[dict], i: int) -> Optional[bool]:
    """S2（2025-05-06）：**放量上影线，2 倍 10 日均量以上**。

    量能是他给的硬数字（≥2×10 日均量）；"上影线"是形态词 → 用可算代理：
    上影 ≥ 全幅的 30%（代理阈值，可用 WOLF_TOP_SHADOW_RATIO 调）。
    """
    if i < 11:
        return None
    b = bars[i]
    try:
        o, h, l, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
        v = float(b.get("vol") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    vma10_prev = _ma(bars, i - 1, 10, "vol")
    if not vma10_prev or v <= 0 or h <= l:
        return None
    vol_ok = v >= 2.0 * vma10_prev
    upper = h - max(o, c)
    shadow_ok = upper >= _env_f("WOLF_TOP_SHADOW_RATIO", 0.3) * (h - l)
    return bool(vol_ok and shadow_ok)


def _long_upper(bars: List[dict], j: int) -> Optional[bool]:
    if j < 1:
        return None
    b = bars[j]
    try:
        o, h, l, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if h <= l:
        return None
    return (h - max(o, c)) >= _env_f("WOLF_TOP_SHADOW_RATIO", 0.3) * (h - l)


def _vol_up(bars: List[dict], j: int) -> Optional[bool]:
    vma = _ma(bars, j - 1, 5, "vol")
    if not vma:
        return None
    return float(bars[j].get("vol") or 0) >= 1.2 * vma


def sig_s3(idx: List[dict], bank: List[dict], broker: List[dict], i: Optional[int] = None) -> Optional[bool]:
    """S3（2025-05-15）：**银保证券出长上影，放量，然后大盘缩量** → 顶部结构重要条件。

    取不到银行/券商日线 → 返回 None（unknown，不参与计数，不猜）。
    """
    if not bank or not broker or not idx:
        return None
    k = len(idx) - 1 if i is None else i
    if k < 6:
        return None
    d = str(idx[k].get("trade_date") or "")
    try:
        bi = next(j for j in range(len(bank) - 1, -1, -1) if str(bank[j].get("trade_date")) == d)
        ri = next(j for j in range(len(broker) - 1, -1, -1) if str(broker[j].get("trade_date")) == d)
    except StopIteration:
        return None
    ups = [_long_upper(bank, bi), _long_upper(broker, ri)]
    volups = [_vol_up(bank, bi), _vol_up(broker, ri)]
    if all(x is None for x in ups) or all(x is None for x in volups):
        return None
    v_idx, v_idx_prev = float(idx[k].get("vol") or 0), float(idx[k - 1].get("vol") or 0)
    idx_shrink = (v_idx > 0 and v_idx_prev > 0 and v_idx < v_idx_prev)
    return bool(any(x is True for x in ups) and any(x is True for x in volups) and idx_shrink)


def top_signals(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """→ {S1,S2,S3, n, n_known, detail, as_of}；None 表示 unknown（不计入 n）。"""
    out = {"S1": None, "S2": None, "S3": None, "n": 0, "n_known": 0, "detail": "", "as_of": None}
    if not enabled():
        out["detail"] = "WOLF_TOP_SIGNALS=0"
        return out
    try:
        idx = index_bars()
        if not idx:
            out["detail"] = "no_index_bars"
            return out
        i = len(idx) - 1
        if trade_date:
            d8 = str(trade_date).replace("-", "")[:8]
            pos = [j for j, b in enumerate(idx) if str(b.get("trade_date")) <= d8]
            if not pos:
                out["detail"] = "date_not_covered"
                return out
            i = pos[-1]
        out["as_of"] = str(idx[i].get("trade_date"))
        bank = bars_for(BANK_ETF)
        broker = bars_for(BROKER_ETF)
        s1, s2 = sig_s1(idx, i), sig_s2(idx, i)
        s3 = sig_s3(idx, bank, broker, i)
        out.update({"S1": s1, "S2": s2, "S3": s3})
        out["n_known"] = sum(1 for x in (s1, s2, s3) if x is not None)
        out["n"] = sum(1 for x in (s1, s2, s3) if x is True)
        out["detail"] = "S1=%s S2=%s S3=%s" % (s1, s2, s3)
    except Exception as e:
        out["detail"] = "err:%s" % str(e)[:60]
    return out


def top_stage(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """信号侧结论：n ≥ WOLF_TOP_MIN_SIGNALS → top=True。

    ⚠️ 阈值为什么默认 **1**（2026-09-14 实测）：把 S1/S2/S3 当"并列条件组"（≥2 条）在
    2025-04→2026-09 的 355 个交易日里**一次都没触发**（S1 0.8%、S2 0%、S3 7.9%），
    等于又是一条休眠规则；他的三句本来就是**不同语境下的独立观察**，"任一条成立"更贴原话。
    """
    sig = top_signals(trade_date)
    need = _env_i("WOLF_TOP_MIN_SIGNALS", 1)
    return {"top": bool((sig.get("n") or 0) >= need), "n": sig.get("n"), "need": need,
            "signals": sig, "enabled": enabled()}
