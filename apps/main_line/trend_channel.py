# -*- coding: utf-8 -*-
"""trend_channel.py — 「趋势/突破通道」（2026-09-21 用户拍板：先加到回测里）

────────────────────────────────────────────────────────────────
**为什么需要它**：现有 confirm_chain 只有「低位埋伏」一条路（S1 缩量止跌 → S2 结构到位 →
S3 放量突破 → S4 站稳），且 S1 是**前置**；而 S1 的量化条件退化成"缩量"（调用方 net=None）⇒
**放量上涨的票必然判「下跌中」**。实测：2026 年 1 月 兆易创新(+47.0%)、长电科技(+34.5%)、
通富微电(+38.1%) 全月被判「下跌中」⇒ 一手未买（逐日标签已复现，见台账 2026-09-21 节）。

**语料（趋势/突破语境的真实逻辑）**
  · 2026-02-12「**放量突破 一口吃完上面挂单**。不行分两口也可以」
  · 2026-01-12「3-2 的结束确认条件是什么 日线级别W底后的红三兵表现 突破颈线的加速
               **4006站稳三天后量能的暴涨** 吃技术流和踏空资金的进场溢价」
  · 2025-07-09「缩量跌，放量涨，**放量突破就是牛市最基础的逻辑**」
  · 2025-06-05「要么**带量突破** 空翻多 50%仓位加到80%…**不带量突破压力线，清仓**」
  · 2025-03-19「日K线W底上穿**带量突破均线 缩量回踩均线 继续带量上涨**」（他称顶级教科书级别）
  · 2026-09-03「**高开不追是基本常识**」／2025-04-15 条件6「想追进去的…**在下午 2.00-2.30** 回补」
  · 2026-05-11「高位这些 PCB CPO 的标…**放量滞涨**…其实就是交换筹码了…**不追高**，性价比不高」
  · 2026-01-27「一旦选择方向**向上突破，全手摁进去**」／2025-10-22「**一次摁一部分 摁2-3次**」

**本版实现范围（v1，刻意收窄）**
  ✅ T1 带量突破：收盘 ≥ 近 N=20 日最高收盘 × 1.005 ∧ 量能 z20 ≥ 1.5
  ✅ T2 站稳：突破日后 ≤3 交易日，收盘均 ≥ 突破日收盘 × 0.97 且 ≥ MA10
  ✅ T3 不在当根追：**发腿发生在突破确认后的下一个交易日**（结构上天然满足）
  ✅ T5 否证：位置分位 ≥ 0.8 ∧ 前一日放量 ∧ 前一日收盘 < 前一日最高 × 0.995 ⇒ 不发腿
  ⛔ 本版**未做**（记为待办，避免一次改太多）：
     · T3 的"当日延后到 14:00–14:30"（t_monitor 目前不认 start_time/end_time，需另开字段）
     · T6 主线响应、T7 白线在上资格线（依赖主题等权/黄白线取数，回测里缺 as-of 源）
     · T8 分批（先整笔 1 份，观察影子后再定首笔比例）

**开关（全部库内默认关 ⇒ 生产零影响；回测 pins 置 1）**
  WOLF_TREND_CHANNEL=0/1   总开关（未开 ⇒ scan() 返回空、布腿不发生）
  WOLF_TREND_SHADOW=1      影子记录（只写 jsonl，不参与任何判断）
  WOLF_TREND_N=20          突破回看窗口
  WOLF_TREND_MULT=1.005    突破幅度
  WOLF_TREND_VOL_Z=1.5     放量门槛（量能 z20）
  WOLF_TREND_STAND_DAYS=3  突破后允许的确认天数
  WOLF_TREND_STAND_HOLD=0.97  站稳要求（相对突破日收盘）
  WOLF_TREND_POS_MAX=0.8   高位否证的位置分位
  WOLF_TREND_CHASE_MAX=0.03 触发时允许高于突破位的上限（3%，自设）
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple


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


_ENV = "WOLF_TREND_CHANNEL"
_SHADOW_ENV = "WOLF_TREND_SHADOW"

STAGE_BREAK = "趋势突破"


def enabled() -> bool:
    return str(os.getenv(_ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def shadow_enabled() -> bool:
    return str(os.getenv(_SHADOW_ENV, "1")).strip().lower() in ("1", "true", "yes", "on")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def params() -> Dict[str, float]:
    return {
        "n": int(_f("WOLF_TREND_N", 20)),
        "mult": _f("WOLF_TREND_MULT", 1.005),
        "vol_z": _f("WOLF_TREND_VOL_Z", 1.5),
        "stand_days": int(_f("WOLF_TREND_STAND_DAYS", 3)),
        "stand_hold": _f("WOLF_TREND_STAND_HOLD", 0.97),
        "pos_max": _f("WOLF_TREND_POS_MAX", 0.8),
        "chase_max": _f("WOLF_TREND_CHASE_MAX", 0.03),
    }


def _ma(vals: Sequence[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(float(v) for v in vals[-n:]) / float(n)


def _z20(vols: Sequence[float], idx: int) -> Optional[float]:
    """idx 处的量能 z 分数（用其前 20 根，不含当日）。"""
    if idx < 21:
        return None
    win = [float(v) for v in vols[idx - 20:idx]]
    mu = sum(win) / len(win)
    var = sum((x - mu) ** 2 for x in win) / len(win)
    sd = var ** 0.5
    if sd <= 0:
        # 方差为 0（横盘量能恒定）⇒ 无"放量"可言，返回 0 而不是 None，
        # 否则整条 T1 会因"量能无法度量"被静默跳过（2026-09-21 单测踩到）。
        return 0.0
    return (float(vols[idx]) - mu) / sd


def prefilter_all() -> bool:
    """龙头预筛口径：**默认 0 = 旧行为**（每个概念只取前 `WOLF_TREND_CONCEPT_TOPK` 名去判 T1）。

    2026-09-25（账本 §9.101）：取日线这一步**已经覆盖全量**（漏斗：宇宙 1239 → 取到日线 1208），
    但预筛只放 **12 只**去判 T1 ⇒ 满足「带量突破」的票只要不是概念前 2 名就**永远不被看见**
    （实测：比亚迪 0305/0306/0313 满足 T1、永兴材料 2 次、天赐材料 1 次，**通道一次都没评估过它们**）。
    置 1 ⇒ **预筛只用于排序、不用于决定"谁能被看"**：全量过 stage 判定，合格者按 leader 降序取上限。
    """
    return str(os.getenv("WOLF_TREND_PREFILTER_ALL", "0")).strip().lower() in ("1", "true", "yes", "on")


def stage(closes: Sequence[float], vols: Sequence[float], p: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """趋势/突破链判定（纯函数，只用传入的序列，最后一根＝最新）。

    返回 {stage, signals, desc, level, anchor}；stage ∈ {"", "趋势突破"}（空＝无信号）。
    """
    p = p or params()
    out: Dict[str, Any] = {"stage": "", "signals": {}, "desc": "", "level": None, "anchor": None}
    try:
        c = [float(x) for x in closes]
        v = [float(x) for x in vols] if vols is not None else []
    except Exception:
        out["desc"] = "序列不可用"
        return out
    n = len(c)
    N = int(p["n"])
    if n < max(N + 6, 30):
        out["desc"] = "样本不足(%d)" % n
        return out
    stand_days = int(p["stand_days"])
    # ---- T1: 在最近 stand_days+2 根里找"放量突破"日 ----
    anchor = None
    for bk in range(n - 1, max(n - (stand_days + 2), N), -1):
        hi = max(c[bk - N:bk])
        if c[bk] >= hi * float(p["mult"]):
            z = _z20(v, bk) if v else None
            if z is not None and z >= float(p["vol_z"]):
                anchor = bk
                break
    if anchor is None:
        out["desc"] = "T1未满足: 近%d日无带量突破" % (stand_days + 2)
        return out
    out["signals"]["breakout_idx"] = anchor
    out["signals"]["breakout_close"] = round(c[anchor], 3)
    out["signals"]["breakout_z20"] = round(_z20(v, anchor) or 0.0, 2)
    out["signals"]["breakout_over_pct"] = round((c[anchor] / max(c[anchor - N:anchor]) - 1) * 100, 3)
    # ---- T2: 突破后站稳 ----
    aged = (n - 1) - anchor
    # ★ 回踩模式（账本 §9.579 ✓）：乖离门拦住后要挂"回踩腿" ⇒ 突破后允许**更长的回踩窗口** ✓
    #   否则 T2 的 stand_days(=3) 会让"突破后第 7 日才回踩"的票扫不到 ✗（301511 就是这种 ✓）
    _pb_days = stand_days
    try:
        if str(os.getenv("WOLF_TREND_PULLBACK", "0")).strip().lower() in ("1", "true", "yes", "on"):
            _pb_days = max(stand_days, int(os.getenv("WOLF_TREND_PULLBACK_DAYS", "10") or 10))
    except Exception as _e_pd:
        print("[trend_channel] 读 WOLF_TREND_PULLBACK_DAYS 失败: %s" % str(_e_pd)[:60], file=sys.stderr)
    if aged > _pb_days:
        out["desc"] = "T2未满足: 突破已过 %d 日(>%d)" % (aged, stand_days)
        return out
    tail = c[anchor:]
    hold = float(p["stand_hold"])
    if min(tail) < c[anchor] * hold:
        out["desc"] = "T2未满足: 突破后跌破突破日收盘×%.2f" % hold
        return out
    ma10 = _ma(c, 10)
    if ma10 is not None and c[-1] < ma10:
        out["desc"] = "T2未满足: 收盘跌破 MA10"
        return out
    # ---- T5: 高位放量滞涨否证（用最新一根的事实）----
    win = c[-20:] if n >= 20 else c
    lo, hi20 = min(win), max(win)
    pos = (c[-1] - lo) / (hi20 - lo) if hi20 > lo else 1.0
    z_last = _z20(v, n - 1) if v else None
    out["signals"]["pos20"] = round(pos, 3)
    out["signals"]["vol_z_last"] = round(z_last, 2) if z_last is not None else None
    if pos >= float(p["pos_max"]) and (z_last is not None and z_last >= float(p["vol_z"])):
        out["desc"] = "T5否证: 高位(分位%.2f)∧放量(z=%.2f)滞涨不追" % (pos, z_last)
        out["signals"]["veto_high_pos_vol"] = True
        return out
    out["stage"] = STAGE_BREAK
    out["level"] = round(c[anchor], 3)
    out["ma20"] = _ma(c, 20)          # ★ 乖离门用（账本 §9.578 ✓）
    out["anchor"] = anchor
    out["desc"] = "带量突破(第%d根 收%.3f, z=%.2f) + 站稳%d日" % (
        anchor - (n - 1), c[anchor], out["signals"]["breakout_z20"], aged)
    return out


def _index_ret20(day: str) -> Optional[float]:
    """上证指数近 20 日涨幅（as-of ≤ day）。数据源＝仓库里的指数日线 CSV。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        p = os.path.join(root, "data", "指数数据", "index_daily", "000001.SH.csv")
        rows = []
        with open(p, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0:
                    continue
                c = line.split(",")
                if len(c) > 4 and c[0] and c[4] and str(c[0]).replace("-", "") <= str(day):
                    try:
                        rows.append((str(c[0]).replace("-", ""), float(c[4])))
                    except Exception as _e_sil1:
                        _silent_alert("trend_channel.py:198", _e_sil1)
        rows.sort()
        if len(rows) < 21:
            return None
        return (rows[-1][1] / rows[-21][1] - 1) * 100
    except Exception:
        return None


def _v2_theme_meta(themes: Sequence[str]) -> Dict[str, Any]:
    """→ {concept2theme, catalyst(theme→0-1), fund(theme→0-1)}。全部 as-of 安全（读当天 DATA_DIR 的文件）。"""
    out: Dict[str, Any] = {"concept2theme": {}, "catalyst": {}, "fund": {}}
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    # 概念 → 主题（fusion_mainline.THEME_CONCEPTS 的反向表）
    try:
        import fusion_mainline as _fm
        for th in (themes or []):
            for cn in (_fm.THEME_CONCEPTS.get(th) or []):
                out["concept2theme"].setdefault(str(cn), str(th))
    except Exception as _e_sil2:
        _silent_alert("trend_channel.py:220", _e_sil2)
    # 政策点火 = main_line_state.catalyst（主题级，0–1）
    data = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(here)), "data"))
    try:
        st = json.load(open(os.path.join(data, "main_line_state.json"), encoding="utf-8"))
        for k, v in (st.get("catalyst") or {}).items():
            try:
                out["catalyst"][str(k)] = float(v)
            except Exception as _e_sil3:
                _silent_alert("trend_channel.py:229", _e_sil3)
    except Exception as _e_sil4:
        _silent_alert("trend_channel.py:231", _e_sil4)
    # 资金买盘 = theme_mf_daily.json 近 3 日主题净流入 → 主题间分位 0–1
    try:
        mf = json.load(open(os.path.join(data, "theme_mf_daily.json"), encoding="utf-8"))
        dts = sorted(str(d) for d in mf)[-3:]
        sums = {}
        for th in (themes or []):
            vals = [mf[d].get(th) for d in dts if isinstance(mf.get(d), dict) and mf[d].get(th) is not None]
            if vals:
                sums[str(th)] = sum(float(v) for v in vals)
        if sums:
            order = sorted(sums, key=lambda t: sums[t])
            n = len(order)
            for i, th in enumerate(order, start=1):
                out["fund"][th] = i / float(n)
    except Exception as _e_sil5:
        _silent_alert("trend_channel.py:247", _e_sil5)
    return out


def _v2_pick(domain: Sequence[Dict[str, Any]], themes: Sequence[str], day: str,
             lv2, topk: int = 2) -> List[Dict[str, Any]]:
    """v2 选股：按他的前提（个股>板块>大盘）+ 要点①（总市值前 N）+ 5 日线上做硬门，再按要素加权排序。"""
    meta = _v2_theme_meta(themes)
    idx20 = _index_ret20(day)
    by: Dict[str, List[Dict[str, Any]]] = {}
    for d in (domain or []):
        ser = d.get("_series") or ([], [], [], [])
        c, v, a, mvs = ser
        if len(c) < 21:
            continue
        d["_ret5"] = (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 and c[-6] else None
        d["_ret20"] = (c[-1] / c[-21] - 1) * 100 if c[-21] else None
        d["_close"] = c[-1]
        d["_ma5"] = sum(c[-5:]) / 5.0 if len(c) >= 5 else None
        d["_mv"] = mvs[-1] if mvs else 0.0
        th = meta["concept2theme"].get((d.get("concepts") or [""])[0])
        d["_catalyst"] = meta["catalyst"].get(th) if th else None
        d["_fund"] = meta["fund"].get(th) if th else None
        for cn in (d.get("concepts") or ["__all__"]):
            by.setdefault(str(cn), []).append(d)
    picked: Dict[str, Dict[str, Any]] = {}
    gated_out = 0
    for cn, members in by.items():
        ctx = {"concept_ret20": sum((m.get("_ret20") or 0.0) for m in members) / max(len(members), 1),
               "concept_ret5": sum((m.get("_ret5") or 0.0) for m in members) / max(len(members), 1)}
        _rows = [{"symbol": m["symbol"], "ret20": m.get("_ret20"), "ret5": m.get("_ret5"),
                  "mv": m.get("_mv"), "close": m.get("_close"), "ma5": m.get("_ma5"),
                  "catalyst": m.get("_catalyst"), "fund": m.get("_fund"),
                  "name": m.get("name") or m.get("_name"),
                  "_stage": m.get("_stage")} for m in members]
        # 2026-09-21 晚补齐：业绩 / 产业链中枢 / 估值（子开关默认关 ⇒ 默认零影响；缺数据 fail-open）
        try:
            if lv2.earn_on() or lv2.chain_on() or lv2.val_on():
                lv2.attach_fund(_rows, day)
        except Exception as _e_sil6:
            _silent_alert("trend_channel.py:287", _e_sil6)
        ranked = lv2.rank_concept(_rows, ctx, index_ret20=idx20)
        ok_rows = [r for r in ranked if r.get("_v2_gate")]
        if not ok_rows:
            gated_out += 1
        for r in ok_rows[:int(topk)]:
            cur = picked.get(r["symbol"])
            if cur is None or (r.get("_v2_score") or 0) > (cur.get("leader") or 0):
                picked[r["symbol"]] = {"symbol": r["symbol"], "leader": r.get("_v2_score"),
                                       "leader_concept": cn, "_stage": r.get("_stage"),
                                       "v2_missing": r.get("_v2_missing"), "v2_rank_in_concept": 1}
    if gated_out:
        print("[trend_channel] v2 硬门：%d/%d 个概念当日无合格龙头（他的前提：个股>板块>大盘）"
              % (gated_out, len(by)), flush=True)
    return list(picked.values())


def _pct_rank_local(vals: Sequence[float]) -> List[float]:
    """升序百分位 0–1，**并列取平均名次**（与 rotation_switch_arm._pct_rank / wolf_confirm_pick.pct_rank 同口径）。"""
    n = len(vals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: vals[i])
    rk = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            rk[order[k]] = avg_rank / n
        i = j + 1
    return rk


def concept_leaders(domain: Sequence[Dict[str, Any]], topk: int = 2,
                    rank_fn=None) -> List[Dict[str, Any]]:
    """**按子概念分组**排龙头：每个概念内用三因子分位算 leader，取组内前 topk（分类龙头/龙2）。

    语料：2026-01-16「买不到龙头买**分类龙头/龙2** 绝不后排」。
    纯函数（无 IO）⇒ 可单测。返回去重后的成员（保留其在**最优概念**里的 leader 与概念名），
    不筛形态 —— 形态由调用方按 _stage 过滤。
    """
    by: Dict[str, List[Dict[str, Any]]] = {}
    for d in (domain or []):
        for c in (d.get("concepts") or ["__all__"]):
            by.setdefault(str(c), []).append(d)
    pr = rank_fn or _pct_rank_local
    picked: Dict[str, Dict[str, Any]] = {}
    for c, members in by.items():
        if not members:
            continue
        for key in ("r60", "amt20", "lim"):
            rk = pr([(m.get(key) if m.get(key) is not None else -1e9) for m in members])
            for m, v in zip(members, rk):
                m.setdefault("_cp", {})[c + ":" + key] = v
        for m in members:
            m.setdefault("_cl", {})[c] = (m["_cp"][c + ":r60"] + m["_cp"][c + ":amt20"]
                                          + m["_cp"][c + ":lim"]) / 3.0
        cand = sorted(members, key=lambda m: -m["_cl"][c])
        if topk and topk > 0:
            cand = cand[:int(topk)]
        for m in cand:
            cur = picked.get(m["symbol"])
            if cur is None or m["_cl"][c] > cur["leader"]:
                picked[m["symbol"]] = {"symbol": m["symbol"], "leader": round(m["_cl"][c], 4),
                                       "leader_concept": c, "_stage": m.get("_stage"),
                                       "concepts": m.get("concepts")}
    return list(picked.values())


def concept_pick(domain: Sequence[Dict[str, Any]], topk: int = 0, rank_fn=None) -> List[Dict[str, Any]]:
    """topk>0 ⇒ 走分组龙头；topk=0 ⇒ 旧口径（混池分位 + leader，全量保留，供对照）。"""
    if topk and int(topk) > 0:
        return concept_leaders(domain, topk=int(topk), rank_fn=rank_fn)
    out: List[Dict[str, Any]] = []
    pr = rank_fn or _pct_rank_local
    dom = list(domain or [])
    for key in ("r60", "amt20", "lim"):
        rk = pr([(d.get(key) if d.get(key) is not None else -1e9) for d in dom])
        for d, v in zip(dom, rk):
            d[key + "_p"] = v
    for d in dom:
        d["leader"] = round((d.get("r60_p", 0) + d.get("amt20_p", 0) + d.get("lim_p", 0)) / 3.0, 4)
        out.append(d)
    return out


def expr_pullback(ma20: float, lo: float = 0.95, hi: float = 1.05) -> Dict[str, Any]:
    """**回踩买腿**表达式（账本 §9.577 的 B 形态 ✓）：

    放量突破后**乖离过大**时，不追高 ✗ ⇒ 改等它**回踩到 MA20 附近**再买 ✓。
    条件：`MA20×lo <= 现价 <= MA20×hi`（默认 ±5% ✓）∧ 均价 > 0 ✓
    语料：2025-04-15 条件6「想追进去的…**在下午 2.00-2.30** 回补」✓；
          2025-03-19「带量突破均线 **缩量回踩均线** 继续带量上涨」✓（他称顶级教科书级别 ✓）
    量化依据（§9.577 ✓）：乖离 >10% 时改"等回踩到 ≤5%"⇒ 均值 +0.23% ⇒ **+0.40%** ✓、
          中位 −0.05% ⇒ **+0.00%** ✓、左尾 −8.53% ⇒ **−8.23%** ✓（**三项同时改善** ✓）
    """
    m = round(float(ma20), 3)
    return {"and": [
        {"op": ">=", "field": "quote.current", "value": round(m * float(lo), 3)},
        {"op": "<=", "field": "quote.current", "value": round(m * float(hi), 3)},
        {"op": ">", "field": "quote.average", "value": 0},
    ]}


def expr(level: float, chase_max: Optional[float] = None,
         ma20: Optional[float] = None) -> Dict[str, Any]:
    """买腿表达式：现价 ≥ 突破位 且 不追高（≤ 突破位×(1+chase_max)）。

    语料：2026-02-12「放量突破 一口吃完上面挂单」；2026-09-03「高开不追」。chase_max 为**自设**上限。
    """
    lv = round(float(level), 3)
    cap = round(lv * (1.0 + float(chase_max if chase_max is not None else params()["chase_max"])), 3)
    # ★ 乖离门（账本 §9.577／§9.578 ✓）放在**布腿阶段**判断 ✓
    #   ⚠️ 不能在 expr 里用"价格上限"实现 ✗（会与 `现价 ≥ 突破位` 互斥 ⇒ 永不触发 ✗）
    _ = ma20   # 保留参数（调用方传 MA20 ✓，布腿侧用它 ✓）
    return {"and": [
        {"op": ">=", "field": "quote.current", "value": lv},
        {"op": "<=", "field": "quote.current", "value": cap},
        {"op": ">", "field": "quote.average", "value": 0},
    ]}


# ────────────────────────── 数据获取与扫描（回测 as-of 安全） ──────────────────────────

def _unpack(rows) -> Tuple[List[float], List[float], List[float], List[float]]:
    """行 → (closes, vols, amts, mvs)。缺 amount/total_mv 时对应表给 0（不阻断）。"""
    try:
        rows = sorted(rows, key=lambda x: str(x[1]))
        ok = [x for x in rows if len(x) > 2 and x[2] not in (None, "")]
        closes = [float(x[2]) for x in ok]
        vols = [float(x[3] or 0) for x in ok]
        amts = [float(x[4] or 0) if len(x) > 4 else 0.0 for x in ok]
        mvs = [float(x[5] or 0) if len(x) > 5 else 0.0 for x in ok]
        return closes, vols, amts, mvs
    except Exception:
        return [], [], [], []


def _pg_daily_on() -> bool:
    """生产是否用本地 PG `mkt_bars_daily` 取日线（2026-10-08 用户拍板「A」）。

    ⚠️ **回测默认关**：`BT_ASOF_FETCH`（回测 pins/快照恒设 ✓）⇒ 走原路（as-of 沙箱 / bars.sqlite / relay shim）✓
       显式 `WOLF_TREND_DAILY_PG=0|1` 可覆盖 ✓（默认：非回测 ⇒ 1 ✓）
    """
    v = os.getenv("WOLF_TREND_DAILY_PG")
    if v is not None and str(v).strip() != "":
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    return not os.getenv("BT_ASOF_FETCH")


_PG_CONN = [None]


def _pg_conn():
    """复用一条 PG 连接（每次 scan 成百上千票 ⇒ 别一票一连 ✗）。失败 ⇒ None ✓"""
    import psycopg2
    c = _PG_CONN[0]
    if c is not None:
        try:
            if c.closed == 0:
                return c
        except Exception:
            pass
    c = psycopg2.connect(os.environ.get("DATABASE_URL")
                         or "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
    _PG_CONN[0] = c
    return c


def _pg_daily(ts: str, start: str, as_of: str) -> List[Any]:
    """取 (ts_code, trade_date, close, vol, amount, total_mv)，按日期升序（与 `_unpack` 的列序一致 ✓）。

    ≤ as_of ⇒ **无未来函数** ✓（与回测 `bars.sqlite` 的 `trade_date<=as_of` 同口径 ✓）
    """
    try:
        conn = _pg_conn()
        cur = conn.cursor()
        cur.execute("SELECT ts_code, trade_date, close, vol, amount, total_mv FROM mkt_bars_daily "
                    "WHERE ts_code=%s AND trade_date>=%s AND trade_date<=%s ORDER BY trade_date",
                    (ts, str(start), str(as_of)))
        rows = list(cur.fetchall())
        cur.close()
        return rows
    except Exception as _e:
        try:
            if _PG_CONN[0] is not None:
                _PG_CONN[0].close()
        except Exception:
            pass
        _PG_CONN[0] = None
        print("[trend_channel] PG 日线取数失败(回落 relay): %s" % str(_e)[:90], flush=True)
        return []


def _daily(symbol: str, as_of: str, bars_db: Optional[str] = None) -> Tuple[List[float], List[float], List[float], List[float]]:
    """取 as-of 日线（收盘/量）。

    ⚠️ 2026-09-21 首次接入回测时踩到的坑：**回测里日线取数不走 relay** —— bt_day_legs.py 会把
      rotation_switch_arm._gz 替换成「读本地 bars.sqlite + cut」，而本模块原先用 wolf_confirm_pick.gz
      （沙箱里没被替换）⇒ 每只票都取不到 ⇒ 候选恒为 0（实测日志：宇宙=3302 取到日线=0 取不到=60）。
    取数优先级：① rotation_switch_arm._gz（与 pipeline 同源；回测=本地库+cut，生产=relay 截断）
              ② 直接读 bars_db（回测传入的 bars.sqlite；强制 trade_date <= as_of，无未来函数）
              ③ wolf_confirm_pick.gz（兜底）。
    """
    s = str(symbol)
    ts = (s[2:] + "." + s[:2]) if s[:2] in ("SH", "SZ") else s
    # ⚠️ 起点必须由 as_of 字符串推出，不能用 time.time()：回测里 bt_run_pinned.install_relay_shim 会钉住时钟，
    #    time.strftime 会返回被钉的那一天 ⇒ start == as_of ⇒ 只取到 1 根日线
    #    （2026-09-21 实测：①gz rows=1 ⇒ 全部票被判"样本不足"，候选恒为 0）。
    try:
        import datetime as _dt
        start = (_dt.datetime.strptime(str(as_of), "%Y%m%d") - _dt.timedelta(days=540)).strftime("%Y%m%d")
    except Exception:
        start = "20190101"
    rows: List[Any] = []
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    # ⓪ 离线分析快路（WOLF_TREND_LOCAL_ONLY=1 且给了 bars_db）⇒ 跳过 relay，直读本地库。
    #   目的：本地复算/回放脚本不必为每只票等一次 relay（实测 relay 每票 ~1.9s × 4,841 只 = 不可用）。
    _local_only = str(os.getenv("WOLF_TREND_LOCAL_ONLY", "0")).strip().lower() in ("1", "true", "yes", "on")
    if _local_only and bars_db and os.path.exists(bars_db):
        try:
            import sqlite3
            con = sqlite3.connect("file:%s?mode=ro" % bars_db, uri=True, timeout=10)
            rows = con.execute(
                "select ts_code, trade_date, close, vol, amount, total_mv from bars "
                "where ts_code=? and trade_date<=? order by trade_date", (ts, str(as_of))).fetchall()
            con.close()
        except Exception:
            rows = []
        return _unpack(rows)
    # ⓪′ 生产快路（2026-10-08 用户拍板「A」）：本地 PG `mkt_bars_daily` ✓
    #   为什么：原路径 ① 是**一票一次外网 relay**（实测 1.50~1.57 s/票 ⇒ 200 票 ≈ 10 分钟 ✗，
    #     而 ⑱ 块要扫两遍 ⇒ 更久 ✗）；生产 PG 里**本来就有**全市场日线
    #     （`mkt_bars_daily`：2,543,452 行 / 5,623 只 / 2024-11-01→2026-10-08 ✓，
    #      字段 close/vol/amount/total_mv 正是 `_unpack` 要的四列 ✓；`wolf_theme_vol_fund.theme_amounts` 已在读它 ✓）
    #   ⇒ 一票一次 SQL ≈ 毫秒 ✓。取不到 / 出错 ⇒ **回落** ① relay（fail-open，不改变语义 ✓）。
    if _pg_daily_on():
        rows = _pg_daily(ts, start, as_of)
        if rows:
            try:
                return _unpack(rows)
            except Exception:
                rows = []
    # ① 与 pipeline 同源
    try:
        import importlib
        _rsa = importlib.import_module("rotation_switch_arm")
        rows = _rsa._gz("daily", {"ts_code": ts, "start_date": start, "end_date": as_of},
                        "ts_code,trade_date,close,vol,amount,total_mv") or []
    except Exception:
        rows = []
    _dbg = str(os.getenv("WOLF_TREND_DEBUG", "0")).strip().lower() in ("1", "true", "yes", "on")
    _g = globals().setdefault("_DBG_N", [0])
    if _dbg and _g[0] < 3:
        _g[0] += 1
        print("[trend_daily] %s as_of=%s ①gz rows=%d type=%s" % (
            ts, as_of, len(rows), type(rows[0]).__name__ if rows else "-"), flush=True)
    # ② 直接读本地 bars.sqlite（回测兜底；按 as_of 截断）
    if not rows and bars_db and os.path.exists(bars_db):
        try:
            import sqlite3
            con = sqlite3.connect("file:%s?mode=ro" % bars_db, uri=True, timeout=10)
            rows = con.execute(
                "select ts_code, trade_date, close, vol, amount, total_mv from bars "
                "where ts_code=? and trade_date<=? order by trade_date", (ts, str(as_of))).fetchall()
            con.close()
        except Exception:
            rows = []
    # ③ 兜底
    if not rows:
        try:
            import wolf_confirm_pick as _WCP
            rows = _WCP.gz("daily", {"ts_code": ts, "start_date": start, "end_date": as_of},
                           "ts_code,trade_date,close,vol,amount,total_mv") or []
        except Exception:
            rows = []
    return _unpack(rows)


def scan_pullback(day: str, symbols: Optional[Sequence[str]] = None,
                  themes: Optional[Sequence[str]] = None, limit: int = 200,
                  bars_db: Optional[str] = None) -> List[Dict[str, Any]]:
    """**回踩候选**（账本 §9.579 ✓）：过去 N 日内有**放量突破** ∧ 现价回到 **MA20±5%** ✓

    ⚠️ 为什么不能靠 `scan()` 放宽：`scan()` 的 T2 要求"**突破后站稳**"（≥突破日收盘×0.97 ✓）
    ⇒ ⇒ **回踩本身就违反 T2** ✗（301511 0309 收 33.50 < 37.77×0.97=36.64 ✗）⇒ 必须**独立扫描** ✓
    语料：2025-03-19「带量突破均线 **缩量回踩均线** 继续带量上涨」✓；2025-04-15 条件6「2.00-2.30 回补」✓
    """
    if not enabled():
        return []
    try:
        _nd = int(os.getenv("WOLF_TREND_PULLBACK_DAYS", "10") or 10)
    except Exception as _e_nd:
        print("[trend_channel] 读 WOLF_TREND_PULLBACK_DAYS 失败: %s" % str(_e_nd)[:50], file=sys.stderr)
        _nd = 10
    p = params()
    syms: List[str] = []
    if symbols:
        syms = [str(x) for x in symbols]
    elif themes:
        try:
            import psycopg2
            import os as _os2
            dsn = _os2.environ.get("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
            cn = psycopg2.connect(dsn, connect_timeout=10)
            cn.set_session(readonly=True, autocommit=True)
            cur = cn.cursor()
            seen = set()
            for th in themes:
                for cn_ in (THEME_CONCEPTS.get(th) or []) if isinstance(globals().get("THEME_CONCEPTS"), dict) else []:
                    cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name=%s", (cn_,))
                    for (ts_,) in cur.fetchall():
                        if ts_ not in seen:
                            seen.add(ts_); syms.append(ts_)
            cn.close()
        except Exception as _e_pb:
            print("[trend_channel] 回踩扫描取池失败: %s" % str(_e_pb)[:80], file=sys.stderr)
            return []
    out: List[Dict[str, Any]] = []
    for sym in syms[:int(limit)]:
        try:
            c, v, _a, _m = _daily(sym, day, bars_db)
            n = len(c)
            if n < 30 or not v:
                continue
            ma20 = _ma(c, 20)
            if not ma20 or ma20 <= 0:
                continue
            cur_px = c[-1]
            if not (ma20 * 0.95 <= cur_px <= ma20 * 1.05):      # ① 回踩到位 ✓
                continue
            broke = False
            for k in range(1, _nd + 1):
                i = n - 1 - k
                if i - 20 < 0:
                    break
                if c[i] >= max(c[i - 20:i]) * float(p["mult"]):
                    z = _z20(v, i)
                    if z is not None and z >= float(p["vol_z"]):
                        broke = True; break
            if not broke:                                       # ② 过去 N 日内有放量突破 ✓
                continue
            out.append({"symbol": sym, "stage": "回踩", "level": round(cur_px, 3),
                        "ma20": round(ma20, 3),
                        "desc": "回踩买(MA20 %.2f, 现价 %.2f, 距MA20 %+.1f%%)"
                                % (ma20, cur_px, (cur_px / ma20 - 1) * 100)})
        except Exception as _e_sb:
            print("[trend_channel] 回踩扫描 %s 失败: %s" % (sym, str(_e_sb)[:60]), file=sys.stderr)
            continue
    return out


def scan(day: str, symbols: Optional[Sequence[str]] = None, themes: Optional[Sequence[str]] = None,
         limit: int = 200, bars_db: Optional[str] = None) -> List[Dict[str, Any]]:
    """扫描候选（默认＝主线主题的概念成分）。返回 [{symbol, theme, stage, level, desc, signals}]。

    未开总开关 ⇒ 立即返回空（生产零影响）。
    """
    if not enabled():
        return []
    syms: List[str] = []
    sym2cons: Dict[str, List[str]] = {}          # 每只票的**概念归属**（分组排龙头要用）
    if symbols:
        syms = [str(s) for s in symbols]
    else:
        try:
            import sqlite3
            data = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"))
            db = os.path.join(data, "stock_pool.db")
            con = sqlite3.connect(db)
            cur = con.cursor()
            if themes:
                for th in themes:
                    cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name=?", (th,))
                    for r in cur.fetchall():
                        _c = str(r[0])
                        if _c not in sym2cons:      # ⚠️ 必须去重：同一只票属于多个概念
                            syms.append(_c)         #   重复会把 syms[:limit] 的额度吃掉，
                        sym2cons.setdefault(_c, []).append(str(th))   # 导致靠后的概念整段被截掉
            con.close()
        except Exception:
            syms = []
    # ── 卫生过滤前置（用户 2026-09-21「过滤里去掉退市，ST，北交所的票」）──
    #   必须在**取日线之前**：syms[:limit] 是在未过滤名单上截断的，脏票会白占额度
    #   （实测 0108：账户权限过滤 385→126，2/3 的额度被"取完才发现不能用"的票浪费掉）。
    #   as-of 名称走 namechange 重建，不用今天的名字判历史（见 universe_clean 模块头）。
    try:
        import importlib as _ilUC
        _uc = _ilUC.import_module("universe_clean")
        if _uc.enabled():
            _before = len(syms)
            syms, _dropped = _uc.filter_symbols(syms, day=day)
            if _dropped or str(os.getenv("WOLF_TREND_DEBUG", "0")).strip().lower() in ("1", "true", "yes", "on"):
                print("[trend_channel] 卫生过滤 %d → %d（剔除 %s）"
                      % (_before, len(syms), _dropped or {}), flush=True)
    except Exception as _e:
        _silent_alert("trend_channel.py:524", _e)
    domain: List[Dict[str, Any]] = []
    seen = set()
    _dbg = str(os.getenv("WOLF_TREND_DEBUG", "0")).strip().lower() in ("1", "true", "yes", "on")
    _n_of = _n_short = 0
    try:
        import importlib as _il0
        _rsa0 = _il0.import_module("rotation_switch_arm")
    except Exception:
        _rsa0 = None
    # limit <= 0 ⇒ **扫全部**（2026-09-21 用户"过滤去掉脏票"延伸）：原先固定 400 只，
    # 而 0108 概念成分就有 859 只 ⇒ 只扫了 46%，且截断顺序是 DB 行序（谁被扫到是随机的）。
    # 实测每日全量扫描 ~2s（本地 bars），比"漏掉一半候选"便宜得多。
    if limit and limit > 0:
        syms = syms[:limit]
    for ts in syms:
        s = str(ts)
        if s in seen:
            continue
        seen.add(s)
        xq = ("SH" + s[:6]) if s.endswith(".SH") else (("SZ" + s[:6]) if s.endswith(".SZ") else s)
        closes, vols, amts, mvs = _daily(xq, day, bars_db)
        if len(closes) < 61:
            _n_short += 1
            continue
        _n_of += 1
        r = stage(closes, vols)
        # ── 龙头三因子（**与 rotation_switch_arm.pick_buy 完全同口径**）──
        #   r60 = 近 60 日涨幅；amt20 = 20 日成交额均值；lim = 近 60 日涨幅≥9.7% 的天数
        try:
            r60 = (closes[-1] / closes[-61] - 1) * 100 if closes[-61] else None
        except Exception:
            r60 = None
        amt20 = (sum(amts[-20:]) / 20.0) if len(amts) >= 20 else None
        lim = sum(1 for i in range(max(1, len(closes) - 60), len(closes))
                  if closes[i - 1] and closes[i] / closes[i - 1] - 1 >= 0.097)
        domain.append({"symbol": xq, "ts": s, "concepts": list(sym2cons.get(s) or ["__all__"]),
                       "r60": r60, "amt20": amt20, "lim": lim, "_stage": r,
                       "_series": (closes, vols, amts, mvs)})
    # ⚠️ 关键顺序（2026-09-21 实测踩到）：**账户权限过滤必须在组内排名之前**。
    #   否则不可交易的 300/688 会占掉每个概念的"分类龙头/龙2"名额，之后才被 board 过滤掉 ⇒
    #   可交易的细分龙头永远排不进前 2（实测：兆易创新在"汽车芯片"组从第 2 掉到第 3+，长电同理）。
    #   与既有口径一致：F2「选股域 ⊆ 执行域」。
    try:
        import importlib as _ilB
        _rsaB = _ilB.import_module("rotation_switch_arm")
        _before = len(domain)
        domain = [d for d in domain if _rsaB.board_ok(d["symbol"])]
        if _dbg:
            print("[trend_channel] 账户权限前置过滤 %d → %d" % (_before, len(domain)), flush=True)
    except Exception as _bfe:
        # 2026-09-25：原来这里 `pass` ⇒ **权限过滤静默失效**（离线复现时 import 不到
        #   `jobs/rotation_switch_arm` 就整段跳过，无权限票照样进龙头排序 ✗）⇒ 改成打印警告。
        print("[trend_channel] ⚠️ 账户权限前置过滤**未生效**（排除 %d 只的步骤被跳过）: %s"
              % (len(domain), str(_bfe)[:80]), flush=True)
    # ── 「各概念排龙头」而不是"37 个概念混在一起排"（2026-09-21 用户拍板）──
    #   语料：2026-01-16「买不到龙头买**分类龙头/龙2**」；路径 B 本来就是"子概念分组 → 组内前 2"。
    #   混池排名的后果（实测 cut=20260108）：前 8 名全是 r60 +86%~+373%、涨停 6~14 个的妖股，
    #   而封测龙头长电科技（r60 −4.4%）排到 463/885 —— 用"整主题一把尺"把细分概念龙头挤出门外。
    topk = int(_f("WOLF_TREND_CONCEPT_TOPK", 2))          # 分类龙头 + 龙2
    # ── v2 优先（2026-09-21 用户拍板"向他对齐"）：WOLF_LEADER_V2=1 时用 leader_v2 ──
    #   ★ 2026-10-09：这里**打印一次实际生效的尺子**，并且当"子开关已开但本体未开"时**醒目告警** ✓
    #     （修静默失效：t3→t36 的臂只设了子开关 ⇒ 实际一直走 v1 却从不报错 ✗；行为语义未改 ✓）
    try:
        import importlib as _ilV
        _lmod = _ilV.import_module("leader_v2")
        _lmod.warn_if_misconfigured()
        _lv2 = _lmod if _lmod.enabled() else None
    except Exception as _eLV:
        _lv2 = None
        print("[trend_channel] leader_v2 不可用（按 v1 走）: %s" % str(_eLV)[:80], flush=True)
    if _lv2 is not None:
        kept = _v2_pick(domain, themes, day, _lv2, topk=topk)
    mode = str(os.getenv("WOLF_TREND_LEADER_MODE", "concept")).strip().lower()
    if _lv2 is None:
        kept = []
    _keep_all = prefilter_all()      # 2026-09-25：预筛只用于排序（默认关 = 旧行为逐字不变）
    if _lv2 is None and (mode == "global" or _keep_all) and _rsa0 is not None and hasattr(_rsa0, "_pct_rank"):
        _pr = _rsa0._pct_rank
        for key in ("r60", "amt20", "lim"):
            _rk = _pr([(d.get(key) if d.get(key) is not None else -1e9) for d in domain])
            for d, v in zip(domain, _rk):
                d[key + "_p"] = v
        for d in domain:
            d["leader"] = round((d["r60_p"] + d["amt20_p"] + d["lim_p"]) / 3.0, 4)
        kept = concept_pick(domain, topk=0)               # 0 = 不分组，全部保留（旧口径）
    elif _lv2 is None:
        kept = concept_leaders(domain, topk=topk, rank_fn=getattr(_rsa0, "_pct_rank", None))
    out: List[Dict[str, Any]] = []
    for d in kept:
        r = d.get("_stage") or {}
        if r.get("stage") == STAGE_BREAK:
            out.append({"symbol": d["symbol"], "stage": r["stage"], "level": r["level"],
                        "ma20": r.get("ma20"),
                        "desc": r["desc"], "signals": r.get("signals"),
                        "leader": d.get("leader"), "leader_concept": d.get("leader_concept")})
    # ① 账户权限前置过滤（300/301/688/北交所）：必须在 cap 之前，否则幻影腿会占掉名额。
    try:
        import importlib as _il
        _rsa = _il.import_module("rotation_switch_arm")
        out = [r for r in out if _rsa.board_ok(r["symbol"])]
    except Exception as _e_sil8:
        _silent_alert("trend_channel.py:622", _e_sil8)
    # ①′ 卫生过滤兜底（ST/退市/北交所）：前置那道若因异常没生效，这里再拦一次。
    try:
        import importlib as _il2
        _uc2 = _il2.import_module("universe_clean")
        if _uc2.enabled():
            out = [r for r in out if _uc2.judge(r["symbol"], day=day)[0]]
    except Exception as _e_sil9:
        _silent_alert("trend_channel.py:630", _e_sil9)
    # ② 每日上限：候选常有上百（全市场带量突破），必须收敛到可执行的少数。
    #    排序口径（自设，可配 WOLF_TREND_ORDER）：
    #      break（默认）= 突破幅度**升序** —— 刚站上前高、还没走远的优先，对应语料「不追高」；
    #      z            = 突破日量能 z 降序（放量最猛优先）——会被一字板/异动票占满，不推荐。
    _cap = int(_f("WOLF_TREND_MAX_LEGS", 8))
    # 龙头优先（leader 降序）——「只看龙头/前排」（2026-01-16「后排反倒不能去 要看好龙头那些」）。
    # WOLF_TREND_ORDER=z / break 可切回旧口径（仅用于对照，不建议）。
    _order = str(os.getenv("WOLF_TREND_ORDER", "leader")).strip().lower()
    if _order == "z":
        out.sort(key=lambda r: -(float((r.get("signals") or {}).get("breakout_z20") or 0)))
    elif _order == "break":
        out.sort(key=lambda r: float((r.get("signals") or {}).get("breakout_over_pct") or 0))
    else:
        out.sort(key=lambda r: -(float(r.get("leader") or 0)))
    if _cap > 0 and len(out) > _cap:
        out = out[:_cap]
    if _dbg:
        print("[trend_channel] day=%s themes=%s 宇宙=%d 取到日线=%d 太短/取不到=%d 命中=%d 上限=%d"
              % (day, len(themes or []), len(syms), _n_of, _n_short, len(out), _cap), flush=True)
    return out


_FALLBACK_CONCEPTS = ("人工智能", "算力概念", "CPO概念", "光通信模块", "液冷概念", "存储芯片")


def theme_concepts(themes: Optional[Sequence[str]] = None) -> List[str]:
    """主线主题 → 概念名列表（复用 fusion_mainline.THEME_CONCEPTS；取不到用兜底表）。

    themes=None ⇒ 读 main_line_state.json 的 main_line（与 stock_confirm_judge 同源）。
    找不到 fusion_mainline（如测试环境）⇒ 返回兜底概念，保证 scan 仍可用。
    """
    names: List[str] = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import fusion_mainline as _fm  # type: ignore
        th = list(themes) if themes else []
        if not th:
            data = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(here)), "data"))
            try:
                st = json.load(open(os.path.join(data, "main_line_state.json"), encoding="utf-8"))
                _ml = str(st.get("main_line") or "AI/算力/科技")
                # ⚠️ 必须取 **TOP2**（与 pipeline 的资格集合 mainline_today = TOP1∪TOP2 对齐）：
                #    只取 TOP1 会漏掉当日次主线 —— 2026-01 的 TOP1=AI/算力/科技 会把半导体概念整条排除，
                #    兆易创新/长电科技这类"次主线里的突破票"就永远进不了候选（2026-09-21 实测）。
                _rest = [k for k, _ in sorted((st.get("fusion") or {}).items(),
                                              key=lambda kv: -((kv[1] or {}).get("score") or 0)) if k != _ml][:1]
                th = [_ml] + _rest
            except Exception:
                th = ["AI/算力/科技"]
        for t in th:
            names += [str(x) for x in (_fm.THEME_CONCEPTS.get(t) or [])]
    except Exception:
        names = []
    if not names:
        names = list(_FALLBACK_CONCEPTS)
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def scan_mainline(day: str, limit: int = 400, bars_db: Optional[str] = None) -> List[Dict[str, Any]]:
    """按主线主题的概念成分扫描（**绕过 stage 白名单** —— 这正是本通道的意义）。"""
    if not enabled():
        return []
    return scan(day, themes=theme_concepts(), limit=limit, bars_db=bars_db)


def shadow_record(day: str, rows: Sequence[Dict[str, Any]], extra: Optional[Dict[str, Any]] = None) -> None:
    """影子落盘（只记录，不参与判断）。文件：data/trend_channel_shadow.jsonl"""
    if not shadow_enabled():
        return
    try:
        data = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"))
        p = os.path.join(data, "trend_channel_shadow.jsonl")
        with open(p, "a", encoding="utf-8") as f:
            rec = {"date": day, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "n": len(rows),
                   "rows": [{"symbol": r.get("symbol"), "level": r.get("level"), "desc": r.get("desc")} for r in rows]}
            if extra:
                rec.update(extra)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as _e_sil10:
        _silent_alert("trend_channel.py:717", _e_sil10)
