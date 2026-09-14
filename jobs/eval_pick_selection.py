# -*- coding: utf-8 -*-
"""eval_pick_selection.py — 买入侧「选择层」（confirm_pick v2.1 / pick_v2）离线验收。

**回答的问题**：pick_v2 到底有没有把"好票"排到前面？（选择层有没有产生超额，还是只买到主题 beta）

口径（与 `docs/leg-metrics-spec.md` 一致，两把尺子同时报）：
  · 主口径（规则模拟，可回放）：入场 = **次日(D+1)收盘**，出场 = D+6 收盘（持 5 个交易日）；
    超额 = 个股收益 − **同主题同日等权篮子**（同窗口）。
  · 副口径（买点真实化）：254 回踩挂单 = 触发价 `low(D)×1.005`，D+1 盘中 `low(D+1) ≤ 触发价` 即成交，
    出场 D+6 收盘；同时报成交率（挂单没成交 = 没买到，这也是成本）。
  · 分主题 / 分位置档(LOW/MID/HIGH) / 分段(H1/H2)；报 胜率、均值、中位、同主题超额、**块状 t**（ISO 周）。
  · **n<100 只作探索，不得作为验收依据**（plan §9 L8）。

被验收的对象（全部按 `wolf_confirm_pick.pick_v2` 的现行规则复刻，见 `_pick_rules_doc`）：
  L2 过滤（板块/ST/成交额≥1亿） → leader 三因子等权分位（r60/amt20/lim，并列取平均名次）
  → 组内前2（rank_in_concept）→ 主题容量分位 top50% → 位置闸(LOW/MID ∧ r20≥0 ∧ rs≥0)
  → tier1(距前日低≤5%) 前 limit / tier2(≤8%) 补位至 max_legs；风向标收破前日低 0.5% → 硬拦清空。

对照臂：同主题等权篮子（beta）、池内随机（蒙特卡洛）、**原 DB 扫描序**（legacy confirm_pick）、
等待池 top6（含高位）、leader 全榜 top5、仅过位置闸的全部（lowmid_all）。

用法：
  .venv/bin/python jobs/eval_pick_selection.py                       # 全部 13 主题 × 全区间
  .venv/bin/python jobs/eval_pick_selection.py --pool-only           # 只在方向层池内主题上跑（产量口径）
  .venv/bin/python jobs/eval_pick_selection.py --start 20260105 --end 20260904
  .venv/bin/python jobs/eval_pick_selection.py --trades              # 额外核对生产实际成交腿
产出：`.dsh-tmp/buyside/eval_pick_selection.json`（+ `--md` 打印 markdown 表）
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))
sys.path.insert(0, os.path.join(ROOT, "jobs"))

BARS = os.path.join(ROOT, ".dsh-tmp", "buyside", "bars.parquet")
UNI_DB = os.path.join(ROOT, ".dsh-tmp", "buyside", "prod_data", "stock_pool.db")
OUT = os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_pick_selection.json")

# pick_v2 的现行参数（默认值，见 apps/main_line/wolf_confirm_pick.py）
POOL_N = 6            # 等待池宽度 WOLF_PICK_POOL_N
LIMIT = 2             # tier1 名额（rotation_switch_arm 传 limit=pool_legs-got，单主题典型 2）
MAX_LEGS = 4          # tier1+tier2 上限 WOLF_PICK_MAX_LEGS
DIST_PCT = 5.0        # 位置闸 WOLF_PICK_DIST_PCT
TIER2_GAP = 8.0       # WOLF_PICK_TIER2_GAP
MIN_AMT20_YI = 1.0    # 20 日均成交额下限（亿）
QUANTILE_PCT = 50.0   # U9 主题容量分位
MIN_R20 = 0.0         # WOLF_PICK_MIN_R20
RS_MIN = 0.0          # WOLF_PICK_RS_MIN
WIND_BREAK = -0.5     # 风向标"死"：收盘 vs 前一交易日低 ≤ -0.5%
HOLD = 5              # 前瞻窗口（交易日，可用 --hold 覆盖）
LIMITUP = 9.7         # 涨停判定阈值（%）
MC_DRAWS = 200        # 池内随机对照的蒙特卡洛次数

POS_CFG = {"low_vh": -30.0, "low_box": 35.0, "high_vh": 10.0, "high_box_hi": 80.0, "high_vh_floor": 20.0}


# ─────────────────────────── 数据层 ───────────────────────────
class Panel:
    """mkt_bars_daily → 矩阵面板（行=交易日升序，列=代码升序）。"""

    def __init__(self, path=BARS):
        df = pd.read_parquet(path)
        self.dates = sorted(df["trade_date"].unique())
        self.codes = sorted(df["ts_code"].unique())
        self.di = {d: i for i, d in enumerate(self.dates)}
        self.ci = {c: i for i, c in enumerate(self.codes)}
        T, N = len(self.dates), len(self.codes)
        self.T, self.N = T, N
        shape = (T, N)
        self.close = np.full(shape, np.nan)
        self.low = np.full(shape, np.nan)
        self.amt = np.full(shape, np.nan)
        self.pct = np.full(shape, np.nan)
        ri = df["trade_date"].map(self.di).to_numpy()
        ci = df["ts_code"].map(self.ci).to_numpy()
        self.close[ri, ci] = pd.to_numeric(df["close"], errors="coerce").to_numpy()
        self.low[ri, ci] = pd.to_numeric(df["low"], errors="coerce").to_numpy()
        self.amt[ri, ci] = pd.to_numeric(df["amount"], errors="coerce").to_numpy()
        self.pct[ri, ci] = pd.to_numeric(df["pct_chg"], errors="coerce").to_numpy()
        # ST 标记：任一来源（日线 is_st 或 stock_pool.is_st）命中即计入
        st = np.zeros(shape, dtype=bool)
        st[ri, ci] = df["is_st"].fillna(False).to_numpy()
        try:
            c = sqlite3.connect(UNI_DB)
            st_codes = {r[0] for r in c.execute("SELECT ts_code FROM stock_pool WHERE is_st=1")}
            c.close()
            for ts in st_codes:
                j = self.ci.get(ts)
                if j is not None:
                    st[:, j] = True
        except Exception as e:  # pragma: no cover
            print("[panel] is_st 读取失败 %s" % e, file=sys.stderr)
        self.st = st
        self.valid = ~np.isnan(self.close)

    def fwd_ret(self, i, hold=HOLD, entry_off=1):
        """入场 = 第 i+entry_off 天收盘；出场 = 第 i+entry_off+hold 天收盘。"""
        a, b = i + entry_off, i + entry_off + hold
        if b >= self.T:
            return None
        r = self.close[b] / self.close[a] - 1.0
        return r * 100.0

    def fwd_ret_254(self, i, hold=HOLD):
        """254 回踩挂单：触发价 = low(D)×1.005；D+1 盘中触及则按触发价成交，出场 D+1+hold 收盘。

        返回 (return%, filled)；未触及 → (None, False)。
        """
        a, b = i + 1, i + 1 + hold
        if b >= self.T:
            return None, False
        trig = self.low[i] * 1.005
        hit = self.low[a] <= trig
        r = np.where(hit, self.close[b] / np.where(hit, trig, np.nan) - 1.0, np.nan) * 100.0
        return r, hit


def load_universe():
    """主题成分：复用方向层 load_universe()（生产 stock_pool.db 副本，概念关键词展开）。"""
    from app.services import wolf_mainline_select as MS
    os.environ.setdefault("WOLF_MS_UNIVERSE_DB", UNI_DB)
    os.environ.setdefault("DATA_DIR", os.path.dirname(UNI_DB))
    uni, lead, allc = MS.load_universe()
    return uni, lead, allc, MS


def theme_concept_sets():
    """主题 → 概念名集合（与 load_universe 的 LIKE 展开同口径），以及 ts → 概念列表。"""
    from app.services.wolf_mainline_select import THEME_CONCEPT_KW
    c = sqlite3.connect(UNI_DB)
    rows = list(c.execute("SELECT DISTINCT concept_name FROM stock_concept_map"))
    all_cons = [str(r[0]) for r in rows]
    cmap = collections.defaultdict(list)
    for ts, cn in c.execute("SELECT ts_code, concept_name FROM stock_concept_map"):
        cmap[str(ts)].append(str(cn))
    c.close()
    th_cons = {}
    for th, kws in THEME_CONCEPT_KW.items():
        th_cons[th] = {cn for cn in all_cons if any(k in cn for k in kws)}
    return th_cons, {k: sorted(set(v)) for k, v in cmap.items()}


def legacy_scan_order(theme, th_cons=None, concepts=None):
    """原（legacy）confirm_pick 的候选域扫描序 = `SELECT DISTINCT ts_code WHERE concept_name IN (...)`
    的返回顺序（无 ORDER BY，实测即表内 rowid 序）。概念表取 `fusion_mainline.THEME_CONCEPTS[theme]`。"""
    cons = list(concepts or sorted((th_cons or {}).get(theme) or []))
    if not cons:
        return []
    c = sqlite3.connect(UNI_DB)
    ph = ",".join("?" * len(cons))
    rows = list(c.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name IN (%s)" % ph, cons))
    c.close()
    return [str(r[0]) for r in rows]


# ─────────────────────────── 因子/位置（与生产同口径的向量化复刻） ───────────────────────────
def _nan_pct_rank(v):
    """升序 → 百分位 0-1，**并列取平均名次**（与 wolf_confirm_pick.pct_rank 同口径）。"""
    n = len(v)
    if n == 0:
        return np.zeros(0)
    order = np.argsort(v, kind="mergesort")
    sv = v[order]
    rk = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sv[j + 1] == sv[i]:
            j += 1
        rk[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return rk / n


def _rank_avg(x):
    """平均名次（1-based），用于 Spearman。"""
    n = len(x)
    order = np.argsort(x, kind="mergesort")
    sv = x[order]
    rk = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sv[j + 1] == sv[i]:
            j += 1
        rk[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return rk


def spearman(x, y):
    if len(x) < 5:
        return None
    rx, ry = _rank_avg(x), _rank_avg(y)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    d = math.sqrt(float((rx * rx).sum()) * float((ry * ry).sum()))
    if d <= 0:
        return None
    return float((rx * ry).sum() / d)


def position_class_vec(vh, box_pos, vm60, slope):
    """向量化复刻 position_class.classify（只用到 vh/box_pos/vm60/slope）。"""
    low = (vh <= POS_CFG["low_vh"]) & (box_pos <= POS_CFG["low_box"]) & (vm60 < 0)
    trend_up = (slope > 0.3) & (vm60 > 0)
    high = ((vh >= -POS_CFG["high_vh"]) & (box_pos >= 65) & (vm60 > 0)) | \
           ((box_pos >= POS_CFG["high_box_hi"]) & (vm60 > 0) & trend_up & (vh >= -POS_CFG["high_vh_floor"]))
    out = np.where(low, "LOW", np.where(high, "HIGH", "MID"))
    bad = np.isnan(vh) | np.isnan(box_pos) | np.isnan(vm60) | np.isnan(slope)
    out = np.where(bad, "?", out)
    return out


def features(panel: Panel, i: int, cols: np.ndarray):
    """第 i 天（as-of）对 cols 这批列的因子（PIT：只用 ≤ i 的数据）。"""
    C, L, A, P = panel.close, panel.low, panel.amt, panel.pct
    c = C[i, cols]
    w250 = C[max(0, i - 249):i + 1][:, cols]
    with np.errstate(invalid="ignore"):
        hi250 = np.nanmax(np.where(np.isnan(w250), -np.inf, w250), axis=0)
        lo250 = np.nanmin(np.where(np.isnan(w250), np.inf, w250), axis=0)
    hi250 = np.where(np.isfinite(hi250), hi250, np.nan)
    lo250 = np.where(np.isfinite(lo250), lo250, np.nan)
    box = C[max(0, i - 29):i + 1][:, cols]
    with np.errstate(invalid="ignore"):
        bhi = np.nanmax(np.where(np.isnan(box), -np.inf, box), axis=0)
        blo = np.nanmin(np.where(np.isnan(box), np.inf, box), axis=0)
    bhi = np.where(np.isfinite(bhi), bhi, np.nan)
    blo = np.where(np.isfinite(blo), blo, np.nan)
    ma60 = np.nanmean(C[max(0, i - 59):i + 1][:, cols], axis=0)
    m30 = np.nanmean(box, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        vh = (c / hi250 - 1.0) * 100.0
        box_pos = (c - blo) / np.where((bhi - blo) == 0, np.nan, bhi - blo) * 100.0
        vm60 = (c / ma60 - 1.0) * 100.0
        slope = (ma60 - m30) / np.abs(np.where(m30 == 0, np.nan, m30)) * 100.0
        r20 = (c / C[i - 20, cols] - 1.0) * 100.0 if i >= 20 else np.full(len(cols), np.nan)
        r60 = (c / C[i - 60, cols] - 1.0) * 100.0 if i >= 60 else np.full(len(cols), np.nan)
    # 生产 position_features 会先 round（vh/box/vm60 到 1 位、slope 到 3 位）再 classify →
    # 边界票的口径必须一致，否则会在 vh=-10.0/box=65.0 这类阈值上出现 ~1% 的分歧（自检实测）。
    pos = position_class_vec(np.round(vh, 1), np.round(box_pos, 1), np.round(vm60, 1), np.round(slope, 3))
    amt20 = np.nanmean(A[i - 19:i + 1][:, cols], axis=0) / 1e5      # 千元 → 亿元
    lim = np.nansum(P[i - 59:i + 1][:, cols] >= LIMITUP, axis=0) if i >= 1 else np.zeros(len(cols))
    hist = np.sum(~np.isnan(C[max(0, i - 249):i + 1][:, cols]), axis=0)
    bars80 = np.sum(~np.isnan(C[max(0, i - 259):i + 1][:, cols]), axis=0) >= 80   # 生产: 取数窗口内 >=80 根
    with np.errstate(invalid="ignore", divide="ignore"):
        d_prevlow = (c / L[i, cols] - 1.0) * 100.0
        d_prevlow_prev = (c / L[i - 1, cols] - 1.0) * 100.0 if i >= 1 else np.full(len(cols), 99.0)
        lo5 = np.nanmin(L[i - 4:i + 1][:, cols], axis=0) if i >= 4 else np.full(len(cols), np.nan)
        d_low5 = (c / lo5 - 1.0) * 100.0
    # 停牌口径修正：生产用的是"该票**实际存在的交易日**序列"（跨日比较），按日历偏移在缺口票上会失真
    # （实测约 1% 成分票）。对缺口票逐票按可用序列重算 r20/r60/lim/amt20。
    gap = (~np.isnan(c)) & (hist < 250)      # 近 250 个交易日里有缺口 → 按可用序列重算
    for k in np.where(gap)[0]:
        j = cols[k]
        av = np.where(~np.isnan(C[:i + 1, j]))[0]
        if len(av) < 61:
            r20[k] = np.nan
            r60[k] = np.nan
            continue
        cl = C[av, j]
        r20[k] = (cl[-1] / cl[-21] - 1.0) * 100.0
        r60[k] = (cl[-1] / cl[-61] - 1.0) * 100.0
        seg = cl[-61:]
        lim[k] = float(np.sum(seg[1:] / seg[:-1] - 1.0 >= LIMITUP / 100.0))
        amt20[k] = float(np.mean(A[av[-20:], j])) / 1e5
    return dict(c=c, r20=r20, r60=r60, amt20=amt20, lim=lim, pos=pos, hist=hist, bars80=bars80,
                dist_prevlow=d_prevlow, dist_prevlow_prev=d_prevlow_prev, dist_low5=d_low5,
                trig_price=L[i, cols] * 1.005)


# ─────────────────────────── pick_v2 规则复刻 ───────────────────────────
def pick_day(panel: Panel, i: int, theme: str, uni, th_cons, cmap, ts2cons=None):
    """复刻 pick_v2(theme, as_of=第 i 天)。返回 dict（scored 明细 + 各臂成员下标）。"""
    codes = uni.get(theme) or []
    if not codes:
        return None
    jj = np.array([panel.ci[c] for c in codes if c in panel.ci])
    kept_codes = [c for c in codes if c in panel.ci]
    if len(jj) == 0:
        return None
    f = features(panel, i, jj)
    tset = th_cons.get(theme) or set()
    # 概念归属：默认用概念表 ∩ 主题概念集（= 主题成分域自身的口径）；
    # 传入 ts2cons 时用它（= 生产 confirm 域的 `uni` 口径：只有确认进该子概念的票才在该组内）。
    if ts2cons is None:
        ts2cons = {c: [x for x in cmap.get(c, []) if x in tset] for c in kept_codes}
    cross = np.array([len(ts2cons.get(c, [])) for c in kept_codes], dtype=float)
    # L2：必须有 as-of 当日 bar、≥80 根、非 ST、成交额 ≥1 亿（板块过滤见 --board 说明：默认不剔创业板，
    # 账户权限过滤属执行层，选择层验收不加，避免把"没权限"混进"选股能力"）
    ok = (~np.isnan(f["c"])) & f["bars80"] & (~panel.st[i, jj]) & (f["amt20"] >= MIN_AMT20_YI) \
         & (~np.isnan(f["r60"])) & (~np.isnan(f["r20"])) & (f["pos"] != "?")
    if ok.sum() < 3:
        return None
    idx = np.where(ok)[0]
    cross_f = cross[idx]          # 注意：排序用的 cross 必须与 leader 同索引（之前误用全域下标 → 排序错位）
    r60, r20, amt20, lim = f["r60"][idx], f["r20"][idx], f["amt20"][idx], f["lim"][idx]
    leader = (_nan_pct_rank(r60) + _nan_pct_rank(amt20) + _nan_pct_rank(lim)) / 3.0
    theme_r20 = float(np.mean(r20))
    rs = r20 - theme_r20
    pos = f["pos"][idx]
    d_prevlow = f["dist_prevlow"][idx]
    d_prevlow_prev = f["dist_prevlow_prev"][idx]
    trig = f["trig_price"][idx]
    n = len(idx)
    subs = [kept_codes[k] for k in idx]
    cols = jj[idx]
    # 组内前 2（pick_v2 pick_mode=concept）：按子概念分组，组内 leader 前 2（rank_in_concept）
    by_con = collections.defaultdict(list)
    for p in range(n):
        for cn in ts2cons.get(subs[p], []):
            by_con[cn].append(p)
    rank_in_concept = np.full(n, 99, dtype=int)
    concept_of = np.array([""] * n, dtype=object)
    for cn, arr in by_con.items():
        arr = sorted(arr, key=lambda p: (-leader[p], subs[p]))
        for r, p in enumerate(arr[:2], 1):
            if r < rank_in_concept[p]:
                rank_in_concept[p] = r
                concept_of[p] = cn
    cand = rank_in_concept <= 2
    # 主题容量约束：候选池按 leader 取前 50%（ceil，并列一并保留）
    if QUANTILE_PCT > 0 and cand.sum() >= 2:
        vals = np.where(cand, leader, -1e9)
        k = max(1, int(math.ceil(cand.sum() * QUANTILE_PCT / 100.0)))
        kth = np.sort(vals)[::-1][k - 1]
        cand = cand & (leader >= kth)
    lowmid = cand & np.isin(pos, ["LOW", "MID"]) & (r20 >= MIN_R20) & (rs >= RS_MIN)
    # tier1 / tier2
    order = sorted(np.where(lowmid)[0], key=lambda p: (rank_in_concept[p], -leader[p], -cross_f[p], subs[p]))
    t1 = [p for p in order if d_prevlow[p] <= DIST_PCT][:LIMIT]
    t2 = [p for p in order if p not in t1 and d_prevlow[p] <= TIER2_GAP][:max(0, MAX_LEGS - len(t1))]
    # 等待池 + 风向标（生产排序键 = (-leader, -cross, ts)）
    order_all = sorted(range(n), key=lambda p: (-leader[p], -cross_f[p], subs[p]))
    wait = order_all[:POOL_N]
    wind_broken = bool(len(wait) and d_prevlow_prev[wait[0]] <= WIND_BREAK)
    if wind_broken:            # WOLF_PICK_WIND_HARD 默认 1 → 硬拦清空
        t1, t2 = [], []
    out = dict(theme=theme, date=panel.dates[i], subs=subs, cols=cols, n=n,
               leader=leader, r60=r60, r20=r20, amt20=amt20, lim=lim, rs=rs, pos=pos,
               dist_prevlow=d_prevlow, dist_prevlow_prev=d_prevlow_prev, trig=trig,
               cross=cross[idx], rank_in_concept=rank_in_concept,
               cand=cand, lowmid=lowmid, t1=np.isin(np.arange(n), t1), t2=np.isin(np.arange(n), t2),
               wait=np.isin(np.arange(n), wait), wind_broken=wind_broken, theme_r20=theme_r20)
    return out


def cross_of(p, subs, cmap, tset):
    return len([x for x in cmap.get(subs[p], []) if x in tset])


def legacy_pick(panel: Panel, i: int, order_ts, limit=2, scanned_max=60, batch=140):
    """原 confirm_pick（2026-09-09 之前线上版）的扫描序选股：沿 DB 扫描序取"前 limit 个 position∈LOW/MID"，
    扫到 60 只仍不足就放弃。用于回答"pick_v2 换掉了什么"。"""
    cand = [ts for ts in order_ts if ts in panel.ci][:batch]
    if not cand:
        return []
    jj = np.array([panel.ci[ts] for ts in cand])
    f = features(panel, i, jj)
    hist = np.sum(~np.isnan(panel.close[max(0, i - 249):i + 1][:, jj]), axis=0)
    picked, scanned = [], 0
    for k, ts in enumerate(cand):
        if np.isnan(f["c"][k]) or hist[k] < 60 or panel.st[i, jj[k]]:
            continue
        scanned += 1
        if scanned > scanned_max:
            break
        if f["pos"][k] in ("LOW", "MID"):
            picked.append(ts)
            if len(picked) >= limit:
                break
    return picked


# ─────────────────────────── 统计 ───────────────────────────
def _isnan(x):
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return True


def _stat(pairs, vals):
    """pairs=[(date8, v)]，vals=[v]；返回均值/中位/胜率/日度 t/块状 t（ISO 周分块）。"""
    from eval_leg_metrics import block_t
    # 统一成原生 float（pandas 的 to_dict 会给 numpy 标量 → statistics.pstdev 会报没有 .numerator）
    pairs = [(d, float(v)) for d, v in (pairs or []) if v is not None and not _isnan(v)]
    v = [float(x) for x in vals if x is not None and not _isnan(x)]
    if not v:
        return {"n": 0}
    arr = np.array(v, dtype=float)
    m = float(arr.mean())
    sd = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    bt = block_t(pairs) if pairs else {"blocks": 0}
    return {"n": len(arr), "mean": round(m, 3), "median": round(float(np.median(arr)), 3),
            "win": round(float((arr > 0).mean()), 3),
            "t": round(m / (sd / math.sqrt(len(arr))), 2) if sd > 0 else None,
            "block_t": bt.get("t"), "blocks": bt.get("blocks")}


def _rows_stat(rows, key, date_key="d"):
    return _stat([(r[date_key], r.get(key)) for r in rows], [r.get(key) for r in rows])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260904")
    ap.add_argument("--pool-only", action="store_true", help="只在方向层池内主题上跑（生产产量口径）")
    ap.add_argument("--hold", type=int, default=5, help="前瞻持有交易日数（默认 5；低位埋伏口径更长，见 blueprint §6）")
    ap.add_argument("--themes", default="", help="逗号分隔，限定主题")
    ap.add_argument("--trades", action="store_true", help="额外核对生产实际成交腿（需 PG 隧道）")
    ap.add_argument("--json", default=OUT)
    global HOLD
    args = ap.parse_args()
    HOLD = int(args.hold)

    panel = Panel()
    uni, lead, allc, MS = load_universe()
    th_cons, cmap = theme_concept_sets()
    from fusion_mainline import THEME_CONCEPTS as LEGACY_CONS       # 旧 confirm_pick 的概念表
    legacy_order = {th: legacy_scan_order(th, th_cons, LEGACY_CONS.get(th)) for th in uni}
    themes = [t for t in uni if not args.themes or t in args.themes.split(",")]
    print("[eval] bars %d 交易日 × %d 只 (%s→%s) | 主题 %d" %
          (panel.T, panel.N, panel.dates[0], panel.dates[-1], len(themes)), flush=True)

    days = [d for d in panel.dates if args.start <= d <= args.end and panel.di[d] >= 260]
    print("[eval] 评估日 %d 天 (%s→%s)" % (len(days), days[0], days[-1]), flush=True)

    # 方向层池（PIT）：复刻 jobs/wolf_mainline_select.run 的 pool 口径（r5>0 ∩ 5日成交额占比 topK）
    pool_by_day = {}
    if args.pool_only:
        print("[eval] 计算方向层池（PIT 回放）…", flush=True)
        pctw = pd.DataFrame(panel.pct, index=panel.dates, columns=panel.codes)
        amtw = pd.DataFrame(panel.amt, index=panel.dates, columns=panel.codes).fillna(0.0)
        px = {d: {c: float(v) for c, v in pctw.loc[d].dropna().items()} for d in panel.dates}
        mamt5 = amtw.sum(axis=1).rolling(5).sum()
        for d in days:
            i = panel.di[d]
            share5 = {}
            for th, codes in uni.items():
                cc = [c for c in codes if c in amtw.columns]
                if not cc:
                    continue
                s = amtw[cc].sum(axis=1).rolling(5).sum() / mamt5.replace(0, np.nan)
                v = s.iloc[i]
                if v == v:
                    share5[th] = float(v)
            res = MS.score_day(panel.dates, i, px, uni, lead, allc, share5=share5, pool_k=3)
            pool_by_day[d] = res.get("pool") or []
        print("[eval] 池（按池大小分布）:", collections.Counter(len(v) for v in pool_by_day.values()), flush=True)

    recs = []            # 每个 (主题,日,成分股) 一行
    day_rows = []        # 每个 (主题,日) 一行：各臂的均值（用于块状 t）
    ic_rows = []
    for d in days:
        i = panel.di[d]
        r_fwd = panel.fwd_ret(i, HOLD, 1)
        r_254, hit_254 = panel.fwd_ret_254(i, HOLD)
        if r_fwd is None:
            continue
        _mv = r_fwd[~np.isnan(r_fwd)]
        mkt = float(_mv.mean()) if len(_mv) else np.nan      # 全市场等权（同日同窗口）
        ths = pool_by_day.get(d, themes) if args.pool_only else themes
        for th in ths:
            codes = uni.get(th) or []
            if not codes:
                continue
            basket_idx = np.array([panel.ci[c] for c in codes if c in panel.ci])
            basket = np.nanmean(r_fwd[basket_idx]) if len(basket_idx) else np.nan
            try:
                pk = pick_day(panel, i, th, uni, th_cons, cmap)
            except Exception as e:
                print("[eval] pick_day err", d, th, type(e).__name__, str(e)[:80], flush=True)
                continue
            if pk is None:
                continue
            cols = pk["cols"]
            rf = r_fwd[cols]
            r254 = r_254[cols]
            fill = hit_254[cols]
            ex = rf - basket
            n = pk["n"]
            recs.append(dict(d=d, th=th, n=pk["n"], cols=cols, rf=rf, r254=r254, fill=fill, ex=ex,
                             leader=pk["leader"], r60=pk["r60"], r20=pk["r20"], amt20=pk["amt20"],
                             lim=pk["lim"], rs=pk["rs"], pos=pk["pos"], cross=pk["cross"],
                             d_prevlow=pk["dist_prevlow"], rank_in_concept=pk["rank_in_concept"],
                             cand=pk["cand"], lowmid=pk["lowmid"], t1=pk["t1"], t2=pk["t2"],
                             wait=pk["wait"], basket=basket, wind_broken=pk["wind_broken"]))
            # 各臂 theme-day 均值
            def m(mask, arr=None):
                a = rf if arr is None else arr
                v = a[mask]
                v = v[~np.isnan(v)]
                return float(v.mean()) if len(v) else None
            def mxx(mask):
                v = ex[mask]
                v = v[~np.isnan(v)]
                return float(v.mean()) if len(v) else None
            pos_is = {p: (pk["pos"] == p) for p in ("LOW", "MID", "HIGH")}
            # 旧 confirm_pick 臂（DB 扫描序）
            try:
                leg_ts = legacy_pick(panel, i, legacy_order.get(th) or [])
            except Exception:
                leg_ts = []
            leg_mask = np.isin(np.array(pk["subs"], dtype=object), np.array(leg_ts, dtype=object))
            row = dict(d=d, th=th, basket=basket, n_legacy=len(leg_ts),
                       v_legacy=m(leg_mask), x_legacy=mxx(leg_mask),
                       v_t1=m(pk["t1"]), x_t1=mxx(pk["t1"]),
                       v_t12=m(pk["t1"] | pk["t2"]), x_t12=mxx(pk["t1"] | pk["t2"]),
                       v_lowmid=m(pk["lowmid"]), x_lowmid=mxx(pk["lowmid"]),
                       v_cand=m(pk["cand"]), x_cand=mxx(pk["cand"]),
                       v_wait=m(pk["wait"]), x_wait=mxx(pk["wait"]),
                       v_basket=m(pk["t1"] | pk["t2"], np.full(n, basket)), x_basket=0.0,
                       v_market=mkt, x_market=mkt - basket,
                       n_t1=int(pk["t1"].sum()), n_t12=int((pk["t1"] | pk["t2"]).sum()),
                       n_lowmid=int(pk["lowmid"].sum()), n_cand=int(pk["cand"].sum()),
                       n_wait=int(pk["wait"].sum()), n=pk["n"],
                       wind_broken=pk["wind_broken"])
            # 位置档 / 闸条件（全成分口径，不选股）
            for p in ("LOW", "MID", "HIGH"):
                row["x_pos_" + p] = mxx(pos_is[p])
                row["n_pos_" + p] = int(pos_is[p].sum())
            row["x_r20pos"] = mxx(pk["r20"] >= 0)
            row["x_r20neg"] = mxx(pk["r20"] < 0)
            row["x_rspos"] = mxx(pk["rs"] >= 0)
            row["x_rsneg"] = mxx(pk["rs"] < 0)
            row["x_near"] = mxx(pk["dist_prevlow"] <= DIST_PCT)
            row["x_far"] = mxx(pk["dist_prevlow"] > DIST_PCT)
            row["x_top3"] = mxx(np.argsort(-pk["leader"])[:3])
            row["x_bot50"] = mxx(np.argsort(-pk["leader"])[n // 2:])
            # 254 买点口径
            for tag, mask in (("t1", pk["t1"]), ("t12", pk["t1"] | pk["t2"]), ("lowmid", pk["lowmid"]),
                              ("cand", pk["cand"])):
                mm = mask & fill
                row["fill_" + tag] = float(fill[mask].mean()) if mask.sum() else None
                vv = r254[mm]
                vv = vv[~np.isnan(vv)]
                row["v254_" + tag] = float(vv.mean()) if len(vv) else None
            # 池内随机（蒙特卡洛，匹配 t1+t2 的数量）
            rng = np.random.default_rng(abs(hash((d, th))) % (2 ** 32))
            kk = int((pk["t1"] | pk["t2"]).sum()) or 1
            pool_idx = np.where(pk["lowmid"])[0] if pk["lowmid"].sum() >= kk else np.where(pk["cand"])[0]
            rand_means, rand_ex = [], []
            if len(pool_idx) >= kk:
                for _ in range(MC_DRAWS):
                    pickr = rng.choice(pool_idx, size=kk, replace=False)
                    v = rf[pickr]
                    v = v[~np.isnan(v)]
                    if len(v):
                        rand_means.append(float(v.mean()))
                        rand_ex.append(float((v - basket).mean()))
            row["v_rand"] = float(np.mean(rand_means)) if rand_means else None
            row["x_rand"] = float(np.mean(rand_ex)) if rand_ex else None
            row["v_rand_p95"] = float(np.percentile(rand_means, 95)) if rand_means else None
            row["v_rand_p05"] = float(np.percentile(rand_means, 5)) if rand_means else None
            day_rows.append(row)
            # IC：leader 与前瞻超额（成分口径 / 池口径）
            okv = ~np.isnan(ex)
            ic_rows.append(dict(d=d, th=th, n=int(okv.sum()),
                                ic_leader=spearman(pk["leader"][okv], ex[okv]),
                                ic_r60=spearman(pk["r60"][okv], ex[okv]),
                                ic_amt20=spearman(pk["amt20"][okv], ex[okv]),
                                ic_lim=spearman(pk["lim"][okv], ex[okv]),
                                ic_rs=spearman(pk["rs"][okv], ex[okv]),
                                ic_r20=spearman(pk["r20"][okv], ex[okv]),
                                ic_leader_cand=spearman(pk["leader"][pk["cand"] & okv], ex[pk["cand"] & okv]),
                                ic_leader_lowmid=spearman(pk["leader"][pk["lowmid"] & okv], ex[pk["lowmid"] & okv])))
        if len(day_rows) % 200 == 0:
            print("[eval] %s 完成 (%d theme-day)" % (d, len(day_rows)), flush=True)

    # ───────── 汇总 ─────────
    D = pd.DataFrame(day_rows)
    IC = pd.DataFrame(ic_rows)
    res = {"window": [days[0], days[-1]], "n_days": len(days), "n_theme_days": len(D),
           "pool_only": bool(args.pool_only), "hold": HOLD, "themes": sorted(set(D["th"]))}
    arms = ["t1", "t12", "lowmid", "cand", "wait", "basket", "market", "rand", "legacy"]
    res["arms"] = {a: {"value": _rows_stat(day_rows, "v_" + a), "excess": _rows_stat(day_rows, "x_" + a)
                       if a != "basket" else {"n": len(D), "mean": 0.0, "median": 0.0, "win": None,
                                              "t": None, "block_t": None}}
                   for a in arms}
    res["arm_stock_counts"] = {a: (int(D["n_" + a].sum()) if ("n_" + a) in D.columns else None)
                               for a in arms}
    # 254 买点口径
    res["arm_254"] = {a: {"fill_rate": round(float(np.nanmean([r.get("fill_" + a) for r in day_rows
                                                                if r.get("fill_" + a) is not None])), 3)
                          if any(r.get("fill_" + a) is not None for r in day_rows) else None,
                          "value": _rows_stat(day_rows, "v254_" + a)}
                      for a in ("t1", "t12", "lowmid", "cand")}
    # 位置档与闸条件
    res["gates"] = {k: _rows_stat(day_rows, k) for k in
                    ["x_pos_LOW", "x_pos_MID", "x_pos_HIGH", "x_r20pos", "x_r20neg", "x_rspos", "x_rsneg",
                     "x_near", "x_far", "x_top3", "x_bot50"]}
    # 分主题
    res["by_theme"] = {}
    for th, g in D.groupby("th"):
        res["by_theme"][th] = {"n_theme_days": int(len(g)),
                               "t1": _rows_stat(g.to_dict("records"), "x_t1"),
                               "lowmid": _rows_stat(g.to_dict("records"), "x_lowmid"),
                               "cand": _rows_stat(g.to_dict("records"), "x_cand"),
                               "legacy": _rows_stat(g.to_dict("records"), "x_legacy"),
                               "basket": _rows_stat(g.to_dict("records"), "v_basket")}
    # 分段 H1/H2（按 theme-day 中位日切）
    if len(D):
        mid = sorted(D["d"])[len(D) // 2]
        for tag, g in (("H1", D[D["d"] < mid]), ("H2", D[D["d"] >= mid])):
            res["seg_" + tag] = {"split_at": mid, "n_theme_days": int(len(g)),
                                 **{a: _rows_stat(g.to_dict("records"), "x_" + a)
                                    for a in ("t1", "t12", "lowmid", "cand", "legacy", "rand")}}
    # IC
    def ic_stat(col):
        return _stat([(r["d"], r[col]) for r in ic_rows], [r[col] for r in ic_rows])
    res["ic"] = {c: ic_stat(c) for c in ["ic_leader", "ic_r60", "ic_amt20", "ic_lim", "ic_rs", "ic_r20",
                                         "ic_leader_cand", "ic_leader_lowmid"]}
    res["ic_pos_rate"] = {c: round(float(np.nanmean([1.0 if (r[c] or 0) > 0 else 0.0 for r in ic_rows
                                                     if r[c] is not None])), 3) for c in res["ic"]}
    # leader 十分位（分主题-日先算分位，再池化）
    dec = collections.defaultdict(list)
    for r in recs:
        if r["n"] < 10:
            continue
        q = np.clip((_nan_pct_rank(r["leader"]) * 10).astype(int), 0, 9)
        for k in range(10):
            v = r["ex"][q == k]
            v = v[~np.isnan(v)]
            if len(v):
                dec[k].append(float(v.mean()))
    res["leader_deciles"] = {int(k): round(float(np.mean(v)), 3) for k, v in sorted(dec.items())}
    res["leader_deciles_n"] = {int(k): len(v) for k, v in sorted(dec.items())}
    # 池内随机的分位位置（我们落在随机分布哪里）—— 用**同一口径的原始收益均值**做差（apples-to-apples）
    diffs = [r["v_t12"] - r["v_rand"] for r in day_rows
             if r.get("v_t12") is not None and r.get("v_rand") is not None]
    res["market_vs_basket"] = _rows_stat(day_rows, "x_market")
    res["pick_vs_random"] = _stat([(r["d"], r["v_t12"] - r["v_rand"]) for r in day_rows
                                   if r.get("v_t12") is not None and r.get("v_rand") is not None], diffs)
    res["wind_broken_days"] = int(D["wind_broken"].sum())
    res["n_t1_mean"] = round(float(D["n_t1"].mean()), 2)

    if args.trades:
        res["production_trades"] = check_production_trades(panel, uni, th_cons, cmap, day_rows)

    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1, default=float)
    print("[eval] 已写 %s" % args.json, flush=True)
    print(json.dumps({k: res[k] for k in ("window", "n_theme_days", "arms", "gates", "ic", "ic_pos_rate",
                                          "seg_H1", "seg_H2", "pick_vs_random")},
                     ensure_ascii=False, indent=1, default=float))
    return res


def check_production_trades(panel, uni, th_cons, cmap, day_rows):
    """生产实际成交腿 vs 选择层池：以真实成交为入场，看"买的是不是选择层选出来的票"。"""
    sys.path.insert(0, os.path.join(ROOT, ".dsh-tmp", "wolfbt"))
    from local_pg import DSN, ensure_tunnel
    ensure_tunnel()
    import psycopg2
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute("SELECT account_id, symbol, direction, trade_date, price, volume, profit, created_at, "
                "coalesce(voided,0) FROM paper_trades WHERE account_id IN ('stock','t') ORDER BY created_at")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    # symbol(SH600000) → ts_code(600000.SH)
    ts_of = {}
    for c in panel.codes:
        ts_of[("SH" if c.endswith(".SH") else "SZ") + c[:6]] = c
    out = []
    for acct, sym, direction, tdate, price, vol, profit, created, voided in rows:
        if voided:
            continue
        ts = ts_of.get(str(sym))
        if not ts:
            continue
        d = str(tdate).replace("-", "")
        if d not in panel.di or panel.di[d] < 260:
            continue
        # 该标的所属主题（取其主题成分里第一个命中的）
        ths = [th for th, codes in uni.items() if ts in codes]
        info = {"account": acct, "symbol": sym, "direction": direction, "date": d, "price": float(price),
                "volume": int(vol), "profit": float(profit or 0), "themes": ths, "in_pool": {}, "fwd": {}}
        i = panel.di[d]
        if direction in ("买入", "buy"):
            for off, tag in ((-1, "asof_prev"), (0, "asof_same")):
                k = i + off
                if k < 260:
                    continue
                for th in ths:
                    pk = pick_day(panel, k, th, uni, th_cons, cmap)
                    if pk is None:
                        continue
                    j = np.where(pk["cols"] == panel.ci.get(ts, -1))[0]
                    if len(j):
                        p = int(j[0])
                        info["in_pool"][f"{tag}|{th}"] = {"t1": bool(pk["t1"][p]), "t2": bool(pk["t2"][p]),
                                                          "lowmid": bool(pk["lowmid"][p]),
                                                          "cand": bool(pk["cand"][p]),
                                                          "leader": round(float(pk["leader"][p]), 3),
                                                          "rank_of": pk["n"]}
            r = panel.fwd_ret(i, HOLD, 0)     # 成交日收盘起算（保守）
            info["fwd5"] = None if r is None else round(float(r[panel.ci[ts]]), 3)
        out.append(info)
    return {"n_trades": len(out), "trades": out[:200]}


if __name__ == "__main__":
    main()
