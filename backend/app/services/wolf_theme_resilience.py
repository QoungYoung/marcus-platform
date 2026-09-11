# -*- coding: utf-8 -*-
"""wolf_theme_resilience.py — C2 方向层「跌得少、弹得早」（2026-09-11）。

═══════════════════════════════════════════════════════════════════════════
狼大原文（XLS **2026-01-27**，讲调整期怎么度过，逐字）:
  「再减少到合理的仓位后，第二个重点就是，如何能找到 **大家跌我跌少一点，大家反弹我抢先反弹**
    这种方向。这个也不用细说了，都已经分享过太多了。」
  同楼上下文（他给的量级参照）:
  「时间截取 1.14-1.27 共10个交易日…其中上证最高4190，最低4080，振幅2.67%，区间非常小
    然后按市场二级概念板块 涨幅第一是贵金属 49%…商业航天 -0.14% 科创半导体 11.65%
    证券 -3.07% 平均股价 2.94% 哪怕是九阴真经的A50也只有 -2.05%
    …简单的说，**调整阶段，除了在几百个板块里面找到唯一对的那个，其他都差不多**」
  → 关键含义：这条判据**只在调整期有意义**（非调整期"其他都差不多"）。所以本模块会先判断
    「当前是否处于调整期」，不在调整期就**不出方向结论**。

旁证（同一判据的日常用法）
  · 2025-09-04「**科创100 今天比科创50 跌得少 所以调仓成功**」→ 跌得少直接用于调仓决策
  · 2026-06-04「大家一起跌 **科技先止跌** 他还在跌 他比科技反弹得少 **科技比他跌得少**」→ 先止跌 + 跌得少
  · 2025-04-09 有人问「做超跌反弹…是选板块里最近跌的多的，还是要看**跌的少的**呢」→ 印证这是他会回答的判据族
═══════════════════════════════════════════════════════════════════════════

**口径**
  · 「大家」= **全市场个股当日涨跌幅的中位数**（比用上证更能代表"大家"；他也单列了"平均股价"作参照）
  · 主题日收益 = 主题成分股（`wolf_confirm_pick.confirm_universe`，**与布腿链同一套成分**）等权平均涨跌幅
  · **调整期** = 基准（全市场中位累计曲线）从近期最高点回落的窗口；区间振幅 < 阈值则视为"区间非常小"（他口径）
  · **跌得少** = 窗口内 主题累计涨幅 − 基准累计涨幅 > 0
  · **弹得早** = ①主题"止跌日"（窗口内主题的最低日）**早于**基准的最低日，且 ②基准最低日之后主题涨幅 > 基准
  · 输出标签: `leader`(跌得少∧弹得早) / `defensive`(只跌得少) / `laggard`(两项皆差)

**落点**：产出**方向优先级**（提示层，进纪律上下文），**不改选股**（选股仍是 pick_v2 的职责，
他这里说的是"找方向"这个定性动作）。开关 `WOLF_THEME_RESILIENCE=0`。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

STATE_FILE = "wolf_theme_resilience.json"


def enabled() -> bool:
    return os.getenv("WOLF_THEME_RESILIENCE", "1").strip() not in ("0", "false", "no")


def _path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/app/data"),
                        os.getenv("WOLF_THEME_RESILIENCE_FILE", STATE_FILE))


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


# ───────────────────────── 数据层 ─────────────────────────

def market_daily(days: Sequence[str]) -> Dict[str, Dict[str, float]]:
    """{date: {ts_code: pct_chg}}（`pro.daily(trade_date=)` 单日返回全市场，1 请求/日）。"""
    out: Dict[str, Dict[str, float]] = {}
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        for d in days:
            try:
                df = pro.daily(trade_date=d)
            except Exception as e:
                print(f"[resilience] daily({d}) 失败: {type(e).__name__}: {str(e)[:60]}")
                continue
            if df is None or len(df) == 0:
                continue
            m: Dict[str, float] = {}
            for ts, pc in zip(df["ts_code"].astype(str), df["pct_chg"]):
                try:
                    v = float(pc)
                except (TypeError, ValueError):
                    continue
                if v == v:
                    m[ts] = v
            out[d] = m
    except Exception as e:
        print(f"[resilience] market_daily 失败: {type(e).__name__}: {str(e)[:70]}")
    return out


def recent_trade_days(n: int = 20) -> List[str]:
    """最近 n 个交易日（升序）；优先用本仓既有交易日历，失败则按自然日倒推过滤。"""
    try:
        import datetime as _dt
        from app.services.t_backtest_data import resolve_trade_days
        end = _dt.date.today()
        start = end - _dt.timedelta(days=int(n * 2.2) + 10)
        ds = resolve_trade_days(start.strftime("%Y%m%d"), end.strftime("%Y%m%d")) or []
        ds = [d for d in ds if str(d)[:8].isdigit()]
        return sorted(ds)[-int(n):]
    except Exception as e:
        print(f"[resilience] 交易日历失败: {type(e).__name__}: {str(e)[:70]}")
        return []


def theme_universe() -> Dict[str, List[str]]:
    """{theme: [ts_code...]} —— 复用布腿链同一套成分（confirm_universe），保证两条路一个口径。"""
    try:
        import sys
        for p in ("/app/apps/main_line", "/home/fengx/marcus-platform/apps/main_line"):
            if os.path.isdir(p) and p not in sys.path:
                sys.path.insert(0, p)
        from wolf_confirm_pick import confirm_universe
        import fusion_mainline as fm
        themes = list(getattr(fm, "THEME_CONCEPTS", {}).keys())
        out: Dict[str, List[str]] = {}
        for th in themes:
            uni = confirm_universe(th) or {}
            codes: List[str] = []
            for _c, syms in uni.items():
                for s in syms:
                    s = str(s).upper()
                    ts = (s[2:] + "." + s[:2]) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s
                    codes.append(ts)
            if codes:
                out[th] = sorted(set(codes))
        return out
    except Exception as e:
        print(f"[resilience] 主题成分失败: {type(e).__name__}: {str(e)[:70]}")
        return {}


# ───────────────────────── 判据层（纯函数，可测） ─────────────────────────

def bench_series(market: Dict[str, Dict[str, float]], days: Sequence[str]) -> List[Tuple[str, float]]:
    """基准 = 每日全市场个股涨跌幅的**中位数** → 累计净值序列 [(date, cum_pct)]。"""
    out, cum = [], 0.0
    for d in days:
        m = market.get(d) or {}
        if not m:
            continue
        vals = sorted(m.values())
        n = len(vals)
        med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
        cum = (1 + cum / 100.0) * (1 + med / 100.0) * 100.0 - 100.0
        out.append((d, round(cum, 3)))
    return out


def theme_series(market: Dict[str, Dict[str, float]], codes: Sequence[str],
                 days: Sequence[str]) -> List[Tuple[str, float]]:
    """主题等权累计涨跌幅序列。"""
    cs = set(codes)
    out, cum = [], 0.0
    for d in days:
        m = market.get(d) or {}
        vals = [m[c] for c in cs if c in m]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        cum = (1 + cum / 100.0) * (1 + avg / 100.0) * 100.0 - 100.0
        out.append((d, round(cum, 3)))
    return out


def find_window(bench: List[Tuple[str, float]], lookback: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """定位**调整期窗口** = 基准累计曲线最高点 → 最后一日。

    返回 {start, end, start_i, peak, low_date, low_i, drawdown, amplitude, is_correction}
      · is_correction: 峰值日之后确实回落（否则"当前不在调整期"→ 他明说非调整期"其他都差不多"）
      · amplitude: 窗口内基准的最高−最低（相对），对应他说的"振幅"口径
    """
    if not bench or len(bench) < 4:
        return None
    lb = int(lookback) if lookback else len(bench)
    seg = bench[-lb:] if lb < len(bench) else bench
    off = len(bench) - len(seg)
    peak_i = max(range(len(seg)), key=lambda i: seg[i][1])
    after = seg[peak_i:]
    low_i = min(range(len(after)), key=lambda i: after[i][1]) + peak_i
    peak = seg[peak_i][1]
    trough = seg[low_i][1]
    hi = max(x[1] for x in seg)
    lo = min(x[1] for x in seg)
    amp = hi - lo
    return {"start": seg[peak_i][0], "end": seg[-1][0],
            "start_i": peak_i + off, "peak": peak,
            "low_date": seg[low_i][0], "low_i": low_i + off,
            "peak_i_rel": peak_i, "low_i_rel": low_i,
            "drawdown": round(trough - peak, 3), "amplitude": round(amp, 3),
            "is_correction": bool(trough < peak and (peak_i < len(seg) - 1)),
            "range_small": amp <= _env_f("WOLF_TR_RANGE_SMALL", 6.0)}


def evaluate_theme(ts: List[Tuple[str, float]], bench: List[Tuple[str, float]],
                   win: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """单个主题在调整窗口内的「跌得少 / 弹得早」。"""
    if not ts or not bench or win is None:
        return None
    bs = {d: v for d, v in bench}
    tv = {d: v for d, v in ts}
    s, e = win["start"], win["end"]
    base_b = bs.get(s)
    base_t = tv.get(s)
    if base_b is None or base_t is None:
        return None
    # 窗口内累计（基准/主题各自相对窗口起点）
    b_win = [bs[d] for d in sorted(bs) if s <= d <= e and d in bs]
    t_win = [(d, tv[d]) for d in sorted(tv) if s <= d <= e]
    if not b_win or not t_win:
        return None
    b_ret = b_win[-1] - base_b
    t_ret = t_win[-1][1] - base_t
    excess = round(t_ret - b_ret, 3)
    # 反弹段：基准最低日 → 窗口末
    low_d = win["low_date"]
    b_low = bs.get(low_d)
    t_low = tv.get(low_d)
    reb_excess = None
    if b_low is not None and t_low is not None:
        reb_excess = round((t_win[-1][1] - t_low) - (b_win[-1] - b_low), 3)
    # 止跌更早：主题自身最低日 vs 基准最低日
    t_low_d = min(t_win, key=lambda x: x[1])[0] if t_win else None
    stopped_early = bool(t_low_d and low_d and t_low_d <= low_d)
    less_down = excess > 0
    early_up = bool(stopped_early and (reb_excess is not None and reb_excess > 0))
    tag = "leader" if (less_down and early_up) else ("defensive" if less_down else "laggard")
    return {"theme": None, "down_ret": round(t_ret, 3), "bench_ret": round(b_ret, 3),
            "excess": excess, "rebound_excess": reb_excess,
            "theme_low_date": t_low_d, "bench_low_date": low_d,
            "stopped_early": stopped_early, "less_down": less_down, "early_up": early_up,
            "tag": tag}


def run(days: Optional[int] = None, save: bool = True,
        market: Optional[Dict[str, Dict[str, float]]] = None,
        uni: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """采集 → 计算 → 排序 → 落状态文件。"""
    if not enabled():
        return {"ok": False, "reason": "disabled"}
    # 回看长度默认 40 个交易日：窗口起点 = **窗口内基准最高点**，太短会让峰值落在首日（无意义）。
    # 他本人举例用的是"1.14-1.27 共10个交易日"，那是**他手选的调整段长度**，不是回看长度。
    n = int(days or _env_f("WOLF_TR_DAYS", 40))
    ds = recent_trade_days(n)
    if not ds:
        return {"ok": False, "reason": "no_trade_days"}
    market = market if market is not None else market_daily(ds)
    uni = uni if uni is not None else theme_universe()
    bench = bench_series(market, ds)
    win = find_window(bench)
    if not bench or win is None:
        return {"ok": False, "reason": "no_data", "days": ds}
    recs: List[Dict[str, Any]] = []
    for th, codes in (uni or {}).items():
        r = evaluate_theme(theme_series(market, codes, ds), bench, win)
        if r:
            r["theme"] = th
            r["n_codes"] = len(codes)
            recs.append(r)
    recs.sort(key=lambda x: (-x["excess"], -(x["rebound_excess"] or -99)))
    res = {"ok": True, "days": ds, "window": win, "bench": bench,
           "themes": recs, "n_themes": len(recs),
           "as_of": ds[-1] if ds else None}
    if not win["is_correction"]:
        res["note"] = ("当前**不是调整期**（基准从近期高点回落不明显）→ 按他的原话"
                       "「调整阶段…其他都差不多」，不出方向结论")
    if save:
        try:
            p = _path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"[resilience] 落盘失败: {str(e)[:80]}")
            res["ok"] = False
    print(f"[resilience] {win['start']}~{win['end']} 基准 {win['drawdown']:+.2f}% "
          f"（峰 {win['peak']:.2f} 谷 {win['peak'] + win['drawdown']:.2f}, 振幅 {win['amplitude']:.2f}%）"
          f" 调整期={win['is_correction']} | 主题 {len(recs)} 个")
    for r in recs[:8]:
        print(f"    {r['theme']}: 窗口 {r['down_ret']:+.2f}% vs 基准 {r['bench_ret']:+.2f}% "
              f"(超额 {r['excess']:+.2f}) 反弹超额 {r['rebound_excess']} 先止跌={r['stopped_early']} → {r['tag']}")
    return res


def load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def directive() -> str:
    """调整期的**方向优先级**提示（他 2026-01-27 的第二步）。

    ⚠️ 2026-09-11 修：原来**不检查 enabled()** → 把 `WOLF_THEME_RESILIENCE=0` 只能让盘后 job 停跑，
    **状态文件里的旧内容仍会照常注入上下文**（假开关）。现关闭时直接返回空。
    """
    if not enabled():
        return ""
    st = load()
    if not st or not st.get("themes"):
        return ""
    win = st.get("window") or {}
    tag_cn = {"leader": "跌得少∧弹得早", "defensive": "只跌得少", "laggard": "跟跌不跟涨"}
    head = ("🧭 方向层「跌得少弹得早」（狼大 2026-01-27 调整期第二步："
            "「大家跌我跌少一点，大家反弹我抢先反弹」）｜%s~%s 基准 %+.2f%%"
            % (win.get("start"), win.get("end"), float(win.get("drawdown") or 0)))
    if not win.get("is_correction"):
        return head + "｜**当前不是调整期** → 按他原话「其他都差不多」，不出方向结论"
    leaders = [t for t in st["themes"] if t.get("tag") == "leader"][:3]
    defs = [t for t in st["themes"] if t.get("tag") == "defensive"][:2]
    lines = [head]
    if leaders:
        lines.append("  ✅ 优先方向（跌得少∧弹得早）: " + " ｜ ".join(
            "%s(跌%s%% 超额%+.2f 反弹超额%+.2f)" % (t["theme"], t["down_ret"], t["excess"],
                                                t.get("rebound_excess") or 0) for t in leaders))
    if defs:
        lines.append("  ○ 只跌得少（还没抢反弹）: " + " ｜ ".join(
            "%s(超额%+.2f)" % (t["theme"], t["excess"]) for t in defs))
    worst = [t for t in st["themes"] if t.get("tag") == "laggard"][-2:]
    if worst:
        lines.append("  ✗ 跟跌不跟涨（调整期避开）: " + " ｜ ".join(
            "%s(超额%+.2f)" % (t["theme"], t["excess"]) for t in worst))
    return "\n".join(lines)
