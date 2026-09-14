# -*- coding: utf-8 -*-
"""wolf_pick_rank_v3.py — 买入侧「条件化分层排序」v3（2026-09-15）。

**为什么要有它**：现行 leader = `r60 + 成交额 + 涨停次数` 的分位均值，实测与未来同主题超额**负相关**
（IC −0.048~−0.087，块状 t −1.8~−2.5；见 `docs/wolf-pick-selection-eval.md`）。而 B1 原话取证
（191 条 / 191 逐字可核，见 `docs/wolf-buy-b1-identifiability.md`）显示他的"辨识度"**不是一个因子**，
而是「**分层 + 阶段依赖**」：选哪一层随板块阶段变。

**本模块只做排序（不改任何过滤/闸门）**，且是**单一实现**：离线验收（`jobs/eval_pick_rank_v3.py`）
与生产（`wolf_confirm_pick.pick_v2` 里的 `WOLF_PICK_RANK_V3=1`）共用同一函数，
避免本项目反复踩过的"两条路各一套口径"。

## 打分构成（每一项都带证据；权重等权、不做拟合）

| 项 | 方向 | 证据 |
|---|---|---|
| `pos == LOW` +1.0 | 低位优先 | 分档实测 LOW −0.132% vs MID −0.484%（5 日同主题超额） |
| `dist_prevlow ≤ 2.5%` +1.0 | 贴近前低 | 分档 −0.039% vs >5% −0.551%；IC −0.019/−0.032（"距前低越近越好"）|
| `flat_low_days` z +0.8 | 低位横盘多时 | 2026-04-07「低位横盘多时的就是好 超跌都没有低位走平多时的好」；实测 IC +0.009/+0.018（两窗口同向为正）|
| `hist` z +0.5 | 老票 | 实测 IC +0.006/+0.004（池内 +0.026）；"老龙头"的字面含义 |
| `mf_dip_in` +0.8 | **跌∧资金净流入** | 2021-08-04「跌多了预判资金去配置才是买的理由」；实测 +0.045%/5 日、+0.173%/20 日 vs 跌∧流出 −0.130%/−0.395%（t −2.08）|
| `not mf_out5` +0.4 | 资金没有 5 日连续流出 | 2025-06-16「资金没有 5 日连续流出的」 |
| `vol_ratio5 ≤ 1.0` +0.4 | 缩量 | 2026-08-04「缩量不参与」/ 缩量低吸；实测 IC −0.013（放量→负超额）|
| 阶段依赖（V3） | 强主题跳第 1 名 / 弱主题偏好抗跌 | 2026-04-13「已经涨起来的板块的龙头不做 做他的二供」；2026-04-19「弱势板块找强势票，强势板块找二线补涨」；2021-02-18「抱团方向的龙2龙3 + 超跌板块的龙1」|

## 明确**不**进打分（实测负向，见 §"被否定的项"）
`r60` / `lim60`（涨停次数）/ `amt20`（成交额分位）/ `lead_days*`（曾领涨）/ `vol_active5`（量能活跃——
他明说是"**选板块**的第一要素"，个股级实测负）/ `name_hit`（名字辨识度，泛化后≈0）。

## 变体（用于消融）
V1 = 位置+结构（pos/dist_prevlow/flat_low_days/hist）
V2 = V1 + 资金（mf_dip_in / not mf_out5 / 缩量）
V3 = V2 + 阶段依赖（强→跳第 1 名；弱→偏好抗跌）
开关：`variant` 参数（离线）/ env `WOLF_PICK_RANK_V3_VARIANT`（生产）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# **默认 = 已验收配置**（2026-09-15 第一版离线验收，见 docs/wolf-pick-rank-v3-eval.md）：
#   域 = 候选池 ∩ LOW/MID（**去掉 r20≥0 与 rs≥0 两个负贡献闸**）
#   排序 = LOW 优先；并列用「低位横盘多时」(flat_low_days) 次键
#   → 5 日同主题超额 −0.198%（现行 −0.982%，Δ +0.72pp，配对块状 t 2.07；H1/H2 两段都不劣）
VARIANT = "V1"
DEFAULT_COMPONENTS = "pos"
DEFAULT_TIEBREAK = "flat"
# v2 新增（2026-09-15 第二版离线验收，见 docs/wolf-pick-rank-v3-eval.md §6）：
#   stage_mode = "qtile"：主题「强/弱」用**跨主题 r5 分位**判定（≥0.5 强、≤0.5 弱；分位由调用方 PIT 计算）
#   diergong   = True  ：强主题按他「已经涨起来的板块的龙头不做 做他的二供」(2026-04-13)
#                        → 跳过该主题候选里 r20 最高的一只
DEFAULT_STAGE = "qtile"
DEFAULT_DIERGONG = True
QTILE_HI = 0.5      # v2 验收最优（0.5/0.5）；0.66/0.33 亦达标（+0.157%）
QTILE_LO = 0.5      # 阈值敏感性小，取对称中线=最简可解释

# 强/弱主题阈值（我们的代理）：主题近 5 日等权涨幅（%）
STRONG_THEME_R5 = 0.5
WEAK_THEME_R5 = -0.5


def _z(vals: List[Optional[float]]) -> List[float]:
    """z 标准化（缺值 → 0；标准差 0 → 全 0）。"""
    xs = [float(v) for v in vals if v is not None and v == v]
    if not xs:
        return [0.0] * len(vals)
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / max(1, len(xs) - 1)
    sd = var ** 0.5
    out = []
    for v in vals:
        if v is None or v != v:
            out.append(0.0)
        else:
            out.append(0.0 if sd == 0 else (float(v) - m) / sd)
    return out


ALL_COMPONENTS = ("pos", "dist", "flat", "hist", "mf", "squeeze", "stage")


def _tie_key(r: Dict[str, Any], tb: str):
    """并列时的次级排序键（**不能用代码序**——那正是我们批判过的 legacy 扫描序）。

    tb: ts（默认，仅作最终稳定序）/ dist（贴近前低）/ flat（低位横盘多时）/ rs（相对抗跌）/ amt（成交额）
    """
    if tb == "dist":
        v = r.get("dist_prevlow")
        return (0, float(v)) if v is not None and v == v else (1, 0.0)
    if tb == "flat":
        v = r.get("flat_low_days") or 0.0
        return (-float(v),)
    if tb == "rs":
        v = r.get("rs")
        return (0, -float(v)) if v is not None and v == v else (1, 0.0)
    if tb == "amt":
        v = r.get("amt20")
        return (0, -float(v)) if v is not None and v == v else (1, 0.0)
    return (str(r.get("ts") or ""),)


def rank_rows(rows: List[Dict[str, Any]], theme_r5: Optional[float] = None,
              variant: str = None, components: str = None, tiebreak: str = None,
              theme_r5_qtile: Optional[float] = None, stage_mode: str = None,
              diergong: bool = None, qtile_hi: float = None, qtile_lo: float = None) -> List[Dict[str, Any]]:
    """对候选 rows（同一主题同一日）打分并降序返回；每条附 `v3_score` 与 `v3_reasons`。

    rows 需要的字段（缺值按 None 处理，不报错）：
      ts/name, pos(LOW/MID/HIGH), dist_prevlow, r20, rs, flat_low_days, hist,
      mf1（当日主力净流入，万元）, mf5（近5日累计）, vol_ratio5, pct_today, rank_in_theme（0–1，1=最强）
    """
    v = (variant or os.getenv("WOLF_PICK_RANK_V3_VARIANT", VARIANT) or VARIANT).strip().upper()
    sm = (stage_mode or os.getenv("WOLF_PICK_RANK_V3_STAGE", DEFAULT_STAGE) or DEFAULT_STAGE).strip().lower()
    dg = DEFAULT_DIERGONG if diergong is None else bool(diergong)
    if os.getenv("WOLF_PICK_RANK_V3_DIERGONG") is not None and diergong is None:
        dg = os.getenv("WOLF_PICK_RANK_V3_DIERGONG", "1").strip() not in ("0", "false", "no", "")
    hi = QTILE_HI if qtile_hi is None else float(qtile_hi)
    lo = QTILE_LO if qtile_lo is None else float(qtile_lo)
    strong_q = bool(theme_r5_qtile is not None and sm == "qtile" and theme_r5_qtile >= hi)
    weak_q = bool(theme_r5_qtile is not None and sm == "qtile" and theme_r5_qtile <= lo)
    comp = tuple(x.strip() for x in (components or os.getenv("WOLF_PICK_RANK_V3_COMPONENTS", DEFAULT_COMPONENTS)
                                     or "").split(",") if x.strip()) or ALL_COMPONENTS
    n = len(rows)
    if n == 0:
        return []
    z_flat = _z([r.get("flat_low_days") for r in rows])
    z_hist = _z([r.get("hist") for r in rows])
    z_rs = _z([r.get("rs") for r in rows])
    strong = theme_r5 is not None and theme_r5 > STRONG_THEME_R5
    weak = theme_r5 is not None and theme_r5 < WEAK_THEME_R5
    # 「二供」：强主题先剔除候选里 r20 最高的一只（= 已经在涨的龙头/大哥）
    if dg and strong_q and len(rows) > 1:
        top = max(rows, key=lambda r: (r.get("r20") if r.get("r20") is not None else -1e9))
        rows = [r for r in rows if r is not top]
        z_flat = _z([r.get("flat_low_days") for r in rows])
        z_hist = _z([r.get("hist") for r in rows])
        z_rs = _z([r.get("rs") for r in rows])
        n = len(rows)
    out = []
    for k, r in enumerate(rows):
        s, why = 0.0, []
        if "pos" in comp and str(r.get("pos")) == "LOW":
            s += 1.0
            why.append("低位(LOW)")
        dp = r.get("dist_prevlow")
        if "dist" in comp and dp is not None and dp == dp and dp <= 2.5:
            s += 1.0
            why.append("贴前低")
        if "flat" in comp:
            s += 0.8 * z_flat[k]
            why.append("横盘%.1f" % (r.get("flat_low_days") or 0))
        if "hist" in comp:
            s += 0.5 * z_hist[k]
        if v in ("V2", "V3") and ("mf" in comp or "squeeze" in comp):
            mf1, mf5, pt = r.get("mf1"), r.get("mf5"), r.get("pct_today")
            dip_in = (mf1 is not None and mf1 == mf1 and mf1 > 0
                      and pt is not None and pt == pt and pt < 0)
            if dip_in and "mf" in comp:
                s += 0.8
                why.append("跌+资金流入")
            if "mf" in comp and mf5 is not None and mf5 == mf5 and mf5 > 0:
                s += 0.4
                why.append("5日资金非净流出")
            vr = r.get("vol_ratio5")
            if "squeeze" in comp and vr is not None and vr == vr and vr <= 1.0:
                s += 0.4
                why.append("缩量")
        if (v == "V3" or sm == "qtile") and "stage" in comp and not dg:
            rk = r.get("rank_in_theme")
            # 强主题：不追"已经涨起来的龙头"（2026-04-13「龙头不做 做他的二供」）
            if strong and rk is not None and rk >= 0.98:
                s -= 1.0
                why.append("强主题跳第1名")
            # 弱主题：找抗跌的强势票（2026-04-19「弱势板块找强势票」）
            if weak or weak_q:
                s += 0.6 * z_rs[k]
                why.append("弱主题偏好抗跌")
        out.append(dict(r, v3_score=round(s, 4), v3_reasons="|".join(why)))
    tb = (tiebreak or os.getenv("WOLF_PICK_RANK_V3_TIEBREAK", DEFAULT_TIEBREAK) or DEFAULT_TIEBREAK).strip().lower()
    out.sort(key=lambda x: (-x["v3_score"],) + tuple(_tie_key(x, tb)))
    return out


def pick_top(rows: List[Dict[str, Any]], theme_r5: Optional[float] = None,
             n: int = 1, variant: str = None, components: str = None,
             tiebreak: str = None, theme_r5_qtile: Optional[float] = None,
             stage_mode: str = None, diergong: bool = None,
             qtile_hi: float = None, qtile_lo: float = None) -> List[Dict[str, Any]]:
    """取 v3 排序前 n（生产用入口；v2 验收配置 n=1）。

    `components` 用于消融（如 "pos,dist"）；`tiebreak` 见 `_tie_key`；
    `theme_r5_qtile` = 该主题 r5 在**当日各主题间的分位**（PIT，调用方算），用于 stage=qtile。
    """
    return rank_rows(rows, theme_r5=theme_r5, variant=variant, components=components,
                     tiebreak=tiebreak, theme_r5_qtile=theme_r5_qtile,
                     stage_mode=stage_mode, diergong=diergong,
                     qtile_hi=qtile_hi, qtile_lo=qtile_lo)[:max(0, int(n))]
