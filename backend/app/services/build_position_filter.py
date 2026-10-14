# -*- coding: utf-8 -*-
"""build_position_filter.py — **选股端**的位置/涨幅闸（§9.844，用户 2026-10-13「把位置/涨幅条件前移到选股端」✓）

【为什么要前移 ✗】下游闸（`t_monitor._build_chase_block` ✓ §9.843）已能拦住"贴新高/已急涨"的建仓腿 ✓，
  但实测（t47 臂头 5 天 ✓）：**腿照样产生、全被拦** ✗ ⇒ 0 成交 ✓ —— 相当于"用下游闸替上游选股擦屁股" ✗。
  正确做法 ✓：**在选股/布腿这一步就不产生**这类腿 ✓（少而精 ✓），下游闸保留做**兜底** ✓。

【口径 ✓（与下游闸同源，便于 A/B 对照 ✓）】
  · `WOLF_BUILD_NEAR_HIGH_PCT`（默认 **0 = 关** ✓；标定值 2）⇒ 现价距 250 日高 > −2% ⇒ 不选 ✓
  · `WOLF_BUILD_GAIN20_MAX`（默认 **0 = 关** ✓；标定值 30）⇒ 20 日涨幅 > 30% ⇒ 不选 ✓
  ★ **出处类型必须区分 ✗**：方向=【语料 ✓】（2025-04-03「冲上去一定不能追」✓）；**数字=【标定 ✓】、无语料数字** ✗
     （语料 §29.1 原文：「C4 高开不追 → **不是"绝不追"，而是"不在开盘追"**」✓）
  标定出处 ✓：`scripts/calib_chase_position2.py`（1796 笔真实买入 ✓）——
    距 250 日高 −2~0%（贴新高）⇒ +10d 中位 −7.19%、胜率 22.0%（n=59 ✓）
    20 日涨幅 >30% ⇒ +10d 中位 −4.45%、胜率 36.9%、MAE −8.93%（n=195 ✓）
    对照「回调里买」（涨幅<0）⇒ +0.83% / 52.8%（n=286 ✓）

【数据与 as-of ✓】读 `$DATA_DIR/mkt_bars_local.db`（跑批天目录里那份 ✓）⇒ 退化到 `data/_bt_full/bars.sqlite` ✓；
  **只用 ≤ 传入 day 的行** ✓（防未来函数 ✗）；取不到数据 ⇒ **放行**（fail-open ✓，绝不因此漏建仓 ✓）
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Optional, Tuple


def thresholds() -> Tuple[float, float]:
    def _f(name: str) -> float:
        try:
            return abs(float(os.getenv(name, "0") or 0))
        except Exception:
            return 0.0
    return _f("WOLF_BUILD_NEAR_HIGH_PCT"), _f("WOLF_BUILD_GAIN20_MAX")


def _ts_code(sym: str) -> str:
    s = str(sym or "").strip().upper()
    m = re.match(r"^(SH|SZ|BJ)(\d{6})$", s)
    if m:
        return "%s.%s" % (m.group(2), m.group(1))
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", s)
    if m:
        return s
    m = re.match(r"^(\d{6})$", s)
    if m:
        c = m.group(1)
        return "%s.%s" % (c, "SH" if c[0] == "6" else ("BJ" if c[:2] in ("43", "83", "87", "92") else "SZ"))
    return ""


def _db_paths() -> list:
    out = []
    dd = os.environ.get("DATA_DIR")
    if dd:
        out.append(os.path.join(dd, "mkt_bars_local.db"))
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out += [os.path.join(here, "data", "_bt_full", "bars.sqlite"), "data/_bt_full/bars.sqlite"]
    return [p for p in out if p and os.path.exists(p)]


def wave_op(day=None, data_dir=None) -> str:
    """当日浪型档位（`wave_state.operation` ✓；取不到 ⇒ "" ⇒ 不拦 ✓ fail-open ✓）"""
    d8 = "".join(ch for ch in str(day or "") if ch.isdigit())[:8]
    cands = []
    for d in ([data_dir] if data_dir else []) + [os.environ.get("DATA_DIR")]:
        if d:
            cands.append(os.path.join(d, "wave_state.json"))
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if d8:
        cands.append(os.path.join(here, "data", "_bt_year", d8, "wave_state.json"))
    for pth in cands:
        try:
            with open(pth, encoding="utf-8") as f:
                op = str((json.load(f) or {}).get("operation") or "").strip().lower()
            if op:
                return op
        except Exception:
            continue
    return ""


def build_shape_mult(op: str, near_pct: float = 0.0, gain20: float = 0.0) -> float:
    """**降档系数** —— 把"涨幅/位置"从硬拦改为降档 ✓（用户「排序因子 + 仓位降档」✓）

    来源 ✓ = **标定**（calib_chase_position2.py，1796 笔买入 ✓）：涨幅>30% 档 +10d 中位 −4.45%、
    MAE −8.93% ✓；距高 −2~0% 档 +10d 中位 −7.19%、胜率 22% ✓ ⇒ 该"少买"✓ 而非"不买"✗
    **数字无语料依据** ✗（代理值待标定 ✓）
    """
    m = 1.0
    if gain20 and gain20 > 30:
        m *= 0.7
    if near_pct and near_pct > -2:
        m *= 0.7
    return round(m, 4)


def block_reason(symbol: str, day: Optional[str] = None, price: Optional[float] = None) -> str:
    """返回拦阻原因；"" = 放行 ✓（开关关 / 缺数 / 异常 ⇒ 一律放行 ✓）"""
    near, gmax = thresholds()
    if near <= 0 and gmax <= 0:
        return ""
    ts = _ts_code(symbol)
    if not ts:
        return ""
    d8 = "".join(ch for ch in str(day or "") if ch.isdigit())[:8]
    for db in _db_paths():
        try:
            cn = sqlite3.connect(db)
            cur = cn.cursor()
            if d8:
                rows = list(cur.execute(
                    "select trade_date, close, high from mkt_bars_daily where ts_code=? and trade_date<=? "
                    "order by trade_date desc limit 260", (ts, d8)))
                if not rows:
                    rows = list(cur.execute(
                        "select trade_date, close, high from bars where ts_code=? and trade_date<=? "
                        "order by trade_date desc limit 260", (ts, d8)))
            else:
                rows = []
            cn.close()
        except Exception:
            continue
        if len(rows) < 25:
            continue
        rows = list(reversed(rows))                     # 升序 ✓
        try:
            hi250 = max(float(r[2] or r[1] or 0) for r in rows[-250:])
            px = float(price) if price else float(rows[-1][1] or 0)
            c20 = float(rows[-21][1] or 0)
        except Exception:
            continue
        if px <= 0 or hi250 <= 0 or c20 <= 0:
            continue
        # ★ §9.849 ✓（2026-10-13 追问"阈值是我们自己定的吗"后收敛 ✓）：本闸**只在
        #   t_only/side/defense 档硬拦** ✓；`build` 档**不硬拦** ✗（改交"同批排序 + 降档"✓）
        #   依据 ✓：标定显示"排序因子随档位反转"（build 里 near60 为正 ✓、20 日涨幅为负 ✗；t_only 反之 ✓）
        #   实测 ✗：t50 排第一的东材科技 20 日 +33.2% 被本闸否掉 ⇒ **0 成交**（两条规则打架 ✓）
        _op_gate = wave_op(day)
        if _op_gate in ("", "build") or _op_gate not in ("t_only", "side", "defense"):
            continue
        if near > 0:
            d = (px / hi250 - 1) * 100
            if d > -near:
                return ("[SEL-POS] 距 250 日高仅 %+.2f%%（> −%.1f%%）⇒ **选股端不选** ✓"
                        "（标定：该档 +10d 中位 −7.19%%、胜率 22%% ✓）" % (d, near))
        if gmax > 0:
            g = (px / c20 - 1) * 100
            if g > gmax:
                return ("[SEL-GAIN] 20 日已涨 %.1f%%（> %.0f%%）⇒ **选股端不选** ✓"
                        "（标定：该档 +10d 中位 −4.45%%、胜率 36.9%%、MAE −8.93%% ✓）" % (g, gmax))
        return ""
    return ""

def _feat_from_bars(db_paths, ts_code, day):
    """取 (现价=当日收盘, 60日高, 20日涨幅%) —— 只用 ≤ day 的行 ✓（防未来函数 ✗）"""
    d8 = "".join(ch for ch in str(day or "") if ch.isdigit())[:8]
    for db in db_paths:
        try:
            cn = sqlite3.connect(db); cur = cn.cursor()
            rows = list(cur.execute(
                "select trade_date, close, high from mkt_bars_daily where ts_code=? and trade_date<=? "
                "order by trade_date desc limit 80", (ts_code, d8)))
            if not rows:
                rows = list(cur.execute(
                    "select trade_date, close, high from bars where ts_code=? and trade_date<=? "
                    "order by trade_date desc limit 80", (ts_code, d8)))
            cn.close()
        except Exception:
            continue
        if len(rows) < 21:
            continue
        rows = list(reversed(rows))
        px = float(rows[-1][1] or 0)
        hi60 = max(float(r[2] or r[1] or 0) for r in rows[-60:])
        c20 = float(rows[-21][1] or 0)
        return px, hi60, ((px / c20 - 1) * 100 if c20 else 0.0)
    return None, None, None


def rank_trend_legs(rows, day, k, factor="auto", data_dir=None):
    """同批趋势腿**排序选优**（§9.848，用户 2026-10-13「排序因子需要调整」✓）

    标定（`scripts/calib_breakout_rank.py` ✓，配对法/同批内比较 ✓）：
      · **build 档**（20 批 ✓）⇒ 最优因子 = **距 60 日高最近** ✓
          （top1−bot1 **+5.53pp**、配对胜率 **60%**、top1−批均 **+4.19pp** ✓；此时"20 日涨幅"为负 ✗）
      · **t_only 档**（34 批 ✓）⇒ 最优因子 = **20 日涨幅最强** ✓
          （+4.27pp / 58.8% / +3.05pp ✓；此时"距高"为负 ✗）
      ⇒ 两档**最优因子相反** ✓ ⇒ `factor="auto"` 时按当日 `wave_state.operation` 自动切换 ✓
    开关（默认 0 = 关 ✓ 不改变现状）：`WOLF_BREAKOUT_RANK_TOP`（保留前 k 条 ✓，0=关 ✓）

    ⚠️ 样本偏小 ✗（20/34 批 ✓）⇒ 仅作 A/B 候选 ✓，不擅自开生产 ✓
    """
    try:
        _k = int(float(os.getenv("WOLF_BREAKOUT_RANK_TOP", str(k)) or 0))
    except Exception:
        _k = int(k or 0)
    if _k <= 0 or len(rows) <= _k:
        return rows
    _dirs = []
    for d in (data_dir, os.environ.get("DATA_DIR"), *[os.path.dirname(x) for x in _db_paths()]):
        if d and d not in _dirs and os.path.isdir(d):
            _dirs.append(d)
    _dbp = []
    for d in _dirs:
        for f in ("mkt_bars_local.db",):
            _p = os.path.join(d, f)
            if os.path.exists(_p) and _p not in _dbp:
                _dbp.append(_p)
    _dbp += [x for x in _db_paths() if x not in _dbp]
    _fac = str(factor or os.getenv("WOLF_BREAKOUT_RANK_FACTOR", "auto")).strip().lower()
    if _fac in ("auto", ""):
        _op = ""
        for d in _dirs[:3]:
            try:
                _op = str((json.load(open(os.path.join(d, "wave_state.json"), encoding="utf-8")) or {})
                          .get("operation") or "").lower()
                if _op:
                    break
            except Exception:
                continue
        _fac = "near60" if _op == "build" else "g20"
    _scored = []
    for r in rows:
        ts = _ts_code(r.get("symbol"))
        px, hi60, g20 = _feat_from_bars(_dbp, ts, day) if ts else (None, None, None)
        if px is None:
            _scored.append((float("-inf"), r))          # 缺数 ⇒ 排最后（不因缺数而丢 ✓）
            continue
        _v = (px / hi60 - 1) if (_fac == "near60" and hi60) else (g20 or 0.0)
        _scored.append((_v, r))
    _scored.sort(key=lambda x: -x[0])
    _keep = [r for _, r in _scored[:_k]]
    print("[rank] 趋势腿同批排序选优 ✓ 因子=%s 保留前 %d/%d 条 ⇒ %s（标定：build⇒near60 / t_only⇒g20 ✓）"
          % (_fac, _k, len(rows), [r.get("symbol") for r in _keep]), flush=True)
    return _keep
