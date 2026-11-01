# -*- coding: utf-8 -*-
"""breakout_context.py — 突破确认的 B4/B5 数据层 + 收盘强弱事实底稿（2026-10-30）

只做两件事：
  1) B4 主线响应 / B5 避险抢戏：用主题代理 ETF + 银行/黄金 ETF 的**当日收益**计算，
     binary 输出，不设阈值。
  2) 收盘强弱：只产出**原始事实**（收盘位置、上影/下影、最后30/60分钟、量能分布），
     不产出 strong/weak 标签；标签交给 agent。

数据源：腾讯日线（fqkline）；本地缓存 data/breakout_etf_cache.json。
缺失即 None（fail-closed），不拿昨日数据冒充今日。

用法：
  python apps/main_line/breakout_context.py --day 2026-04-30 --theme 半导体/芯片
  python apps/main_line/breakout_context.py --facts sh512480 2026-04-30
"""
from __future__ import annotations
import argparse, json, os, urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

DATA = os.environ.get("DATA_DIR", "data")
CACHE = os.path.join(DATA, "breakout_etf_cache.json")

# 主题 → 代理 ETF（数据适配，不是策略阈值；取不到就 None）
THEME_ETF_PROXY: Dict[str, List[str]] = {
    "AI/算力/科技": ["sh512480", "sh588170"],
    "半导体/芯片": ["sh512480", "sh588170"],
    "新能源/电池": ["sh515030", "sz159755"],
    "军工/航天": ["sh512660"],
    "资源/周期": ["sh510410", "sh518880"],
    "金融": ["sh512800", "sh512000"],
    "医药": ["sh512010"],
    "消费/内需": ["sz159928"],
    "机器人/智能制造": ["sz562500"],
    "电力/公用": ["sh561560"],
    "汽车/智驾": ["sh516110"],
    "传媒/游戏": ["sh512980"],
    "农业": ["sz159825"],
    "稳增长/基建": ["sh516950"],
}
BANK_ETF = "sh512800"
GOLD_ETF = "sh518880"


def _load_cache() -> Dict[str, Dict[str, list]]:
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c: Dict[str, Dict[str, list]]) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass


_INDEX_MAP = {"000001": "sh000001", "000300": "sh000300", "000688": "sh000688",
              "399001": "sz399001", "399006": "sz399006"}


def normalize_code(code: str) -> str:
    code = (code or "").strip().lower()
    if code in _INDEX_MAP:
        return _INDEX_MAP[code]
    if code.endswith(".sh"):
        return "sh" + code[:-3]
    if code.endswith(".sz"):
        return "sz" + code[:-3]
    if not code.startswith(("sh", "sz")):
        return ("sh" if code[:1] in "56" else "sz") + code
    return code


_DAILY_CACHE: Dict[str, Dict[str, Dict[str, float]]] = {}


def fetch_daily(code: str, n: int = 800) -> Optional[Dict[str, Dict[str, float]]]:
    """腾讯日线 → {date: {open,close,high,low,amount}}；失败读缓存，仍无则 None。"""
    code = normalize_code(code)
    if code in _DAILY_CACHE:
        return _DAILY_CACHE[code]
    if code.endswith(".sh"):
        code = "sh" + code[:-3]
    elif code.endswith(".sz"):
        code = "sz" + code[:-3]
    if not code.startswith(("sh", "sz")):
        code = ("sh" if code[:1] in "56" else "sz") + code
    cache = _load_cache()
    try:
        u = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq" % (code, n))
        raw = urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=20
        ).read().decode("utf-8")
        j = json.loads(raw)
        data = (j.get("data") or {}).get(code) or {}
        rows = data.get("qfqday") or data.get("day") or []
        if rows:
            out = {}
            for r in rows:
                try:
                    out[str(r[0]).replace("-", "")] = {"open": float(r[1]), "close": float(r[2]),
                                      "high": float(r[3]), "low": float(r[4]),
                                      "amount": float(r[5]) if len(r) > 5 else 0.0}
                except Exception:
                    continue
            if out:
                cache[code] = out
                _save_cache(cache)
                _DAILY_CACHE[code] = out
                return out
    except Exception:
        pass
    _DAILY_CACHE[code] = cache.get(code)
    return _DAILY_CACHE[code]


def day_return(code: str, day: str) -> Optional[float]:
    """当日收益（小数）。day 支持 2026-04-30 / 20260430。"""
    d = day.replace("-", "")
    bars = fetch_daily(code)
    if not bars:
        return None
    ds = sorted(bars)
    if d not in bars:
        return None
    i = ds.index(d)
    if i < 1:
        return None
    prev = float(bars[ds[i - 1]]["close"])
    cur = float(bars[d]["close"])
    return (cur / prev - 1.0) if prev > 0 else None


def _avg(vals: List[Optional[float]]) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def b4_b5(theme: str, day: str) -> Dict[str, Any]:
    """B4 主线响应 / B5 避险抢戏。binary，无阈值。

    B4: 主题代理 ETF 当日平均收益 > 0 → mainline_ok=True
    B5: max(银行, 黄金) 当日收益 > 主线收益 且 主线收益 <= 0 → hedge_flag=True
    """
    proxies = THEME_ETF_PROXY.get(theme) or []
    main_rets = {c: day_return(c, day) for c in proxies}
    main_ret = _avg(list(main_rets.values()))
    bank = day_return(BANK_ETF, day)
    gold = day_return(GOLD_ETF, day)
    hedge_best = max([x for x in (bank, gold) if x is not None], default=None)
    mainline_ok = (main_ret > 0) if main_ret is not None else None
    hedge_flag = bool(main_ret is not None and hedge_best is not None
                      and hedge_best > main_ret and main_ret <= 0)
    return {
        "as_of": day, "theme": theme,
        "mainline_ret": round(main_ret, 4) if main_ret is not None else None,
        "mainline_proxies": main_rets,
        "bank_ret": round(bank, 4) if bank is not None else None,
        "gold_ret": round(gold, 4) if gold is not None else None,
        "mainline_ok": mainline_ok,
        "hedge_flag": hedge_flag,
        "source": "tencent_fqkline",
    }


_B45_CACHE: Dict[tuple, Dict[str, Any]] = {}


def asof_day() -> str:
    return os.getenv("WOLF_ASOF_DAY") or datetime.now().strftime("%Y-%m-%d")


def prev_trade_day(day: Optional[str] = None) -> str:
    """上一交易日（PIT：盘前/盘中只用已完成收盘，不偷看当日）。"""
    raw = day or os.getenv("WOLF_ASOF_DAY") or datetime.now().strftime("%Y-%m-%d")
    d = str(raw).replace("-", "")
    bars = fetch_daily("sh512480") or fetch_daily("sh000001")
    if not bars:
        return str(raw)
    ds = sorted(bars)
    prev = [x for x in ds if x < d]
    p = prev[-1] if prev else ds[-1]
    return p[:4] + "-" + p[4:6] + "-" + p[6:]


def b4b5_enabled() -> bool:
    return str(os.getenv("WOLF_B4B5_ENABLED", "1")).strip().lower() in ("1", "true", "yes", "on")


def b4b5_gate_enabled() -> bool:
    return str(os.getenv("WOLF_B4B5_GATE", "1")).strip().lower() in ("1", "true", "yes", "on")


def b8_gate_enabled() -> bool:
    return str(os.getenv("WOLF_B8_GATE", "1")).strip().lower() in ("1", "true", "yes", "on")


def b4_b5_cached(theme: str, day: str) -> Dict[str, Any]:
    key = (str(theme or ""), str(day or "").replace("-", ""))
    if key not in _B45_CACHE:
        _B45_CACHE[key] = b4_b5(theme, day)
    return _B45_CACHE[key]


def theme_of_symbol(ts_code: str, day: Optional[str] = None) -> Optional[str]:
    """代码 → 主题：优先 stock_confirm_result.json，回退 main_line_state.main_line。"""
    code6 = "".join(ch for ch in str(ts_code or "") if ch.isdigit())[:6]
    try:
        p = os.path.join(DATA, "stock_confirm_result.json")
        if os.path.exists(p):
            sc = json.load(open(p, encoding="utf-8")) or {}
            for _cname, v in sc.items():
                if not isinstance(v, dict):
                    continue
                for st in (v.get("stocks") or []):
                    c = "".join(ch for ch in str(st.get("code") or "") if ch.isdigit())[:6]
                    if c and c == code6:
                        return v.get("theme") or None
    except Exception:
        pass
    try:
        p = os.path.join(DATA, "main_line_state.json")
        if os.path.exists(p):
            ms = json.load(open(p, encoding="utf-8")) or {}
            return ms.get("main_line") or None
    except Exception:
        pass
    return None


def gate(theme: str, day: Optional[str] = None) -> Dict[str, Any]:
    """B4/B5 生产门：B5=硬 block；B4 失败=降级；数据缺失=fail-open。

    返回 {..., action: pass|downgrade|block|unknown, allow, reasons}
    """
    d = day or prev_trade_day()
    r = b4_b5_cached(theme, d) if theme else {"mainline_ok": None, "hedge_flag": False,
                                               "mainline_ret": None, "bank_ret": None,
                                               "gold_ret": None, "theme": theme, "as_of": d}
    main = r.get("mainline_ok")
    hedge = bool(r.get("hedge_flag"))
    reasons = []
    if hedge:
        reasons.append("B5 避险抢戏：银行/黄金强于主线（诱多）")
    if main is False:
        reasons.append("B4 主线未响应：主题代理当日收益<=0")
    if main is None and r.get("mainline_ret") is None:
        action = "unknown"
        reasons.append("B4/B5 数据缺失(fail-open)")
    elif hedge:
        action = "block"
    elif main is False:
        action = "downgrade"
    else:
        action = "pass"
    return {**r, "action": action, "allow": action != "block", "reasons": reasons}


_B8_CACHE: Dict[tuple, Dict[str, Any]] = {}


def b8_state_from_bars(bars: Dict[str, Dict[str, float]], day: str,
                       N: int = 20, touch: float = 1.0, max_wait: int = 5,
                       lookback: int = 15) -> Dict[str, Any]:
    """从日线 bars 算最近一次「突破→回踩」状态（纯函数，PIT：只用 <= day）。"""
    d = str(day).replace("-", "")
    ds = sorted([x for x in bars if x <= d])
    if len(ds) < N + 3:
        return {"state": "no_setup", "reason": "bars_lt_%d" % (N + 3), "as_of": d}
    c = [float(bars[x]["close"]) for x in ds]
    lo = [float(bars[x]["low"]) for x in ds]
    end = len(ds) - 1
    bi = None
    for i in range(end, max(N, end - int(lookback)), -1):
        level = max(c[i - N:i])
        if c[i] > level and c[i - 1] <= level:
            bi = i
            break
    if bi is None:
        return {"state": "no_setup", "reason": "no_breakout", "as_of": d}
    level = max(c[bi - N:bi])
    rj = None
    for j in range(bi + 1, min(bi + int(max_wait), end) + 1):
        if lo[j] <= level * float(touch):
            rj = j
            break
    if rj is None:
        return {"state": "no_touch", "level": round(level, 3), "breakout": ds[bi],
                "as_of": d, "reason": "no_retest_within_%d" % max_wait}
    nxt = c[rj + 1] if rj + 1 <= end else None
    st = retest_state(level, lo[rj], c[rj], nxt, touch=touch)
    return {"state": st, "level": round(level, 3), "breakout": ds[bi], "retest": ds[rj],
            "retest_low": round(lo[rj], 3), "retest_close": round(c[rj], 3),
            "confirm_close": round(nxt, 3) if nxt is not None else None, "as_of": d}


def b8_state(code: str, day: Optional[str] = None) -> Dict[str, Any]:
    """代码级 B8：取日线 → 最近突破/回踩状态；失败返回 unknown（fail-open）。"""
    d = day or prev_trade_day()
    key = (str(code), str(d).replace("-", ""))
    if key in _B8_CACHE:
        return _B8_CACHE[key]
    bars = fetch_daily(code)
    if not bars:
        out = {"state": "unknown", "reason": "no_bars", "as_of": d}
    else:
        out = b8_state_from_bars(bars, d)
    _B8_CACHE[key] = out
    return out


def b8_gate(code: str, day: Optional[str] = None) -> Dict[str, Any]:
    """B8 生产门：跌破确认=block；跌破待确认=downgrade；不破/假跌破/未回踩=pass。"""
    st = b8_state(code, day)
    s = st.get("state")
    if s == "break_confirmed":
        action = "block"; allow = False; reasons = ["B8 跌破确认：次日仍破 level"]
    elif s == "break_pending":
        action = "downgrade"; allow = True; reasons = ["B8 跌破待确认：等次日"]
    elif s in ("hold", "false_break"):
        action = "pass"; allow = True; reasons = ["B8 " + ("回踩不破" if s == "hold" else "假跌破（次日收回）")]
    elif s in ("no_touch", "no_setup"):
        action = "pass"; allow = True; reasons = ["B8 不适用（" + str(s) + "）"]
    else:
        action = "unknown"; allow = True; reasons = ["B8 数据缺失(fail-open)"]
    return {**st, "action": action, "allow": allow, "reasons": reasons}


def _ma(vals: List[float], n: int) -> Optional[float]:
    return sum(vals[-n:]) / n if len(vals) >= n else None


def close_strength_facts(code: str, day: str) -> Dict[str, Any]:
    """收盘强弱的**原始事实**（不判定）。agent 依据这些数字自己判 strong/weak/neutral/no_confident。"""
    d = day.replace("-", "")
    bars = fetch_daily(code)
    if not bars or d not in bars:
        return {"ok": False, "reason": "no_daily", "code": code, "as_of": day}
    ds = sorted(bars)
    i = ds.index(d)
    b = bars[d]
    o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    rng = (h - l) if h > l else None
    closes = [bars[x]["close"] for x in ds[: i + 1]]
    facts = {
        "ok": True, "code": code, "as_of": d, "mode": "postclose",
        "open": o, "high": h, "low": l, "close": c,
        "range": rng,
        "close_pos_raw": round((c - l) / rng, 4) if rng else None,
        "upper_shadow_pct": round((h - max(o, c)) / rng * 100, 2) if rng else None,
        "lower_shadow_pct": round((min(o, c) - l) / rng * 100, 2) if rng else None,
        "body_pct": round(abs(c - o) / rng * 100, 2) if rng else None,
        "close_vs_ma5_pct": round((c / _ma(closes, 5) - 1) * 100, 2) if _ma(closes, 5) else None,
        "close_vs_ma10_pct": round((c / _ma(closes, 10) - 1) * 100, 2) if _ma(closes, 10) else None,
    }
    # 可选：若有 5m/15m 数据，补最后 30/60 分钟与量能分布（本地 index_5m 只覆盖指数）
    facts["intraday"] = None
    return facts


def index_intraday_facts(day: str) -> Optional[Dict[str, Any]]:
    """指数 5m 事实（本地 index_5m.db）；只给数字，不判定强弱。"""
    try:
        import sqlite3
        prefix = day if "-" in day else (day[:4] + "-" + day[4:6] + "-" + day[6:])
        con = sqlite3.connect(os.path.join(DATA, "index_5m.db"))
        rows = con.execute(
            "select trade_time,open,high,low,close,vol from index_bars_5m "
            "where ts_code='000001.SH' and trade_time like ? order by trade_time", (prefix + "%",)
        ).fetchall()
        con.close()
        if not rows:
            return None
        bars = [{"t": r[0], "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                 "c": float(r[4]), "v": float(r[5] or 0)} for r in rows]
        last = bars[-1]["c"]
        def ret_back(minutes: int):
            # 每根 5m；回到 N 分钟前
            k = max(0, len(bars) - 1 - minutes // 5)
            base = bars[k]["c"]
            return (last / base - 1) * 100 if base else None
        hi = max(bars, key=lambda x: x["h"])
        morning = [b for b in bars if b["t"][11:16] <= "11:30"]
        afternoon = [b for b in bars if b["t"][11:16] >= "13:00"]
        tot_v = sum(b["v"] for b in bars) or 1.0
        return {
            "last_30m_ret_pct": round(ret_back(30), 2) if ret_back(30) is not None else None,
            "last_60m_ret_pct": round(ret_back(60), 2) if ret_back(60) is not None else None,
            "day_high_time": hi["t"][11:16],
            "morning_high": round(max((b["h"] for b in morning), default=0), 2),
            "afternoon_high": round(max((b["h"] for b in afternoon), default=0), 2),
            "volume_last_30m_share": round(sum(b["v"] for b in bars[-6:]) / tot_v, 3),
            "close": round(last, 2),
        }
    except Exception:
        return None


def agent_payload(code: str, day: str, theme: str = "") -> Dict[str, Any]:
    """给 agent 的完整底稿：事实 + B4/B5；不带结论、不打分。"""
    facts = close_strength_facts(code, day)
    if code.replace(".SH", "").replace(".SZ", "").lower() in ("sh000001", "000001"):
        facts["intraday"] = index_intraday_facts(day)
    return {
        "task": "judge_close_strength",
        "symbol": code, "as_of": day, "mode": "postclose",
        "facts": facts,
        "context": b4_b5(theme, day) if theme else None,
        "allowed_labels": ["strong", "weak", "neutral", "no_confident"],
        "rules": [
            "只依据 facts 里的数字，不引入外部记忆",
            "必须给至少 2 个数字证据（收盘位置、上影、最后30/60分钟、量能分布）",
            "允许 no_confident；不确定时不要硬判",
            "不要输出概率/评分/阈值触发",
        ],
        "output_schema": {
            "close_strength": "strong|weak|neutral|no_confident",
            "evidence": ["字符串，含数字"],
            "invalidations": ["可观测的反证条件"],
        },
    }


def retest_state(level: float, low: float, close: float,
                 next_close: Optional[float] = None, touch: float = 1.0) -> str:
    """B8 回踩状态：no_touch / hold / break_pending / false_break / break_confirmed。

    - 不破：low <= level*touch 且 close >= level
    - 跌破待确认：close < level，且没有 next_close
    - 假跌破：close < level 但 next_close >= level
    - 跌破确认：close < level 且 next_close < level
    """
    try:
        lv = float(level); lo = float(low); c = float(close)
    except Exception:
        return "no_data"
    if lv <= 0 or lo > lv * float(touch):
        return "no_touch"
    if c >= lv:
        return "hold"
    if next_close is None:
        return "break_pending"
    return "false_break" if float(next_close) >= lv else "break_confirmed"


def agent_input(code: str, day: str, theme: str = "") -> Dict[str, Any]:
    """给 agent 的底稿：事实 + B4/B5 上下文；不含结论、不打分。"""
    return {
        "symbol": code, "as_of": day, "theme": theme,
        "close_strength_facts": close_strength_facts(code, day),
        "b4_b5": b4_b5(theme, day) if theme else None,
        "note": "只给事实；close_strength 由 agent 判 strong/weak/neutral/no_confident，并给数字证据。",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None)
    ap.add_argument("--theme", default="")
    ap.add_argument("--facts", nargs=2, metavar=("CODE", "DAY"))
    a = ap.parse_args()
    if a.facts:
        print(json.dumps(close_strength_facts(a.facts[0], a.facts[1]), ensure_ascii=False, indent=1))
    elif a.day:
        print(json.dumps(b4_b5(a.theme, a.day), ensure_ascii=False, indent=1))
    else:
        ap.error("--day or --facts required")
