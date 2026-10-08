# -*- coding: utf-8 -*-
"""universe_exclude.py — 排除「光伏 / 商业航天」行业票（账本 §9.758）

用户拍板口径（2026-10-07）：
  原话「**能否直接去掉商业航天和光伏相关行业的票**」→「**申万行业呢**」→「**推进**」→「**做吧**」
  · **A 商业航天** = ③ **申万 `航天装备`（10 只）∪ 概念（商业航天/卫星互联网/北斗导航）里
    名字含 `航天`/`卫星`/`宇航` 的** ✓
    ★ **收紧**：**不用**裸「星」✗ —— 会误抓 `星宇股份(601799)`（车灯 ✗）／
      `星源材质(300568)`（锂电隔膜 ✗）这类**非航天**票 ✓
  · **B 光伏** = **全要** ✓：`l2_name == '光伏设备'` ∪ `l3_name LIKE '光伏%'`
    （含 **逆变器 10** ✓／硅料硅片 7 ✓／光伏发电 14 ✓）
    ✗ **不扩到** `电网设备 / 电池 / 风电设备 / 电机` ✓
  · **C 生效范围** = 回测开（pins=1 ✓）、**生产暂不开** ✓
    ⇒ ★ 代码默认 `WOLF_UNIVERSE_EXCLUDE='0'`（**关** ✓ ⇒ 生产零影响 ✓）

数据源 ✓（**申万自带 in_date/out_date** ⇒ **天然 as-of** ✓ —— 这是项目第一次拿到
  "可按时点重建行业归属"的权威数据 ✓，也是「过滤口径没长在买票路上」✗ 的正确修法 ✓）：
  · 申万全量 ✓：`index_member_all(is_new='Y')` ⇒ 5914 行 ✓
    列：`l1_name/l2_name/l3_name/ts_code/name/in_date/out_date/is_new` ✓
  · 概念侧 ✓：`data/concept_hist.json`（东财概念 ✓；`leader` **实为成员榜** ✓、**无代码** ✗
    ⇒ 用申万缓存的 `name → ts_code` 映射补 ✓）

★ 语义（**别搞反** ✗，本项目今天已在这栽过 ✓）：
    `check(symbol, day) -> (blocked, why)`
      `blocked=True`  ⇒ **拦**（禁止买入）✗
      `blocked=False` ⇒ **放行** ✓
    取不到名单/数据 ⇒ ★ **放行**（fail-open ✓，与 `universe_clean` / `lowdip_junk_gate` 既有口径一致 ✓）
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

ENV = "WOLF_UNIVERSE_EXCLUDE"

# ★ 概念侧关键词（**收紧** ✓：只用这三个 ✓；**明确不用**裸「星」✗）
SPACE_NAME_KW = ("航天", "卫星", "宇航")
# ★ 概念名（东财）→ 用于取成员榜 ✓
SPACE_CONCEPTS = ("商业航天", "卫星互联网", "北斗导航")
# ★ 光伏：申万二级名（精确 ✓）
PV_L2 = "光伏设备"
# ★ 商业航天：申万二级名（`航天装备` / `航天装备Ⅱ` 都要 ✓）
SPACE_L2_PREFIX = "航天装备"

_MEMO: Dict[str, Any] = {}


def enabled() -> bool:
    """总开关 ✓：`WOLF_UNIVERSE_EXCLUDE`，★ **默认 '0' ⇒ False**（生产零影响 ✓）"""
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def _repo() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _warn(msg: str) -> None:
    """★ §9.759 ✓：醒目告警（stderr ＋ 项目既有告警通道若可用 ✓）；绝不抛 ✓"""
    try:
        sys.stderr.write('[SW-CACHE] ✗ %s\n' % str(msg)[:200])
        sys.stderr.flush()
    except Exception:
        pass
    try:
        from app.services import gate_alarm as _ga     # 项目既有 ✓（不可用就算了 ✓）
        _ga.note('universe_exclude', RuntimeError('[SW-CACHE] %s' % str(msg)[:160]))
    except Exception:
        pass


def _warn_if_stale(path: str) -> None:
    """缓存**过期** ⇒ 报警 ✓（默认 7 天 ✓；★ 行为不变 ⇒ 仍放行 ✓）"""
    try:
        import time as _t
        days = float(os.getenv('WOLF_SW_CACHE_MAX_AGE_DAYS', '7') or 7)
        age = (_t.time() - os.path.getmtime(path)) / 86400.0
        if days > 0 and age > days:
            _warn('申万缓存已过期（%.1f 天 > %.1f 天 ✓）⇒ 建议刷新：'
                  '`jobs/bt_refresh_sw_cache.py` ✓ ｜ %s' % (age, days, path))
    except Exception:
        pass


def _sw_cache_path() -> Optional[str]:
    """申万缓存查找顺序 ✓：`WOLF_SW_CACHE` → `data/sw_members_cache.json` → `.dsh-tmp/prod/…` ✓"""
    cands: List[str] = []
    _e = str(os.getenv("WOLF_SW_CACHE") or "").strip()
    if _e:
        cands.append(_e)
    cands.append(os.path.join(_repo(), "data", "sw_members_cache.json"))
    cands.append(os.path.join(_repo(), ".dsh-tmp", "prod", "sw_members_cache.json"))
    for c in cands:
        if c and os.path.exists(c):
            _warn_if_stale(c)          # ★ §9.759 ✓：过期 ⇒ 大声报警（行为不变 ✓ 仍放行 ✓）
            return c
    # ★ §9.759 ✓（用户 2026-10-07 拍板 ✓）：缓存**缺失** ⇒ 必须**大声报警** ✗
    #   为什么 ✗：缺失时本模块 **fail-open 放行** ✓ ⇒ 「规则看着有、实际没生效」✗
    #     —— 今晚反复吃亏的形态 ✓ ⇒ 至少要在日志里喊出来 ✓（**不改语义** ✗）
    _warn('申万缓存**缺失**（找过 %s）⇒ 行业排除规则将 **fail-open 放行** ✗ '
          '请先跑 `jobs/bt_refresh_sw_cache.py` ✓' % '；'.join(x for x in cands if x))
    return None


def _concept_path() -> Optional[str]:
    c = os.path.join(_repo(), "data", "concept_hist.json")
    return c if os.path.exists(c) else None


def _sw_rows() -> List[Dict[str, Any]]:
    if "rows" in _MEMO:
        return _MEMO["rows"]
    p = _sw_cache_path()
    rows: List[Dict[str, Any]] = []
    if p:
        try:
            with open(p, encoding="utf-8") as fh:
                j = json.load(fh)
            rows = j if isinstance(j, list) else []
        except Exception as exc:  # noqa: BLE001
            print("[exclude] 申万缓存读取失败(按无数据 ⇒ 放行 ✓): %s" % str(exc)[:80], flush=True)
            rows = []
    else:
        print("[exclude] 找不到申万缓存 ⇒ 放行 ✓（fail-open ✓）", flush=True)
    _MEMO["rows"] = rows
    return rows


def to_sw(symbol: Any) -> str:
    """`SZ300477` ⇒ `300477.SZ` ✓（DB/引擎形态 ⇄ 申万形态 ✓）"""
    s = str(symbol or "").strip().upper()
    if "." in s:
        return s
    if len(s) >= 3 and s[:2] in ("SH", "SZ", "BJ"):
        return "%s.%s" % (s[2:], s[:2])
    return s


def to_engine(ts_code: Any) -> str:
    """`300477.SZ` ⇒ `SZ300477` ✓（反向 ✓）"""
    s = str(ts_code or "").strip().upper()
    if "." in s:
        a, b = s.split(".", 1)
        if b in ("SH", "SZ", "BJ"):
            return b + a
    return s


def _open_at(row: Dict[str, Any], day: Optional[str]) -> bool:
    """该申万归属在 `day`（8 位 ✓）当天是否有效 ✓：`in_date <= day <= out_date` ✓"""
    if not day:
        return True
    d = str(day).replace("-", "")[:8]
    ind = str(row.get("in_date") or "").replace("-", "")[:8]
    outd = str(row.get("out_date") or "")
    if ind and len(ind) == 8 and ind > d:
        return False
    if outd and outd.lower() not in ("nan", "none", "") and len(outd) >= 8:
        if outd[:8] < d:
            return False
    return True


def build_lists(day: Optional[str] = None) -> Dict[str, Any]:
    """按 `day` 重建两张名单 ✓（`day` 为空 ⇒ **当前**成员 ✓＝生产语义 ✓；给了 ⇒ as-of ✓）"""
    key = "lists:%s" % (str(day or "").replace("-", "")[:8])
    if key in _MEMO:
        return _MEMO[key]
    rows = _sw_rows()
    pv: Dict[str, str] = {}
    sp: Dict[str, str] = {}
    name2code: Dict[str, str] = {}
    for r in rows:
        nm = str(r.get("name") or "").strip()
        tc = str(r.get("ts_code") or "").strip()
        if nm and tc and nm not in name2code:
            name2code[nm] = tc
        if not _open_at(r, day):
            continue
        l2 = str(r.get("l2_name") or "")
        l3 = str(r.get("l3_name") or "")
        if l2 == PV_L2 or l3.startswith("光伏"):
            pv[tc] = "光伏(%s/%s)" % (l2 or "-", l3 or "-")
        if l2.startswith(SPACE_L2_PREFIX):
            sp[tc] = "商业航天(申万%s/%s)" % (l2, l3 or "-")
    # 概念侧 ✓：只取"名字含 航天/卫星/宇航"的成员 ✓（★ 不用裸「星」✗）
    cp = _concept_path()
    n_concept = 0
    if cp:
        try:
            with open(cp, encoding="utf-8") as fh:
                cj = json.load(fh) or {}
            for _k, v in (cj.items() if isinstance(cj, dict) else []):
                if not isinstance(v, dict):
                    continue
                cname = str(v.get("name") or "")
                if cname not in SPACE_CONCEPTS:
                    continue
                for nm in (v.get("leader") or []):
                    nm = str(nm or "").strip()
                    if not nm or not any(t in nm for t in SPACE_NAME_KW):
                        continue
                    tc = name2code.get(nm)
                    if not tc:
                        continue
                    if tc not in sp:
                        sp[tc] = "商业航天(概念%s)" % cname
                        n_concept += 1
        except Exception as exc:  # noqa: BLE001
            print("[exclude] 概念读取失败(只用申万侧 ✓): %s" % str(exc)[:80], flush=True)
    out = {"day": (str(day).replace("-", "")[:8] if day else ""), "pv": pv, "space": sp,
           "pv_n": len(pv), "space_n": len(sp), "space_from_concept": n_concept}
    _MEMO[key] = out
    return out


def check(symbol: Any, day: Optional[str] = None) -> Tuple[bool, str]:
    """★ 返回 `(blocked, why)` ✓ —— `blocked=True` 表示**拦**（禁买）✗

    · 开关关（默认 ✓）⇒ `(False, '')` ✓（生产零影响 ✓）
    · 取不到名单/数据 ⇒ `(False, '')` ✓（**fail-open** ✓）
    """
    if not enabled():
        return False, ""
    try:
        tc = to_sw(symbol)
        if not tc:
            return False, ""
        L = build_lists(day)
        if tc in L["pv"]:
            return True, "光伏行业票（%s）⇒ 用户口径：不买 ✗" % L["pv"][tc]
        if tc in L["space"]:
            return True, "商业航天票（%s）⇒ 用户口径：不买 ✗" % L["space"][tc]
        return False, ""
    except Exception as exc:  # noqa: BLE001
        print("[exclude] check 异常(放行 ✓): %s" % str(exc)[:80], flush=True)
        return False, ""


def stats(day: Optional[str] = None) -> Dict[str, Any]:
    """给单测/对账用 ✓：两张名单的规模与样例 ✓"""
    L = build_lists(day)
    return {"pv_n": L["pv_n"], "space_n": L["space_n"],
            "space_from_concept": L["space_from_concept"],
            "pv_sample": sorted(L["pv"].items())[:3], "space_sample": sorted(L["space"].items())[:3],
            "enabled": enabled(), "cache": _sw_cache_path()}
