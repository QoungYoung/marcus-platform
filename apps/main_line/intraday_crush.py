# -*- coding: utf-8 -*-
"""intraday_crush.py — 盘中「急杀分」：**买腿触发那一刻**的形态判据（2026-09-21 用户拍板 C2′+C2″）

语料（方向依据）：2025-06-05「**急杀可以买，缓跌不买**」；2026-01-17「跌破趋势…盘中跌破不算」的反面用法——
  他要的是"当下这一刻"的形态，而不是隔夜/前一日算好的 stage。

为什么必须在**触发时**算：253/254 腿由 `switch_builder` 在**08:18 开盘前**布下（那时没有盘中数据），
  真正的成交发生在盘中表达式命中时 ⇒ 只有那一刻才有分钟级信息。

实测（台账 I/J 节，547 笔有分钟档的买腿，全部 ≤T 数据）：
  · **C2″ 极端门**：`放量(vol≥1.5) ∧ 跌破前一日低点 ∧ 跌破当日 VWAP` ⇒ 拦 11/547 笔，
    拦截集净 **−1,488（拦对了）**，跨臂 4✅/1❌。
  · **C2′ 急杀分排序**：同日多笔买腿时按"急杀分"取前 2 ⇒ 保留 +69,001 / 放弃 **−25,841** ⇒ **净差 +25,841**；
    反方向（按"企稳确认度"排）是 **−3,889** ⇒ **方向必须是"急杀优先"**。
  · 分档（绝对值）**非单调**（最低 25% 反而最赚）⇒ 所以本模块**不做绝对阈值门**，只做
    ① 极端组合门（三条件同时成立，样本极少）② 同日相对准入（`WOLF_CRUSH_ADMIT_K`）。

开关（库内默认全 0 ⇒ 生产零影响）：
  WOLF_CRUSH_GATE=0            总开关
  WOLF_CRUSH_EXTREME=1         极端门：放量∧破前低∧破VWAP ⇒ 拦
  WOLF_CRUSH_VOL_MIN=1.5       极端门的量比下限（当日累计量 / 前 5 日同一时刻均量）
  WOLF_CRUSH_ADMIT=0           同日准入：当日该账户买腿触发数 ≥ K 后，只放行分数 ≥ 当日已触发中位数的
  WOLF_CRUSH_ADMIT_K=3
数据源：回测 `data/_bt_full/mins/<code>_<mkt>_5min_<day>.json`；**取不到 ⇒ 一律放行（fail-open）**。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


ENV = "WOLF_CRUSH_GATE"


def enabled() -> bool:
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def _on(name: str, dflt: str = "0") -> bool:
    return str(os.getenv(name, dflt)).strip().lower() in ("1", "true", "yes", "on")


def extreme_on() -> bool:
    return _on("WOLF_CRUSH_EXTREME", "1")


def admit_on() -> bool:
    return _on("WOLF_CRUSH_ADMIT", "0")


def vol_min() -> float:
    try:
        return float(os.getenv("WOLF_CRUSH_VOL_MIN", "1.5") or 1.5)
    except Exception:
        return 1.5


def admit_k() -> int:
    try:
        return max(int(float(os.getenv("WOLF_CRUSH_ADMIT_K", "3") or 3)), 1)
    except Exception:
        return 3


# ────────────────────────────── 数据访问 ──────────────────────────────
def _mins_dir() -> str:
    d = os.getenv("WOLF_MINS_DIR")
    if d and os.path.isdir(d):
        return d
    data = os.environ.get("DATA_DIR", "data")
    for cand in (os.path.join(data, "_bt_full", "mins"), os.path.join(os.path.dirname(data), "mins")):
        if os.path.isdir(cand):
            return cand
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cand in (os.path.join(root, "data", "_bt_full", "mins"), os.path.join(root, "data", "mins")):
        if os.path.isdir(cand):
            return cand
    return ""


_MEMO: Dict[Any, Any] = {}


def _bars(code: str, day: str) -> Optional[List[Any]]:
    """分钟档（回测格式：{bars:[[code, 'YYYY-MM-DD HH:MM:SS', o,h,l,c,vol,amt], ...]}，**倒序**）。"""
    key = (code, day)
    if key in _MEMO:
        return _MEMO[key]
    d = _mins_dir()
    got = None
    if d:
        p = os.path.join(d, "%s_5min_%s.json" % (code, day))
        if os.path.exists(p):
            try:
                got = json.load(open(p, encoding="utf-8")).get("bars") or None
            except Exception:
                got = None
    _MEMO[key] = got
    return got


def _hm(x: Any) -> str:
    """时间归一化：`"09:35"` / `"0935"` / `"9:35"` → `"0935"`。

    ⚠️ 2026-09-22 修 bug（用户点「9.35 会被放行吧」时暴露）：原实现直接
    `t = str(b[1])[11:16]`（`"09:35"`，**带冒号**）与调用方传进来的 cutoff 做字符串比较，
    而 cutoff 有两种写法：
      · `now.strftime("%H:%M")` → `"09:40"` ⇒ 比较正确；
      · `now.strftime("%H%M")`  → `"0940"` ⇒ `":" (0x3A) > any digit` ⇒
        **同一小时内的 bar 全部被排除、更晚的小时也排除** ⇒ 实际退化成「hour < cutoff_hour」：
        cutoff=`0935/0940/0945` 取到 **0 根 bar** ⇒ `feats()` 返回 None ⇒ **整条闸 fail-open**；
        cutoff=`1000` 只取到 09:55 及以前（**滞后最多 55 分钟**的旧形态）。
    现在两边都归一化后再比 ⇒ 两种写法结果一致（带冒号的老调用方行为**逐位不变**）。
    """
    d = "".join(ch for ch in str(x or "") if ch.isdigit())
    return d[:4] if len(d) >= 4 else d.zfill(4)


def _upto(bars: List[Any], hhmm: str) -> List[Tuple[str, float, float, float, float, float, float]]:
    out = []
    cut = _hm(hhmm)
    for b in bars:
        try:
            t = str(b[1])[11:16]
            if _hm(t) <= cut:
                out.append((t, float(b[2]), float(b[3]), float(b[4]), float(b[5]), float(b[6]), float(b[7])))
        except Exception as _e_sil1:
            _silent_alert("intraday_crush.py:128", _e_sil1)
            continue
    return sorted(out)


def _prev_close(code: str, day: str) -> Optional[float]:
    """前一日收盘（用分钟档推，与 `_prev_low` 同一来源 ✓）：取前 5 个交易日里最近一档的最后一根收盘 ✓。"""
    d = _mins_dir()
    if not d:
        return None
    import glob
    cands = sorted(x for x in os.listdir(d) if x.startswith(code + "_5min_") and x.endswith(".json"))
    prev = [x for x in cands if x[len(code) + 6:-5] < day]
    for fn in reversed(prev[-5:]):
        try:
            bars = json.load(open(os.path.join(d, fn), encoding="utf-8")).get("bars") or []
            if bars:
                # ⚠️ 分时文件是**倒序**（15:00 在前 ✗）⇒ 必须按时间戳取**最后一根** ✓
                _last = max(bars, key=lambda b: str(b[1]))
                return float(_last[5])
        except Exception as _e_sil2:
            _silent_alert("intraday_crush.py:148", _e_sil2)
            continue
    return None


def _prev_low(code: str, day: str) -> Optional[float]:
    """前一日最低价：用分钟档（前 5 个交易日里最近一个有档的日子）推。取不到 ⇒ None。"""
    d = _mins_dir()
    if not d:
        return None
    import glob
    cands = sorted(x for x in os.listdir(d) if x.startswith(code + "_5min_") and x.endswith(".json"))
    prev = [x for x in cands if x[len(code) + 6:-5] < day]
    for fn in reversed(prev[-5:]):
        try:
            bars = json.load(open(os.path.join(d, fn), encoding="utf-8")).get("bars") or []
            if bars:
                return min(float(b[3]) for b in bars)
        except Exception as _e_sil3:
            _silent_alert("intraday_crush.py:166", _e_sil3)
            continue
    return None


def feats(code: str, day: str, hhmm: str, min_bars: int = 3) -> Optional[Dict[str, Any]]:
    """触发那一刻的盘中特征（全部 ≤hhmm）。取不到 ⇒ None（调用方 fail-open）。

    `min_bars` 默认 3（C2′/C2″ 的既有口径，**不要动**）；B/C 用 2 —— 因为 09:35 只有 2 根 bar，
    用 3 会让开盘头 10 分钟整段 fail-open（用户 2026-09-22 点出的洞）。
    """
    bars = _bars(code, day)
    if not bars:
        return None
    u = _upto(bars, hhmm)
    if len(u) < int(min_bars or 1):
        return None
    cv = sum(x[5] for x in u) or 0.0
    ca = sum(x[6] for x in u) or 0.0
    px = u[-1][4]
    hi = max(x[2] for x in u)
    lo = min(x[3] for x in u)
    op = u[0][1]
    vwap = (ca / cv) if cv else None
    d = _mins_dir()
    refs: List[float] = []
    if d:
        import os as _os
        cands = sorted(x for x in _os.listdir(d) if x.startswith(code + "_5min_") and x.endswith(".json"))
        prevs = [x for x in cands if x[len(code) + 6:-5] < day][-5:]
        for fn in prevs:
            try:
                b0 = json.load(open(_os.path.join(d, fn), encoding="utf-8")).get("bars") or []
                u0 = _upto(b0, hhmm)
                if u0:
                    refs.append(sum(x[5] for x in u0))
            except Exception as _e_sil4:
                _silent_alert("intraday_crush.py:202", _e_sil4)
                continue
    vr = (cv / (sum(refs) / len(refs))) if refs else None
    pl = _prev_low(code, day)
    # 2026-09-22（B/C）：买入前的**趋势**——前 3 根 5min 收盘是否严格连跌/连涨
    _c3 = [x[4] for x in u[-3:]]
    last3_down = len(_c3) == 3 and _c3[0] > _c3[1] > _c3[2]
    last3_up = len(_c3) == 3 and _c3[0] < _c3[1] < _c3[2]
    # 开盘头 10 分钟只有 2 根 bar：用"**现有 bar 逐根走低**"（≥2 根）兜底，免得整段 fail-open
    _cn = [x[4] for x in u]
    nb_down = len(_cn) >= 2 and all(_cn[i] > _cn[i + 1] for i in range(len(_cn) - 1))
    _last2 = None
    try:
        if len(u) >= 2 and float(u[-2][4]) > 0:
            _last2 = (float(u[-2][4]) - float(u[-1][4])) / float(u[-2][4]) * 100.0
    except Exception:
        _last2 = None
    _pc = _prev_close(code, day)
    _chg = ((px / _pc - 1) * 100) if (_pc and px) else None
    return {"px": px, "vr": vr, "vwap": vwap, "prev_low": pl, "open": op, "last2_drop_pct": _last2,
            "pre_close": _pc, "chg_pct": _chg,
            "last3_down": last3_down, "last3_up": last3_up,
            "nbars": len(u), "nb_down": nb_down, "below_open": px < op,
            "day_hi": hi, "day_lo": lo,
            "above_prev_low": (pl is None) or px >= pl,
            "above_vwap": (vwap is not None) and px >= vwap,
            "below_vwap_pct": ((px / vwap - 1) * 100) if vwap else None,
            "day_pos": ((px - lo) / (hi - lo)) if hi > lo else None}


def crush_score(f: Dict[str, Any]) -> float:
    """急杀分（越大越"急杀"）：放量 + 跌破均价 + 靠日内低点。与语料「急杀可以买」同向。"""
    s = 0.0
    if f.get("vr") is not None:
        s += max(0.0, min(2.0, float(f["vr"]) - 0.8)) * 1.5
    if f.get("below_vwap_pct") is not None:
        s += max(0.0, min(3.0, -float(f["below_vwap_pct"]))) * 0.8
    if f.get("day_pos") is not None:
        s += (1 - float(f["day_pos"])) * 1.5
    return round(s, 3)


def extreme(f: Dict[str, Any]) -> Tuple[bool, str]:
    """C2″ 极端门：放量 ∧ 跌破前一日低点 ∧ 跌破当日 VWAP。"""
    if not extreme_on() or not f:
        return False, ""
    vr = f.get("vr")
    if vr is None or float(vr) < vol_min():
        return False, ""
    if f.get("prev_low") is None or f.get("above_prev_low"):
        return False, ""
    if f.get("vwap") is None or f.get("above_vwap"):
        return False, ""
    return True, "急杀门: 量比%.2f≥%.1f ∧ 破前低%.2f ∧ 破均价%.2f" % (
        float(vr), vol_min(), float(f["prev_low"]), float(f["vwap"]))


# ────────────────────────── 同日准入（C2′ 的流式近似）──────────────────────────
_ADMIT: Dict[str, List[float]] = {}


def reset_day():
    _ADMIT.clear()


def admit(account: str, day: str, f: Dict[str, Any]) -> Tuple[bool, float, str]:
    """同日准入：当日触发数 < K ⇒ 放行；否则只放行分数 ≥ 当日已触发分数中位数的。
    ⚠️ 这是"同日按急杀分取前 K"的**流式近似**（无法撤销已成交），实测效果见台账 I 节。"""
    sc = crush_score(f or {})
    if not admit_on():
        return True, sc, ""
    key = "%s|%s" % (account, day)
    seen = _ADMIT.setdefault(key, [])
    k = admit_k()
    if len(seen) < k:
        seen.append(sc)
        return True, sc, ""
    import statistics as _st
    med = _st.median(seen)
    if sc >= med:
        seen.append(sc)
        return True, sc, "急杀分%.2f≥当日中位%.2f" % (sc, med)
    return False, sc, "同日准入: 急杀分%.2f < 当日中位%.2f（当日已触发 %d 笔）" % (sc, med, len(seen))


def check(symbol: Any, day: str, hhmm: str, account: str = "") -> Tuple[bool, str, float]:
    """买腿触发前的总检查：返回 (是否放行, 原因, 急杀分)。取不到分钟数据 ⇒ 放行（fail-open）。"""
    if not enabled():
        return True, "", 0.0
    s = str(symbol or "").upper()
    code = (s[2:8] + "_" + s[:2]) if (s[:2] in ("SH", "SZ") and len(s) >= 8) else s.replace(".", "_")
    f = feats(code, str(day), str(hhmm))
    if not f:
        return True, "", 0.0
    bad, why = extreme(f)
    if bad:
        return False, why, crush_score(f)
    ok, sc, why2 = admit(account or "", str(day), f)
    if not ok:
        return False, why2, sc
    return True, why2, sc


# ══════════════════ B/C 买入时点闸（2026-09-22 用户拍板 "A+B+C"）══════════════════
# 起点：环旭电子 601231 2026-03-03 —— 我们的 `wolf_zheng_t_buy` 在 09:40 以「日内回撤-3.1% 量比0.12」
#   触发，买在 47.39（当日最高 48.50、昨收 48.42），股价随后一路瀑布到**跌停收 43.58**，
#   次日 09:45 由破位腿割在 42.94（−3,611）。**不是买在跌停价，是买在瀑布起点。**
#
# 量化（1,333 笔有分钟档的买成交，FIFO 已实现盈亏；脚本 `.dsh-tmp/intraday_buy_timing.py`）：
#   · 买入当日收盘涨跌幅单调：≤−9.5% 单笔 −1,482 / −9.5~−5% −498 / −5~−2% −174 / −2~0% −104 /
#     0~+3% −74 / **>+3% +239**；
#   · 现价 < 当日 VWAP：535 笔 −90/笔（VWAP 上方 798 笔 +6/笔）；
#   · 买入前 3 根 5min 连跌：282 笔 −81/笔（连涨 313 笔 +5/笔）；
#   · **破均价 ∧ 连跌**：246 笔 −108/笔（其余 1,087 笔 −15/笔），**跨臂 10 条臂里 9 条更差**。
#   · 「一律改到尾盘买」**不成立**：同一批 1,120 笔只换时点，14:45 买要多付 46,857 元
#     （因为 >+3% 那 274 笔要多付 117,447）——**但分档看弱势日等到尾盘是省钱的**
#     （≤−5% 省 17,521 / −5~−2% 省 24,741 / −2~0% 省 23,076）。
#
# ⇒ 两条闸（都只作用于**买入**，取不到分钟档一律放行）：
#   B `WOLF_FALLING_GATE`（默认 0）：**下跌中不买** —— 现价 < 当日 VWAP ∧ 前 3 根 5min 连跌 ⇒ 本次不执行。
#   C `WOLF_WEAK_DEFER`（默认 0）：**弱势日延后到尾盘** —— 当日为跌（现价 < 昨收）∧ 现价 < VWAP，
#     且当前 < `WOLF_WEAK_DEFER_HM`（默认 1445）⇒ 本次不执行，14:45 后重新评估。
#     语料：2025-05-23「尾盘能回来就尾盘买 急什么」；2026-01-12「买点只有尾盘…千万绝对不要开盘买」。
#   两条都**不杀腿**：不写触发/不执行，腿保留活性 ⇒ 后续 round（尤其 14:45 后）自然重评。
#
# ⚠️ 与 G7（`wolf_day_rules`：2025-01-23「大涨之日少买票…**大跌之日多买票** 少卖票。因为**指数方面**
#   没什么太大隐患」）的边界：G7 讲的是**指数层**的日偏向（现未在回测启用），B/C 讲的是**个股层**
#   "现价已在均价下方且走势向下"。两者作用对象不同；但若将来启用 G7，"指数大跌 + 个股走低"的日子上
#   会相抵，届时要另量"B 优先"还是"给 G7 豁免"，不要默认叠加。

_WK_ENV = "WOLF_WEAK_DEFER"
_FL_ENV = "WOLF_FALLING_GATE"


def falling_on() -> bool:
    return str(os.getenv(_FL_ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def weak_defer_on() -> bool:
    return str(os.getenv(_WK_ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def weak_defer_hm() -> str:
    return str(os.getenv("WOLF_WEAK_DEFER_HM", "1445") or "1445").replace(":", "")


def falling(f: Dict[str, Any]) -> Tuple[bool, str]:
    """B：**在下跌中**——现价 < 当日 VWAP ∧（当日自开盘**逐根走低** ∨ **跌破当日开盘**）。

    口径是**量出来的**（2026-09-22 用户点「9.35 会被放行吧」后重做；1,332 笔有分钟档的买成交）：

    | 口径 | 拦下 | 影响面 | 命中单笔 | 其余单笔 |
    |---|---|---|---|---|
    | 破均价 ∧ 前 3 根连跌（初版） | 244 | 18% | −104 | −15 |
    | 破均价 ∧ 跌破当日开盘 | 450 | 34% | −94 | **+1** |
    | **破均价 ∧（逐根走低 ∨ 跌破开盘）← 本实现** | 478 | 36% | −92 | **+3** |
    | 破均价（不看趋势） | 530 | 40% | −87 | +5 |
    | 破均价 ∧ 跌破开盘 ∧ 前 3 根连跌（更严） | 241 | 18% | −91 | −18 |

    初版（只认"前 3 根连跌"）有两个硬伤，都会被**开盘头 10 分钟整段放过**：
      ① 09:35 只有 2 根 bar（集合竞价 + 第一根 5min）⇒ 判据无从成立；
      ② `_upto` 的冒号 bug 让 `feats()` 在 09:xx 直接返回 None ⇒ 整条闸 fail-open。
    改后：开盘头 15 分钟能拦 **161 笔**（−107/笔），其中就包括环旭电子 09:40 那笔。
    跨臂稳健性：28 条臂里 **21 条**"命中单笔更差"。
    """
    if not f or f.get("vwap") is None:
        return False, ""
    px = f.get("px")
    if px is None:
        return False, ""
    if float(px) >= float(f["vwap"]):
        return False, ""
    down_seq = bool(f.get("nb_down")) or bool(f.get("last3_down"))
    below_open = bool(f.get("below_open"))
    if not (down_seq or below_open):
        return False, ""
    _how = []
    if down_seq:
        _how.append("自开盘逐根走低")
    if below_open:
        _how.append("跌破当日开盘 %.3f" % float(f.get("open") or 0))
    return True, "现价 %.3f < 当日均价 %.3f 且 %s" % (float(px), float(f["vwap"]), "、".join(_how))


_V2_ENV = "WOLF_FALLING_GATE_V2"


def falling_v2_on() -> bool:
    """B 闸 **v2**（能区分"强势回踩"与"真缓跌"）；库内默认 0 ⇒ 逐字沿用 v1。"""
    # 用字面量读（便于机器校验 `guard_defaults_check` 扫描：它按 getenv("KEY", …) 正则找）
    return str(os.getenv("WOLF_FALLING_GATE_V2", "0")).strip().lower() in ("1", "true", "yes", "on")


def _near(px: float, level: Optional[float], tol_pct: float) -> bool:
    if not level or level <= 0 or px <= 0:
        return False
    return abs(px / float(level) - 1.0) * 100.0 <= float(tol_pct)


def falling_v2(f: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """B 闸 v2：**缓跌 = 分时形状下行 ∧ 且不命中他任何一类买点**。

    ## 为什么要有 v2（2026-09-25，用户指出"鱼龙混杂、要知道为什么拦"）

    v1（`falling`）只用**当日分时三个量**：现价 < 当日 VWAP ∧（自开盘逐根走低 ∨ 跌破当日开盘）。
    它**完全不看日线**（趋势/位置/相对强度）⇒ "强势股回踩"与"真缓跌"在它眼里是同一个形状，
    于是必然整段拦掉（实测：环旭电子 601231 在 2026-02-10 被拦 28 条买腿，价格 37.67~38.01；
    两天后该票 +10.9%、全月 +40.6%）。

    ## v2 的判据（**全部来自他的原话**，不新增自设条件）

    骨架仍是 v1 的"缓跌形状"；**豁免**（= 命中他说的买点 ⇒ 放行）四条：

    | 豁免 | 判据 | 语料（逐字） |
    |---|---|---|
    | E1 **到线** | 现价距 13/34/60/144 任一线 ≤ `tol_pct` | 「好票**跌到事先画好的线上（13/34/60/144）**…提前挂单到线上」(xls2025:257)；「位置没到（上下没到线）…继续挂单等着」(xls2025:182) |
    | E2 **到前低** | 现价距前低 ≤ `tol_pct` | 254 低吸=「破前低 + 缩量」；「跌下来直接补平」(xls2016:245) |
    | E3 **破一下当天拉回** | 当日低点 <（线或前低）∧ 现价 ≥ 该位 | 「C 缓跌的结束信号：不破 60 日线或**破一下当天拉回**」(nga:1873) |
    | E4 **急杀** | 近 2 根 5min 跌幅 ≥ `crash_pct` ∧ 量比 ≥ `crash_vr` | 「**急杀可以买，缓跌不买**」(2025-06-05)；「出现**急杀、某些票到线**→到线就长线配置」(xls2025:266) |

    **没有第 5 条**：不引入"相对强度/位置分位"这类他没说过的量 —— 那会变成自设规则。

    `ctx` 字段（全部可缺，缺 ⇒ 对应豁免不成立，退化为 v1 行为）：
      · `lines`: 序列 [13/34/60/144 均线值...]（**与分钟档同一复权空间**）
      · `prev_low`: 前一日最低（缺则用 feats 里的 `prev_low`）
      · `tol_pct`（默认 1.0）、`crash_pct`（默认 1.5）、`crash_vr`（默认 1.5）
    返回 `(是否拦, 原因)`；形状不坏 ⇒ `(False, "")`（不介入，与 v1 一致）。
    """
    if not f or f.get("vwap") is None:
        return False, ""
    px = f.get("px")
    if px is None:
        return False, ""
    # ① 骨架：v1 的"缓跌形状"（形状不坏 ⇒ 直接放行，v2 不新增拦截）
    bad, why1 = falling(f)
    if not bad:
        # ⚠️ 2026-09-30（账本 §9.416 ✓ 用户「改成他的配置，和他保持一致」✓）：
        #   「形状不坏 ⇒ 直接放行」这条会放走**"杀中买"** ✗ —— 实测该类 **T+10 −4.38%、胜率 9%** ✗✗
        #   而**深急杀**（相对前收 ≤−5%）⇒ **+3.81%、胜率 77%** ✓✓（他的「急杀可以买」✓）
        #   故：**跌幅尚浅（> −阈值）∧ 现价 < 当日开盘（当日偏弱）⇒ 不再自动放行** ✗，继续走 E1~E4 ✓
        #   开关 ✓：`WOLF_CRUSH_NO_MIDCRASH`（**库内默认 0 ＝ 关 ⇒ 生产逐字不变** ✓）
        if str(os.getenv("WOLF_CRUSH_NO_MIDCRASH", "0")).strip().lower() in ("1", "true", "yes", "on"):
            try:
                _dp = float(os.getenv("WOLF_CRUSH_DEEP_PCT", "5") or 5)
                _cg = f.get("chg_pct")
                _op = f.get("open")
                _shallow = (_cg is None) or (float(_cg) > -_dp)
                _weak = (_op is not None) and (float(px) < float(_op))
                if _shallow and _weak:
                    bad, why1 = True, ("当日跌幅 %s 未达深急杀（≤−%.1f%% ✗）且现价 %.3f < 当日开盘 %.3f（当日偏弱 ✗）"
                                       "⇒ 不在「杀中」买（账本 §9.416 ✓：该类胜率仅 9%% ✗）"
                                       % (("%.2f%%" % float(_cg)) if _cg is not None else "缺", _dp,
                                          float(px), float(_op)))
                else:
                    return False, ""
            except Exception:
                return False, ""
        else:
            return False, ""
    c = ctx or {}
    tol = float(c.get("tol_pct", 1.0) or 1.0)
    crash_pct = float(c.get("crash_pct", 1.5) or 1.5)
    crash_vr = float(c.get("crash_vr", 1.5) or 1.5)
    px = float(px)
    pl = c.get("prev_low") or f.get("prev_low")
    lines = [float(x) for x in (c.get("lines") or []) if x]
    day_lo = f.get("day_lo")
    vr = f.get("vr")
    # ② E1 到线
    hit_line = None
    for lv in lines:
        if _near(px, lv, tol):
            hit_line = lv
            break
    if hit_line:
        return False, "命中买点 E1 到线：现价 %.3f 距 %.3f ≤ %.1f%%（语料「跌到事先画好的线上 13/34/60/144」）" % (
            px, hit_line, tol)
    # ③ E2 到前低
    if _near(px, pl, tol):
        return False, "命中买点 E2 到前低：现价 %.3f 距前低 %.3f ≤ %.1f%%（语料 254「破前低 + 缩量」）" % (px, pl, tol)
    # ④ E3 破一下当天拉回（当日最低跌破线/前低，但现价收回其上）
    for lv in (lines + ([pl] if pl else [])):
        try:
            if day_lo is not None and float(day_lo) < float(lv) and px >= float(lv):
                return False, ("命中买点 E3 破一下当天拉回：当日低 %.3f < %.3f 且现价 %.3f 已收回"
                               "（语料「不破 60 日线或破一下当天拉回」）" % (float(day_lo), float(lv), px))
        except Exception as _e_sil5:
            _silent_alert("intraday_crush.py:488", _e_sil5)
            continue
    # ⑤ E4 急杀（近 2 根跌幅 + 放量）
    try:
        drop2 = f.get("last2_drop_pct")
        # ⚠️ 2026-09-30（账本 §9.415 ✓）：**深急杀**条件 —— 他「急杀可以买，缓跌不买」✓
        #   证据边界（§9.413 ✓ 89.97 万样本 ✓）：**深急杀 ≤−5%（相对前收）＋ 近前低 ⇒ +2.98%、胜率 62.4%** ✓✓
        #   而「近 2 根跌 ≥1.5%」的**浅急杀无效** ✗（+0.44%、49.1% ✗）⇒ 故加此条件 ✓
        #   开关 ✓：`WOLF_CRUSH_DEEP_ONLY`（**库内默认 0 ＝ 关 ⇒ 生产逐字不变** ✓）
        _deep_on = str(os.getenv("WOLF_CRUSH_DEEP_ONLY", "0")).strip().lower() in ("1", "true", "yes", "on")
        _deep_pct = float(os.getenv("WOLF_CRUSH_DEEP_PCT", "5") or 5)
        _chg = f.get("chg_pct")
        _deep_ok = (not _deep_on) or (_chg is not None and float(_chg) <= -_deep_pct)
        if (drop2 is not None and float(drop2) >= crash_pct and (vr is None or float(vr) >= crash_vr)
                and _deep_ok):
            return False, ("命中买点 E4 急杀：近 2 根 5min 跌 %.2f%%（≥%.2f%%）且量比 %s（≥%.1f）"
                           "且相对前收 %s（≤−%.1f%% ✓）"
                           "（语料 2025-06-05「急杀可以买，缓跌不买」，账本 §9.413 深急杀边界 ✓）"
                           % (float(drop2), crash_pct, ("%.2f" % float(vr)) if vr else "缺", crash_vr,
                              ("%.2f%%" % float(_chg)) if _chg is not None else "缺", _deep_pct))
    except Exception as _e_sil6:
        _silent_alert("intraday_crush.py:508", _e_sil6)
    # ⑥ 都不命中 ⇒ 拦（真缓跌）
    _nearest = min([abs(px / lv - 1) * 100 for lv in lines], default=None)
    _pl_d = (abs(px / float(pl) - 1) * 100) if pl else None
    return True, ("缓跌且**未命中任何买点**：现价 %.3f < 均价 %.3f 且 %s；距最近线 %s、距前低 %s、"
                  "近2根跌幅 %s、量比 %s（语料 2025-06-05「急杀可以买，缓跌不买」）"
                  % (px, float(f["vwap"]), why1.replace("现价 %.3f < 当日均价 %.3f 且 " % (px, float(f["vwap"])), ""),
                     ("%.2f%%" % _nearest) if _nearest is not None else "无线数据",
                     ("%.2f%%" % _pl_d) if _pl_d is not None else "无前低",
                     ("%.2f%%" % float(f["last2_drop_pct"])) if f.get("last2_drop_pct") is not None else "缺",
                     ("%.2f" % float(vr)) if vr else "缺"))


def weak_day(f: Dict[str, Any], pre_close: Optional[float]) -> Tuple[bool, str]:
    """C：当日为跌（现价 < 昨收）∧ 现价 < 当日 VWAP（"弱势日"）。"""
    if not f or f.get("vwap") is None or not pre_close or float(pre_close) <= 0:
        return False, ""
    px = f.get("px")
    if px is None:
        return False, ""
    if float(px) >= float(pre_close):
        return False, ""
    if float(px) >= float(f["vwap"]):
        return False, ""
    return True, "当日走弱：现价 %.3f < 昨收 %.3f 且 < 当日均价 %.3f" % (
        float(px), float(pre_close), float(f["vwap"]))


def timing_verdict(symbol: Any, day: str, hhmm: str, pre_close: Optional[float] = None,
                   side: str = "buy") -> Tuple[bool, str]:
    """B/C 总入口：返回 (是否放行, 原因)。卖腿一律放行；分钟档取不到一律放行（fail-open）。"""
    if str(side or "buy").strip().lower() not in ("buy", "买入"):
        return True, ""
    if not (falling_on() or weak_defer_on()):
        return True, ""
    s = str(symbol or "").upper()
    code = (s[2:8] + "_" + s[:2]) if (s[:2] in ("SH", "SZ") and len(s) >= 8) else s.replace(".", "_")
    # min_bars=2：09:35 只有 2 根 bar（集合竞价 + 第一根 5min），用 3 会让开盘头 10 分钟整段 fail-open
    f = feats(code, str(day), str(hhmm), min_bars=2)
    if not f:
        return True, "缺分钟档→放行"
    if falling_on():
        bad, why = falling(f)
        if bad:
            return False, ("不在下跌中买（B）：%s —— 语料 2025-06-05「急杀可以买，缓跌不买」；"
                           "本腿保留活性，企稳后会被重新评估" % why)
    if weak_defer_on():
        bad2, why2 = weak_day(f, pre_close)
        if bad2 and str(hhmm).replace(":", "") < weak_defer_hm():
            return False, ("当日走弱→延后到尾盘再评（C）：%s —— 他 2025-05-23「尾盘能回来就尾盘买 急什么」、"
                           "2026-01-12「买点只有尾盘」；本腿保留活性，%s 后重评" % (why2, weak_defer_hm()))
    return True, ""
