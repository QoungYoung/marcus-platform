# -*- coding: utf-8 -*-
"""wolf_direction_position.py — G5（2026-09-14）：**方向（主题/概念）的高低位**判定。

狼大原话（分类型操作总纲，2026-09-04 15:07 NGA 长帖）：
  「之前的高位方向 大科技这些，**避开公墓重仓的同时，卖强的 留弱的 拉升后都走**。
    之前的低位方向 AI软券商军工这些，避开暴雷ST股的同时，**找辨识度最高老龙头埋伏，强的留 弱的丢**。」

关键：这里的"高位/低位"说的是**方向（板块/概念）**，不是个股自己的价格分位
（个股分位口径已由其它模块用；见 docs/wolf-exit-gap-audit.md G5）。

判据来源（**复用既有能力，不新造分类器**）：
  `data/position_class_result.json` —— `apps/main_line/position_class.py` 产出的东财概念级
  HIGH/MID/LOW 分类（概念自身结构 + 位置/量能/资金流 + 指数大级别锚，每日刷新）。
  方向 → 概念名：`apps/main_line/fusion_mainline.py` 的 THEME_CONCEPTS（既有映射）。

输出: verdict ∈ {"HIGH","LOW","MID","UNKNOWN"} + 证据（命中的概念、各自 position、计数、文件 mtime）。
**数据缺失/过期/映射不上 → UNKNOWN**（绝不猜；调用方按 UNKNOWN 走原行为）。

⚠️ 为什么不直接"多数票"：该分类器**整体偏 MID**（2026-09-14 实测：MID 420 / HIGH 28 / LOW 4 / 空 69），
所以"某方向里 MID 最多"毫无信息量。改用**相对全局基准的富集度**（透明、可复核）：
  HIGH 方向 ⟺ 该方向命中概念中 HIGH 个数 ≥ `WOLF_DIRECTION_POS_MIN_HIGH`（默认 2）
               且 HIGH 占比 ≥ `WOLF_DIRECTION_POS_SHARE_MULT`（默认 2.0）× 全局 HIGH 基准率；
  LOW 同理（用 LOW 个数/占比）。不满足 → MID/UNKNOWN（**走原行为**，绝不猜）。

开关:
  WOLF_DIRECTION_POS=0      关闭（永远 UNKNOWN）
  WOLF_DIRECTION_POS_MAX_AGE_H  数据新鲜度护栏（小时，默认 48；超龄 → UNKNOWN）
  WOLF_DIRECTION_POS_MIN_HIGH / WOLF_DIRECTION_POS_SHARE_MULT  富集门槛（见上）
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

_CACHE: Dict[str, Any] = {"t": 0.0, "mtime": None, "by_name": {}, "by_code": {}}
CACHE_SECONDS = 600.0


def _data_dir() -> str:
    return os.environ.get("DATA_DIR", "/app/data")


def _result_path() -> str:
    return os.environ.get(
        "WOLF_DIRECTION_POS_FILE", os.path.join(_data_dir(), "position_class_result.json"))


def _enabled() -> bool:
    return os.getenv("WOLF_DIRECTION_POS", "1").strip() not in ("0", "false", "no")


def _max_age_h() -> float:
    try:
        return float(os.getenv("WOLF_DIRECTION_POS_MAX_AGE_H", "48"))
    except (TypeError, ValueError):
        return 48.0


def _float_env(name: str, dflt: float) -> float:
    try:
        return float(os.getenv(name, str(dflt)))
    except (TypeError, ValueError):
        return dflt


def _int_env(name: str, dflt: int) -> int:
    try:
        return int(float(os.getenv(name, str(dflt))))
    except (TypeError, ValueError):
        return dflt


def _base_rate() -> float:
    """全局 HIGH 基准率（用同一份分类结果算；用于富集度归一）。"""
    _by_name, by_code, _mt = _load()
    if not by_code:
        return 0.0
    n = len(by_code)
    h = sum(1 for v in by_code.values() if str(v.get("position")).upper() == "HIGH")
    return (h / n) if n else 0.0


def _load() -> Tuple[Dict[str, dict], Dict[str, dict], Optional[float]]:
    """读分类结果（带 mtime 缓存）。返回 (name→entry, code→entry, mtime)。"""
    p = _result_path()
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}, {}, None
    now = time.time()
    if _CACHE["by_name"] and _CACHE["mtime"] == mt and (now - _CACHE["t"]) < CACHE_SECONDS:
        return _CACHE["by_name"], _CACHE["by_code"], mt
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (ValueError, OSError):
        return {}, {}, mt
    by_name: Dict[str, dict] = {}
    by_code: Dict[str, dict] = {}
    if isinstance(raw, dict):
        for code, v in raw.items():
            if not isinstance(v, dict):
                continue
            rec = {"code": code, "name": v.get("name"), "position": v.get("position"),
                   "trend": v.get("trend"), "op": v.get("op")}
            by_code[str(code)] = rec
            nm = v.get("name")
            if nm:
                by_name.setdefault(str(nm), rec)
    _CACHE.update({"t": now, "mtime": mt, "by_name": by_name, "by_code": by_code})
    return by_name, by_code, mt


def _theme_concepts(theme: str) -> List[str]:
    """主题 → 东财概念名列表（复用 fusion_mainline.THEME_CONCEPTS）。"""
    if not theme:
        return []
    try:
        import sys as _s
        here = os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.join(here, "..", "..", "apps", "main_line"),
                  os.path.join(here, "..", "..", "..", "apps", "main_line")):
            p = os.path.abspath(p)
            if os.path.isdir(p) and p not in _s.path:
                _s.path.insert(0, p)
        from fusion_mainline import THEME_CONCEPTS  # type: ignore
        return list(THEME_CONCEPTS.get(theme) or [])
    except Exception:
        return []


def concepts_of_theme(theme: str) -> List[Dict[str, Any]]:
    """该方向命中的概念及其 position（仅返回有分类结果的概念）。"""
    by_name, _by_code, _mt = _load()
    out = []
    for nm in _theme_concepts(theme):
        rec = by_name.get(nm)
        if rec and rec.get("position"):
            out.append(dict(rec))
    return out


def direction_position(theme: str, max_age_h: Optional[float] = None) -> Dict[str, Any]:
    """方向高低位。返回 {verdict, reason, concepts, n_high, n_low, n_mid, age_h}。

    verdict 规则（透明、可复核，不用阈值拟合）：
      · 有分类结果的概念里 **HIGH 多于 LOW** → HIGH（高位方向）
      · **LOW 多于 HIGH** → LOW（低位方向）
      · 两者相等且都有 → MID（不切换分支）
      · 一个都没命中 / 数据缺失或超龄 / 开关关 → UNKNOWN
    """
    theme = str(theme or "").strip()
    if not theme or not _enabled():
        return {"verdict": "UNKNOWN", "reason": "关闭或无主题", "concepts": [],
                "n_high": 0, "n_low": 0, "n_mid": 0, "age_h": None}
    _by_name, _by_code, mt = _load()
    if mt is None:
        return {"verdict": "UNKNOWN", "reason": "无 position_class_result.json", "concepts": [],
                "n_high": 0, "n_low": 0, "n_mid": 0, "age_h": None, "base_rate": None}
    age_h = round((time.time() - mt) / 3600.0, 2)
    lim = _max_age_h() if max_age_h is None else float(max_age_h)
    if lim > 0 and age_h > lim:
        return {"verdict": "UNKNOWN", "reason": "分类结果超龄 %.1fh > %.1fh" % (age_h, lim),
                "concepts": [], "n_high": 0, "n_low": 0, "n_mid": 0, "age_h": age_h}

    cons = concepts_of_theme(theme)
    n_high = sum(1 for c in cons if str(c.get("position")).upper() == "HIGH")
    n_low = sum(1 for c in cons if str(c.get("position")).upper() == "LOW")
    n_mid = sum(1 for c in cons if str(c.get("position")).upper() == "MID")
    base = _base_rate()
    min_n = _int_env("WOLF_DIRECTION_POS_MIN_HIGH", 2)
    mult = _float_env("WOLF_DIRECTION_POS_SHARE_MULT", 2.0)
    share_h = (n_high / len(cons)) if cons else 0.0
    share_l = (n_low / len(cons)) if cons else 0.0
    if not cons:
        verdict, reason = "UNKNOWN", "方向『%s』没有可匹配的概念分类" % theme
    elif base and n_high >= min_n and share_h >= mult * base:
        verdict, reason = ("HIGH", "高位概念 %d/%d=%.0f%% ≥ %.1f×基准 %.1f%%"
                           % (n_high, len(cons), share_h * 100, mult, base * 100))
    elif base and n_low >= min_n and share_l >= mult * base:
        verdict, reason = ("LOW", "低位概念 %d/%d=%.0f%% ≥ %.1f×基准 %.1f%%"
                           % (n_low, len(cons), share_l * 100, mult, base * 100))
    else:
        verdict, reason = ("MID", "未达富集门槛（HIGH %d/%d, LOW %d/%d；基准 %.1f%%）"
                           % (n_high, len(cons), n_low, len(cons), (base or 0) * 100))
    return {"verdict": verdict, "reason": reason,
            "concepts": [{"code": c.get("code"), "name": c.get("name"), "position": c.get("position")}
                         for c in cons],
            "n_high": n_high, "n_low": n_low, "n_mid": n_mid,
            "share_high": round(share_h, 3), "share_low": round(share_l, 3),
            "base_rate": round(base, 4) if base else None, "age_h": age_h}


def branch_of(theme: str) -> Dict[str, Any]:
    """去弱留强的分支选择：高位方向→卖强留弱；低位/中位/未知→留强丢弱（原行为）。"""
    if os.getenv("WOLF_POSITION_DISC_HIGH", "1").strip() in ("0", "false", "no"):
        return {"verdict": "UNKNOWN", "branch": "low_sell_weak", "reason": "WOLF_POSITION_DISC_HIGH=0",
                "concepts": [], "n_high": 0, "n_low": 0, "n_mid": 0, "age_h": None}
    info = direction_position(theme)
    info["branch"] = "high_sell_strong" if info.get("verdict") == "HIGH" else "low_sell_weak"
    return info
