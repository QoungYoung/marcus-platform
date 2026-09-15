# -*- coding: utf-8 -*-
"""wolf_entry_filters.py — 建仓腿的**两道真闸门**（2026-09-15，用户指示：真对接生产，不做只记录影子）。

闸门 1 · `WOLF_CLOSE_POS_GATE`（默认 **1**）：**距前低最远 ∧ 半强收盘 → 不买**
  证据（总账 §50）：`dist_prevlow 高档 ∧ pos∈[0.5,0.8)` 这一交叉格 −2.270%（块状 t −3.14），
  H1 −1.514（t −3.19）/ H2 −3.034（t −2.19）两段一致；同档其它腿不显著（−0.808/t −0.30）。
  · 「高档」不写死数值：按**当日候选域**的 dist_prevlow 三分位动态取（避免自设阈值）；
  · `pos = (close − low) / (high − low)`（当日收盘在当日区间的位置），`[0.5, 0.8)` 来自证据。

闸门 2 · `WOLF_DEFENSIVE_GATE`（默认 **1**）：**低位 ∧ 相对大盘不弱**（他的"抗跌"）
  他的话：2021-01-27「什么叫防御型，就是**大盘跌的时候他少跌一点**」（比较基准=大盘，有原话）；
  2025-10-23「防御类品种，就是那种跌了也就最多跌 2 个点的」（特征描述）；2026-04-07「低位横盘多时…」。
  · 「低位」= dist_prevlow 处于**当日候选域下三分位**（与闸门 1 同一分位口径，动态，不写死）；
  · 「不弱」= 近 `WOLF_DEF_REL_WIN`（**默认 20，自设**）交易日个股涨幅 ≥ 同期指数涨幅（相对超额 ≥0）；
  · ⚠️ 窗口 20 日是**自设**（他没给窗口），可 env 调；阈值"相对超额 ≥0"直接来自"少跌一点"的语义。

**分位口径（2026-09-15 round 40 用户指令：`分位改在更宽的候选域上算`）**
  第一版把三分位算在**当日终选腿**（3~4 条）上 → 域内样本 <6 触发"不判（放行）"，
  两道闸门**实际从未生效**（DRY 实测无 `ENTRY_FILTER` 行）。现在改为按**当日候选域**算分位：
  · 候选域 = 当日各条选股路径**实际评估过的可买候选**（路径 B：`pick_v2` 的候选池 ∩ LOW/MID；
    路径 A：`pick_buy` 短名单里位置闸 LOW/MID 的候选），由调用方按腿传入（`domain=`），
    通常几十只 → 三分位有意义；
  · 分位把两条路径、各主题的候选**并成一个当日域**（与 §50 证据的池化口径一致）；
  · 域内样本仍 <`DOMAIN_MIN`(6) → 退回旧口径（用终选腿）并在 stderr/审计文件里写明 `src=legs`；
  · 腿不在域里（如 ETF 兜底腿/legacy 回退腿）→ 该腿单独 `leg_features()` 取数，不影响别人的分位；
  · 回退开关：`WOLF_ENTRY_QDOMAIN=legs` 恢复"只看终选腿"的旧口径（= 实际不生效状态）。

两道闸门都只作用于**新开买腿**；失败/数据缺失一律**放行**（fail-open，绝不因为算不出而停买）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

DATA = os.environ.get("DATA_DIR", "/app/data")
MA_LINE_MIN_CLOSES = 144          # 与 rotation_switch_arm 一致：算得出 144 线所需的最少 K 线
POS_LO, POS_HI = 0.5, 0.8         # 闸门 1 的 pos 区间（来自 §50 的交叉格）
DOMAIN_MIN = 6                    # 分位所需最少域内样本（不足 → 不判/放行）


def close_pos_enabled() -> bool:
    return os.getenv("WOLF_CLOSE_POS_GATE", "1").strip().lower() not in ("0", "false", "no")


def defensive_enabled() -> bool:
    return os.getenv("WOLF_DEFENSIVE_GATE", "1").strip().lower() not in ("0", "false", "no")


def def_rel_win() -> int:
    try:
        return max(int(os.getenv("WOLF_DEF_REL_WIN", "20")), 2)
    except Exception:
        return 20


def qdomain_mode() -> str:
    """分位域口径：`cand`（默认，当日候选域）/ `legs`（旧口径，只看终选腿 = 实际不生效）。"""
    return (os.getenv("WOLF_ENTRY_QDOMAIN", "cand").strip().lower() or "cand")


# ── 候选域（2026-09-15 round 40）──────────────────────────────────
def _sym_of(d: Dict[str, Any]) -> Optional[str]:
    for k in ("symbol", "xq", "ts_code", "ts", "code"):
        v = d.get(k)
        if v:
            return str(v)
    return None


def _fnum(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # NaN → None


def domain_map(domain: Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """候选域条目 → `{symbol: {dist_prevlow, pos, ret20}}`（字段名与闸门口径一致）。

    入参允许两种形态（两种都常见）：
      · `{"symbol": "SH600000", "dist_prevlow": 1.2, "pos": 0.63, "ret20": 3.1}`
      · `{"xq": "SH600000", "dist_prevlow_prev": 1.2, "pos_range": 0.63, "r20": 3.1}`
        （`pick_v2` 的字段名；它的 `dist_prevlow` 是**距当日低**、`dist_prevlow_prev` 才是距**前一日**低，
          与闸门口径一致的是后者 —— 这里显式做映射，避免又一次"同名不同义"）
    """
    out: Dict[str, Dict[str, Any]] = {}
    for d in (domain or []):
        if not isinstance(d, dict):
            continue
        s = _sym_of(d)
        if not s:
            continue
        out[s] = {
            "dist_prevlow": _fnum(d.get("dist_prevlow", d.get("dist_prevlow_prev"))),
            "pos": _fnum(d.get("pos", d.get("pos_range"))),
            "ret20": _fnum(d.get("ret20", d.get("r20"))),
        }
    return out


# ── 纯函数（可单测）──────────────────────────────────────────────
def tercile_high(values: Sequence[float]) -> Optional[float]:
    """该组数值的**上三分位下限**（≥ 它算"高档"）；样本 <6 个返回 None（不判）。"""
    v = sorted(float(x) for x in values if x is not None)
    if len(v) < 6:
        return None
    k = int(len(v) * 2 / 3)
    return v[min(k, len(v) - 1)]


def tercile_low(values: Sequence[float]) -> Optional[float]:
    """该组数值的**下三分位上界**（≤ 它算"低档"）；样本 <6 个返回 None。"""
    v = sorted(float(x) for x in values if x is not None)
    if len(v) < 6:
        return None
    k = int(len(v) / 3)
    return v[min(k, len(v) - 1)]


def pos_of(close: float, high: float, low: float) -> Optional[float]:
    if high is None or low is None or close is None or high <= low:
        return None
    return (float(close) - float(low)) / (float(high) - float(low))


def close_pos_blocked(dist_prevlow: Optional[float], pos: Optional[float],
                      pool: Sequence[float]) -> Tuple[bool, str]:
    """闸门 1：距前低高档 ∧ pos∈[0.5,0.8) → 拦。"""
    if dist_prevlow is None or pos is None:
        return False, "数据缺失 → 放行"
    cut = tercile_high(pool)
    if cut is None:
        return False, "候选池样本<6 → 不判（放行）"
    if dist_prevlow >= cut and POS_LO <= pos < POS_HI:
        return True, ("距前低高档(%.2f≥%.2f) ∧ 半强收盘(pos=%.2f∈[%.1f,%.1f)) → 不买（§50 交叉格）"
                      % (dist_prevlow, cut, pos, POS_LO, POS_HI))
    return False, "未命中交叉格"


def defensive_blocked(dist_prevlow: Optional[float], ret_stock: Optional[float],
                      ret_index: Optional[float], pool: Sequence[float]) -> Tuple[bool, str]:
    """闸门 2：低位（dist 低档）∧ 近 N 日弱于大盘 → 拦（他的"抗跌"）。"""
    if dist_prevlow is None or ret_stock is None or ret_index is None:
        return False, "数据缺失 → 放行"
    cut = tercile_low(pool)
    if cut is None:
        return False, "候选池样本<6 → 不判（放行）"
    if dist_prevlow <= cut and ret_stock < ret_index:
        return True, ("低位(dist=%.2f≤%.2f) ∧ 近%d日弱于大盘(个股%.2f%% < 指数%.2f%%) → 不买（他的抗跌）"
                      % (dist_prevlow, cut, def_rel_win(), ret_stock, ret_index))
    return False, "未命中抗跌格"


# ── 取数与批量过滤（供 rotation_switch_arm 调用）──────────────────
def leg_features(symbol: str, as_of: str) -> Dict[str, Any]:
    """→ {dist_prevlow, pos, ret20, close, prev_low}（拉 540 天日线；失败返回 {}）。"""
    try:
        import datetime as _dt
        _p = os.path.dirname(os.path.abspath(__file__))
        if _p not in sys.path:
            sys.path.insert(0, _p)
        import wolf_confirm_pick as _WCP
        s = str(symbol)
        ts = (s[2:] + "." + s[:2]) if s[:2] in ("SH", "SZ") else s
        start = (_dt.date.today() - _dt.timedelta(days=540)).strftime("%Y%m%d")
        rows = _WCP.gz("daily", {"ts_code": ts, "start_date": start, "end_date": as_of},
                       "ts_code,trade_date,close,low,amount,high") or []
        rows = sorted(rows, key=lambda x: str(x[1]))
        if len(rows) < MA_LINE_MIN_CLOSES:
            return {}
        closes = [float(x[2]) for x in rows]
        lows = [float(x[3]) for x in rows]
        highs = [float(x[5]) for x in rows]
        prev_low = lows[-2] if len(lows) >= 2 else None
        dist = ((closes[-1] / prev_low - 1) * 100.0) if prev_low else None
        win = def_rel_win()
        ret = ((closes[-1] / closes[-1 - win] - 1) * 100.0) if len(closes) > win else None
        return {"dist_prevlow": dist, "pos": pos_of(closes[-1], highs[-1], lows[-1]),
                "ret20": ret, "close": closes[-1], "prev_low": prev_low}
    except Exception as e:
        print("ENTRY_FILTER_FEAT_ERR", symbol, str(e)[:70], file=sys.stderr)
        return {}


def index_ret(win: int, as_of: str) -> Optional[float]:
    """同期指数涨幅（000001.SH）。"""
    try:
        _p = os.path.dirname(os.path.abspath(__file__))
        if _p not in sys.path:
            sys.path.insert(0, _p)
        import wolf_ma144_regime as _M
        rows = _M.closes(refresh=False) or []
        seq = [c for d, c in rows if str(d) <= str(as_of)]
        if len(seq) > win:
            return (seq[-1] / seq[-1 - win] - 1) * 100.0
    except Exception as e:
        print("INDEX_RET_ERR", str(e)[:60], file=sys.stderr)
    return None


def filter_legs(legs: List[Dict[str, Any]], as_of: str,
                domain: Optional[Sequence[Dict[str, Any]]] = None
                ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """对**新开买腿**施加两道闸门 → (保留, 被拦[{symbol, why}])。数据缺失一律放行。

    `domain` = **当日候选域**（各选股路径实际评估过的可买候选，带特征）——三分位按它算；
    缺省/过窄时退回"只看终选腿"的旧口径（`src=legs`），并在审计文件里写明。
    """
    if not legs:
        return legs, []
    on1, on2 = close_pos_enabled(), defensive_enabled()
    if not (on1 or on2):
        return legs, []
    dom = domain_map(domain) if qdomain_mode() == "cand" else {}
    feats: Dict[str, Dict[str, Any]] = {}
    for b in legs:
        s = b.get("symbol")
        f = dict(dom.get(s) or {})
        if f.get("dist_prevlow") is None or (on1 and f.get("pos") is None) or (on2 and f.get("ret20") is None):
            # 域外腿（ETF 兜底 / legacy 回退 / 域里缺字段）→ 单独取数兜底；域值优先，缺项补齐
            _lf = leg_features(s, as_of) or {}
            _lf.update({k: v for k, v in f.items() if v is not None})
            f = _lf
        feats[s] = f
    pool = [d["dist_prevlow"] for d in dom.values() if d.get("dist_prevlow") is not None]
    src = "cand"
    if len(pool) < DOMAIN_MIN:                      # 候选域过窄/缺失 → 退回旧口径（终选腿）
        pool = [f.get("dist_prevlow") for f in feats.values() if f.get("dist_prevlow") is not None]
        src = "legs"
    cut_hi = tercile_high(pool) if on1 else None
    cut_lo = tercile_low(pool) if on2 else None
    idx = index_ret(def_rel_win(), as_of) if on2 else None
    kept, blocked, trace = [], [], []
    for b in legs:
        f = feats.get(b.get("symbol")) or {}
        why = None
        if on1:
            hit, w1 = close_pos_blocked(f.get("dist_prevlow"), f.get("pos"), pool)
            if hit:
                why = w1
        if why is None and on2:
            hit, w2 = defensive_blocked(f.get("dist_prevlow"), f.get("ret20"), idx, pool)
            if hit:
                why = w2
        if why:
            blocked.append({"symbol": b.get("symbol"), "why": why})
        else:
            kept.append(b)
        trace.append({"symbol": b.get("symbol"),
                      "dist_prevlow": f.get("dist_prevlow"), "pos": f.get("pos"),
                      "ret20": f.get("ret20"), "in_domain": b.get("symbol") in dom,
                      "blocked": bool(why), "why": why})
    print("ENTRY_FILTER_DOMAIN n=%d src=%s cut_hi=%s cut_lo=%s idx_ret20=%s legs=%d blocked=%d"
          % (len(pool), src,
             ("%.2f" % cut_hi) if cut_hi is not None else "None",
             ("%.2f" % cut_lo) if cut_lo is not None else "None",
             ("%.2f" % idx) if idx is not None else "None",
             len(legs), len(blocked)), file=sys.stderr)
    _record(as_of, {"date": as_of, "qdomain": qdomain_mode(), "domain_n": len(pool),
                    "domain_raw_n": len(dom), "src": src,
                    "cut_hi": cut_hi, "cut_lo": cut_lo, "index_ret20": idx,
                    "gate_close_pos": on1, "gate_defensive": on2,
                    "legs": trace, "blocked": blocked})
    return kept, blocked


def _record(as_of: str, obj: Dict[str, Any]) -> None:
    """审计文件 `data/entry_filter_<date>.json`（不改决策，只为"这道门到底看的是什么"留证）。"""
    try:
        os.makedirs(DATA, exist_ok=True)
        fn = os.path.join(DATA, "entry_filter_%s.json" % as_of)
        cur = {}
        if os.path.exists(fn):
            try:
                with open(fn, encoding="utf-8") as f:
                    cur = json.load(f) or {}
            except Exception:
                cur = {}
        if not isinstance(cur, dict):
            cur = {}
        if cur.get("date") == as_of and isinstance(cur.get("calls"), list):
            obj = dict(cur, **{k: v for k, v in obj.items() if k != "calls"})
            obj["calls"] = (cur["calls"] + [{"ts": time.strftime("%H:%M:%S")}])[-20:]
        else:
            obj["calls"] = [{"ts": time.strftime("%H:%M:%S")}]
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("ENTRY_FILTER_WRITE_ERR", str(e)[:80], file=sys.stderr)

