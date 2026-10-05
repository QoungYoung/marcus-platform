# -*- coding: utf-8 -*-
"""wolf_high_pos_vol.py —— 「位置高 ∧ 前一日量能放大 ⇒ 不买」买腿闸（2026-09-20）

**语料（方向有据、数值拟合）**
  · 2026-07-31「低位看逻辑，**高位看量价**；基本面没问题」→ 高位必须叠加量价条件；
  · 2021-04-02「突破后会有个分歧点…底部的人全解套了…**别山顶接就好了**」；
  · 2025-04-03「跌下来可以找买点，**但是冲上去一定不能追**」；
  · 2026「**放量黑K或放量黑上影**就结束参与」（同族：放量属风险侧）。
  ⚠️ 阈值（距 20 日低 ≥ 20% / 距 MA20 ≥ 8% / 前一日量 > 5 日均量）**是我们拟合的，不是他的原话数字**。

**离线证据**（脚本 `.dsh-tmp/wolfbt/_highpos_*.py` + 修正版 `_hpv_recheck.py`；口径＝量比，与实现一致）
  · y26 78 轮：拦 11 轮 Δ **+10,267**（拦到 6/15 大亏、误伤 1/11 大赚，置换 p=0.990）；
  · draymar 63 轮：拦 7 轮 Δ **+7,936**（拦到 5/11、误伤 0/10，p=0.999）；
  · 小账户同向：drabbranch +2,237、drabsize +230（drabm56 无命中）；
  · ⚠️ 修正（2026-09-20）：早期版本用**换手率**口径得到 +12,704 / +10,373，但该库 `turnover_rate`
    在 2025-12 仅 4.3% 覆盖 ⇒ 1 月初买点的前 5 日窗口缺值、被 pandas 跳过 NaN 求均值污染。
    同定义重跑后：换手口径 +8,547 / +6,216（且 10–13% 轮次不可用），**量比口径 +10,267 / +7,936** ⇒ 采用量比。
  · 轮次级 5 个账户方向一致；同日配对两账户同向；**纯位置闸（不含量能）在 draymar 会塌（+15,376→+3,487）**
    ⇒ 「位置高」是必要条件，「前一日量能放大」才是把它变成可用的那一半。
  · 混淆检查：高位组 2×2，「放量」不是「前一日大涨」的马甲（两账户、两种前日方向下放量组都为负）。

**口径**
  · 特征只用 **买入日前一交易日的收盘日线**（`trade_date < day` 强制过滤，无未来函数）；
  · 量能口径用**成交量比**（前一日量 vs 前 5 日均量）——实测与「换手率比」在 276 个真实买点上**一致率 100%**，
    且不依赖流通股本字段；
  · 只作用于**新开底仓**（`WOLF_HPV_SCOPE=new`，默认）：持仓中的加仓/做T买回不受影响。

**数据源限制（重要）**：as-of 日线目前只有回测源（`data/_bt_full/bars.sqlite`）⇒ 本闸**当前是回测专用**，
  生产环境取不到该库会 fail-open 放行（=生产零影响）。要让它在生产生效，必须先接一条生产可用的 as-of 日线
  （带交易日期的日线 + 换手率），否则就是"看起来有闸、实际不生效"。

**开关（全部库内默认关/原值 ⇒ 生产零影响）**
  WOLF_HIGH_POS_VOL=0        总开关（回测 pins 置 1）
  WOLF_HPV_POS_MODE=low20    高位判据：low20 | ma20 | either | both
  WOLF_HPV_LOW20_PCT=20      距 20 日低点 ≥ 该百分数 ⇒ 高位
  WOLF_HPV_MA20_PCT=8        距 MA20 ≥ 该百分数 ⇒ 高位
  WOLF_HPV_VOL_MULT=1.0      前一日量 > mult × 前 5 日均量 ⇒ 放量
  WOLF_HPV_VOL_FIELD=auto    auto（换手优先、量比兜底）| turnover | vol
  WOLF_HPV_SCOPE=new         new | all（all=连加仓/回补也拦）
"""
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


ENV = "WOLF_HIGH_POS_VOL"
_SCOPE_ENV = "WOLF_HPV_SCOPE"
_MODE_ENV = "WOLF_HPV_POS_MODE"
_LOW20_ENV = "WOLF_HPV_LOW20_PCT"
_MA20_ENV = "WOLF_HPV_MA20_PCT"
_VOLMULT_ENV = "WOLF_HPV_VOL_MULT"
_BARS_DB_ENV = "WOLF_HPV_BARS_DB"

_STATS: Dict[str, int] = {"calls": 0, "blocked": 0, "allowed": 0, "nodata": 0, "err": 0}


def enabled() -> bool:
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def scope() -> str:
    """`new`（默认）=只拦首次建仓；`all`=连加仓/做T买回一起拦。"""
    return str(os.getenv(_SCOPE_ENV, "new")).strip().lower() or "new"


def applies(held: bool = False, sc: Optional[str] = None) -> bool:
    """该买单是否受本闸约束（纯函数）。`held` 未知时按 True 处理（=视为再买，不拦，保守）。"""
    s = str(sc if sc is not None else scope()).strip().lower()
    if s == "all":
        return True
    return not bool(held)


def pos_mode() -> str:
    m = str(os.getenv(_MODE_ENV, "low20")).strip().lower()
    return m if m in ("low20", "ma20", "either", "both") else "low20"


def _num(env: str, dflt: float) -> float:
    try:
        return float(os.getenv(env, str(dflt)))
    except Exception:
        return dflt


def low20_pct() -> float:
    return _num(_LOW20_ENV, 20.0)


def ma20_pct() -> float:
    return _num(_MA20_ENV, 8.0)


def vol_mult() -> float:
    return _num(_VOLMULT_ENV, 1.0)


def stats() -> Dict[str, int]:
    return dict(_STATS)


def stats_reset() -> None:
    for k in _STATS:
        _STATS[k] = 0


# ── 数据 ──────────────────────────────────────────────────────────────────
def _f(x) -> Optional[float]:
    # ★ 账本 §9.643 ✓（用户 QQ 告警 `float('')` ✗ ×378 ✓）：
    #   空/缺失值先挡掉 ✓ ⇒ **行为完全不变**（原来也是走 except 分支 ✓），
    #   但**不再抛异常** ✗ ⇒ 告警自动消失 ✓
    if x is None or (isinstance(x, str) and x.strip() in ("", "nan", "NaN", "-", "null", "None")):
        return None
    try:
        v = float(x)
        return v if v == v else None            # NaN → None
    except Exception:
        try:
            v = float(str(x).strip() or "nan")
            return v if v == v else None
        except Exception:
            return None


def _norm(bars) -> List[Dict[str, float]]:
    """容错归一化（接受 dict 列表 / {date: {...}} / 元组列表），保留 vol。"""
    out: List[Dict[str, float]] = []
    seq = bars
    if isinstance(bars, dict):
        seq = [bars[k] for k in sorted(bars)]
    for b in (seq or []):
        try:
            if isinstance(b, dict):
                c = _f(b.get("close"))
                if c is None or c <= 0:
                    continue
                out.append({"date": str(b.get("date") or ""), "close": c,
                            "high": _f(b.get("high")) or c, "low": _f(b.get("low")) or c,
                            "open": _f(b.get("open")) or c, "vol": _f(b.get("vol")) or 0.0,
                            "turnover_rate": _f(b.get("turnover_rate")) or 0.0})
            elif isinstance(b, (list, tuple)) and len(b) >= 5:
                c = _f(b[4])
                if c is None or c <= 0:
                    continue
                out.append({"date": str(b[0]), "open": _f(b[1]) or c, "high": _f(b[2]) or c,
                            "low": _f(b[3]) or c, "close": c,
                            "vol": _f(b[5]) if len(b) > 5 else 0.0,
                            "turnover_rate": _f(b[6]) if len(b) > 6 else 0.0})
        except Exception as _e_sil1:
            _silent_alert("wolf_high_pos_vol.py:143", _e_sil1)
            continue
    return out


def bars_asof(symbol: str, day: str, n: int = 30):
    """**前一交易日及更早**的日线（强制 `trade_date < day`）。

    回测专用源：`data/_bt_full/bars.sqlite`（回放的权威日线源，路径可用 `WOLF_HPV_BARS_DB` 覆盖）。
    生产（无该库）自动返回 None ⇒ 调用方放行，**生产零影响**。
    """
    p = os.getenv(_BARS_DB_ENV) or ""
    if not p:
        cand = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), "data", "_bt_full", "bars.sqlite")
        p = cand if os.path.exists(cand) else ""
    if not p or not os.path.exists(p):
        return None
    try:
        import sqlite3
        s = str(symbol).upper()
        code = ("%s.%s" % (s[2:], s[:2])) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s
        con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
        try:
            rows = con.execute("""select trade_date,open,high,low,close,vol,turnover_rate from bars
                                  where ts_code=? and trade_date<? order by trade_date desc limit ?""",
                               (code, str(day), int(n))).fetchall()
        finally:
            con.close()
        out = [{"date": str(r[0]), "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                "vol": r[5], "turnover_rate": (r[6] if len(r) > 6 else None)} for r in reversed(rows)]
        return out or None
    except Exception:
        return None


# ── 判据（纯函数）────────────────────────────────────────────────────────
def metrics(bars) -> Optional[Dict[str, float]]:
    """由 as-of 日线算「位置」与「量能」指标。数据不足 ⇒ None。"""
    b = _norm(bars)
    if len(b) < 21:
        return None
    b = [x for x in b if x["close"] > 0]
    if len(b) < 21:
        return None
    last = b[-1]
    closes = [x["close"] for x in b[-20:]]
    lows = [x["low"] for x in b[-20:]]
    ma20 = sum(closes) / len(closes)
    lo20 = min(lows)
    prev5 = b[:-1][-5:]                                    # 前 5 日（不含前一日）
    vols = [x["vol"] for x in prev5]
    v1 = last["vol"]
    v5 = (sum(vols) / len(vols)) if vols and all(v > 0 for v in vols) else 0.0
    t1, t5 = last["turnover_rate"], 0.0
    tprev = [x["turnover_rate"] for x in prev5]
    if tprev and all(x > 0 for x in tprev) and t1 > 0:
        t5 = sum(tprev) / len(tprev)
    if lo20 <= 0 or ma20 <= 0:
        return None
    return {"close": last["close"], "ma20": ma20, "lo20": lo20,
            "dist_ma20_pct": (last["close"] / ma20 - 1.0) * 100.0,
            "dist_low20_pct": (last["close"] / lo20 - 1.0) * 100.0,
            "vol1": v1, "vol5": v5, "vol_ratio": (v1 / v5) if v5 > 0 else None,
            "turn1": t1, "turn5": t5, "turn_ratio": (t1 / t5) if t5 > 0 else None,
            "date": last["date"]}


def pos_high(m: Dict[str, float], mode: Optional[str] = None) -> bool:
    """位置是否算「高」（按 `WOLF_HPV_POS_MODE` 组合）。"""
    md = str(mode if mode is not None else pos_mode()).lower()
    a = bool(m.get("dist_low20_pct", -1e9) >= low20_pct())
    c = bool(m.get("dist_ma20_pct", -1e9) >= ma20_pct())
    if md == "ma20":
        return c
    if md == "either":
        return a or c
    if md == "both":
        return a and c
    return a


def vol_field() -> str:
    """量能字段：`auto`（默认＝**量比优先、换手兜底**）/ `turnover` / `vol`。

    为什么量比优先（2026-09-20 修正）：本库 `turnover_rate` 在 **2025-12 只有 4.3% 覆盖**
    （2026-01~04 才是 100%）⇒ 1 月初买点的"前 5 日均换手"会因缺值而失真，实测 10–13% 的轮次
    上换手口径**根本不可用**；而 `vol` 全窗口 100% 稠密、口径稳定。早期离线版用的正是被污染的
    换手口径（pandas 跳过 NaN 求均值）⇒ 已按同一干净定义重跑，结论不变但数值更小（见模块头）。
    """
    v = str(os.getenv("WOLF_HPV_VOL_FIELD", "vol")).strip().lower()
    return v if v in ("auto", "turnover", "vol") else "vol"


def vol_ratio_of(m: Dict[str, float]) -> Optional[float]:
    """按 `vol_field()` 取量能比（None=数据不可用 ⇒ 放行侧）。"""
    f = vol_field()
    if f == "turnover":
        return m.get("turn_ratio")
    if f == "vol":
        return m.get("vol_ratio")
    return m.get("vol_ratio") if m.get("vol_ratio") is not None else m.get("turn_ratio")


def vol_expanded(m: Dict[str, float], mult: Optional[float] = None) -> bool:
    """前一日量能是否放大（> mult × 前 5 日均量）。数据缺失 ⇒ False（放行侧）。"""
    r = vol_ratio_of(m)
    if r is None:
        return False
    return bool(float(r) > float(vol_mult() if mult is None else mult))


def verdict(bars, held: bool = False, day: str = "", symbol: str = "") -> Tuple[bool, str]:
    """返回 (是否拦下, 原因)。**任何异常/数据缺失 ⇒ 放行**（fail-open）。

    拦下条件：开关开 ∧ 适用（默认只新开）∧ 位置高 ∧ 前一日量能放大。
    """
    _STATS["calls"] += 1
    try:
        if not enabled() or not applies(held):
            _STATS["allowed"] += 1
            return False, ""
        m = metrics(bars)
        if not m:
            _STATS["nodata"] += 1
            return False, "无 as-of 日线（放行）"
        if not pos_high(m):
            _STATS["allowed"] += 1
            return False, ""
        if not vol_expanded(m):
            _STATS["allowed"] += 1
            return False, ""
        why = ("位置高(距20日低 %+.1f%%≥%.0f%%%s) ∧ 前一日量能放大(量比 %.2f>%.2f) ⇒ 不买"
               % (m["dist_low20_pct"], low20_pct(),
                  "，距MA20 %+.1f%%≥%.0f%%" % (m["dist_ma20_pct"], ma20_pct())
                  if pos_mode() in ("ma20", "either", "both") else "",
                  float(vol_ratio_of(m) or 0.0), vol_mult()))
        _STATS["blocked"] += 1
        print("[HPV] 高位放量不买 %s%s: %s" % (symbol, ("@%s" % day) if day else "", why), flush=True)
        return True, why
    except Exception as e:
        _STATS["err"] += 1
        return False, "ERR " + str(e)[:60]
