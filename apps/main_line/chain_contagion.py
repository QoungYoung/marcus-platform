# -*- coding: utf-8 -*-
"""chain_contagion.py — 「锚定链主破位 ⇒ 同链/相关链**陪葬**」的连坐风控（2026-10-15 落地为影子）。

狼大原话（帖 47288722）：
  · 2026-10-12：「**海外链会因为大光向下破位加速陪葬**。所以我直接避开所有海外链方向，但是如果
    海外链破位**叠加**国产半导体链同时向下破位，那整个市场都要死，不管是谁，机构但凡不想死，
    哪怕后面还要跌这里他们也要做个技术支撑……」
  · 2026-10-09：「中际旭创里有机构**跌破年线强制止损**了吧 昨天开始就有了 **而且很多票在陪葬**」
  · 2026-10-15：「半导体国产链的好处现在就体现出来了：在碰到利空时**韧性会强于海外链**，说人话就是跌得少」
  → 规则：**链主（leading_verified）破位 ⇒ 同链成员先降级（连坐）**；跨链（海外链 → 国产链）另行加权。
    这是「**先被送葬**」的防守侧机制：不等成员自己破位，先按链主的状态降级 ✓。

离线验收（`python jobs/eval_chain_contagion.py --start 20260701 --end 20260914 --hold 5,10`，
340 个「链主当日首次下穿 20 日低位」事件、412 个有日线的成员代码；口径＝「首次下穿」= 当日新低 ∧\n前一日非新低）——**结论是分开的**：
  · **通用「同链连坐」没有增量信息 ✗**：同链**未破**成员 5 日中位 −0.26%（基线 −0.79%）⇒ 反而**跑赢**
    +0.53pp；10 日中位 +0.83%（基线 +0.89%）⇒ 只跑输 −0.06pp（≈0）。未破 vs 已破中位差
    +0.81pp / −0.61pp（不稳定）⇒ **不要拿它当通用风控闸**（这一条是我们自己证伪的）。
  · **他具体那条「大光破位 ⇒ 国产半导体链」成立 ✓**：大光(300308.SZ) 当日首次下穿 ⇒ 国产链成员
    5 日中位 **−2.99%**（基线 −0.71%，中位差 **−2.27pp**）、10 日中位 **−7.10%**（基线 +0.77%，差 **−7.88pp**）。
    ⚠️ 但**样本小**（大光窗口内只破 5 次 ⇒ 大概率 2~3 个独立事件段、成员高度同期重叠）⇒ 只能当**观察项**，
    不能当闸门；要更强结论需要更长的标的级数据（本地 gz 库只到 2026-09-14）。
  · **提前量**：成员自己首次下穿比链主晚 **中位 7 个交易日** ⇒ 链主破位适合做「同链两周内重点观察」的触发器。
数据（全部本地，无 DB 依赖 → 生产容器里若缺文件一律 fail-open ✓）：
  · 链结构 `data/chain_map_<YYYYMMDD>.json`（**取 ≤ as-of 的最新快照**，无未来函数 ✓）：
      themes[] → segments[] = {role, label, concepts[], candidates[ts_code…], leading_verified[{ts,name,ok,…}]}；
  · 标的日线 `data/_bt_bars_gap.csv.gz` / `data/bars_2026.csv.gz` / `data/_bt_bars_hist.csv.gz`
      （无表头：`ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount, …`）

判据（带 ⛔ 的是我们的代理，他只给了定性）：
  · **链主破位** = 收盘 < 前 20 日最低收盘（⛔ 20）∨ 收盘 < MA60（⛔ 60，他口里的“年线”我们本地取不到 250 日）
  · **连坐** = 链主破位当日 + `WINDOW` 个交易日内 ⇒ 该链**全部成员**标 `high`；未破但贴线（距破位 < 3%）⇒ `watch`
  · **跨链** = `CHAIN_ADJ`（源链概念关键词 → 目标链概念关键词 + 原话依据）；源链 `high` ⇒ 目标链成员
    按 `level`（same ⇒ high / half ⇒ watch）降级

开关：`WOLF_CHAIN_CONTAGION`（默认 **0** ⇒ 影子：只落盘 + 只读，不改任何交易行为）
      `WOLF_CHAIN_CONTAGION_WINDOW`（默认 5 交易日）｜`_NEAR`（默认 0.03）｜`_ASOF`（默认空＝今天）
自检：`python apps/main_line/chain_contagion.py`
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DATA = os.environ.get("DATA_DIR", "data")

BAR_FILES = ("_bt_bars_gap.csv.gz", "bars_2026.csv.gz", "_bt_bars_hist.csv.gz")
# 跨链传导（源链概念关键词 → 目标链概念关键词；依据＝他 2026-10-12/10-15 的原话）
CHAIN_ADJ: List[Dict[str, Any]] = [
    {"src_kw": ["光通信模块", "CPO概念", "光模块", "PCB"],
     "dst_kw": ["半导体材料", "半导体设备", "光刻胶", "存储芯片", "AI芯片"],
     "level": "same",
     "why": "2026-10-12「海外链会因为大光向下破位加速陪葬」＋10-15「国产链跌得少但会跟」"},
]


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def enabled() -> bool:
    return os.getenv("WOLF_CHAIN_CONTAGION", "0").strip() in ("1", "true", "yes")


def window() -> int:
    try:
        return int(os.getenv("WOLF_CHAIN_CONTAGION_WINDOW", "5"))
    except ValueError:
        return 5


def near_pct() -> float:
    return _envf("WOLF_CHAIN_CONTAGION_NEAR", 0.03)


def _silent_alert(where, exc=None):
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


# ─────────────────────────── 数据层 ───────────────────────────
def _find(rel: str) -> Optional[str]:
    for cand in (os.path.join(DATA, rel), os.path.join("data", rel)):
        if os.path.exists(cand):
            return cand
    return None


def chain_maps() -> List[Tuple[str, str]]:
    """→ [(date8, path)]，只认 `chain_map_<YYYYMMDD>.json`（排除 _v2_cmp / _ai_verdicts 等衍生件）。"""
    out = []
    for base in (os.environ.get("DATA_DIR", "data"), "data"):
        try:
            for fn in os.listdir(base):
                m = re.fullmatch(r"chain_map_(\d{8})\.json", fn)
                if m:
                    out.append((m.group(1), os.path.join(base, fn)))
        except OSError as _e:
            _silent_alert("chain_contagion.py:listdir", _e)
    return sorted(set(out))


def load_chain_map(asof: str) -> Optional[Dict[str, Any]]:
    """取 **≤ asof** 的最新链快照（无未来函数 ✓）；取不到返回 None（fail-open ✓）。"""
    asof = str(asof)[:8]
    cands = [p for d, p in chain_maps() if d <= asof]
    if not cands:
        return None
    try:
        return json.load(open(cands[-1], encoding="utf-8"))
    except Exception as _e:
        _silent_alert("chain_contagion.py:load_chain_map", _e)
        return None


def load_bars(codes: Optional[Iterable[str]] = None) -> Dict[str, List[Tuple[str, float, float]]]:
    """→ {ts_code: [(date8, close, high)]}（本地 gz 多源合并、按日升序）。取不到返回 {}。"""
    want = set(codes) if codes else None
    out: Dict[str, List[Tuple[str, float, float]]] = {}
    for fn in BAR_FILES:
        p = _find(fn)
        if not p:
            continue
        try:
            with gzip.open(p, "rt", encoding="utf-8", errors="ignore") as fh:
                for row in csv.reader(fh):
                    if len(row) < 6:
                        continue
                    code = row[0]
                    if want is not None and code not in want:
                        continue
                    try:
                        out.setdefault(code, []).append((str(row[1])[:8], float(row[5]), float(row[3])))
                    except (ValueError, IndexError) as _e:
                        _silent_alert("chain_contagion.py:bar_row", _e)
        except Exception as _e:
            _silent_alert("chain_contagion.py:bar_file:%s" % fn, _e)
    for c in out:
        out[c].sort(key=lambda x: x[0])
        seen, ded = set(), []
        for d, cl, hi in out[c]:                     # 多源可能有重复日 ⇒ 后者（更新的文件）为准
            if d in seen:
                ded = [x for x in ded if x[0] != d]
            seen.add(d)
            ded.append((d, cl, hi))
        out[c] = sorted(ded, key=lambda x: x[0])
    return out


# ─────────────────────────── 判据 ───────────────────────────
def broke(series: Sequence[Tuple[str, float, float]], asof: str, ma_win: int = 60,
          low_win: int = 20, look: Optional[int] = None) -> Dict[str, Any]:
    """链主**破位事件**判定（as-of 只用 ≤ asof ✓）。

    ⚠️ 2026-10-15 首测教训：用「收盘 < MA60」这种**状态**当信号 ⇒ 全市场 256 只同时 high ✗
    （他说的「破位/陪葬」是**事件**：**新跌破**关键位那一刻）。故改为：
      · cross_low = 最近 look 日内出现「前一日不破 20 日低位、当日破」的**首次下穿**；
      · cross_ma  = 同理的 MA60 首次下穿（严重度用）。
    """
    look = window() if look is None else look
    hist = [r for r in series if r[0] <= str(asof)[:8]]
    if len(hist) < max(low_win + 1, 21):
        return {"broke": False, "near": False, "why": "样本不足"}
    d, close, _ = hist[-1]
    low = min(r[1] for r in hist[-(low_win + 1):-1])
    ma = sum(r[1] for r in hist[-min(ma_win, len(hist)):]) / min(ma_win, len(hist))
    hi = max(r[1] for r in hist[-min(120, len(hist)):])
    def _new_low(i):                      # 「当日收盘创前 low_win 日新低」
        return i >= low_win and hist[i][1] < min(r[1] for r in hist[i - low_win:i])
    cross_low_d, cross_ma_d = "", ""
    for i in range(max(low_win + 1, len(hist) - max(look, 1)), len(hist)):
        prev, cur = hist[i - 1][1], hist[i][1]
        # ★ 首次下穿 = 今天是新低 ∧ 昨天**不是**新低（单边下跌里天天新低 ⇒ 不是事件 ✗）
        if not cross_low_d and _new_low(i) and not _new_low(i - 1):
            cross_low_d = hist[i][0]
        seg = hist[max(0, i - ma_win):i + 1]
        seg_p = hist[max(0, i - ma_win - 1):i]
        m_i = sum(r[1] for r in seg) / len(seg)
        m_p = (sum(r[1] for r in seg_p) / len(seg_p)) if seg_p else m_i
        if not cross_ma_d and cur < m_i <= m_p:
            cross_ma_d = hist[i][0]
    fresh = bool(cross_low_d)          # ★ 触发只认「新破 20 日低位」；破 MA60 只作严重度
    return {"date": d, "close": close, "low20": low, "ma60": ma, "hi120": hi,
            "broke": fresh, "cross_low": cross_low_d, "cross_ma": cross_ma_d,
            "near": bool((not fresh) and (close <= low * (1 + near_pct()))),
            "why": ("新破20日低位" if cross_low_d else
                    ("仅新破MA60(不触发，只作严重度)" if cross_ma_d else "未破"))}


def chains(asof: str) -> List[Dict[str, Any]]:
    """链清单：theme × segment（含锚定链主与成员代码）。取不到快照 ⇒ []。"""
    cm = load_chain_map(asof)
    out: List[Dict[str, Any]] = []
    for th in (cm or {}).get("themes") or []:
        theme = th.get("theme") or "?"
        for seg in th.get("segments") or []:
            anchors = [a for a in (seg.get("leading_verified") or []) if a.get("ok") is not False]
            out.append({"theme": theme, "role": seg.get("role") or "", "label": seg.get("label") or "",
                        "concepts": seg.get("concepts") or [],
                        "members": [c for c in (seg.get("candidates") or []) if isinstance(c, str)],
                        "anchors": [{"ts": a.get("ts"), "name": a.get("name")} for a in anchors if a.get("ts")]})
    return out


def assess(asof: Optional[str] = None, bars: Optional[Dict[str, List[Tuple[str, float, float]]]] = None) -> Dict[str, Any]:
    """全链连坐评估 → artifact dict（**绝不抛**；缺数据 ⇒ 空结果 ✓）。"""
    asof = (asof or os.getenv("WOLF_CHAIN_CONTAGION_ASOF", "") or time.strftime("%Y%m%d")).strip()[:8]
    cs = chains(asof)
    if not cs:
        return {"date": asof, "chains": [], "risk_by_code": {}, "why": "无链快照（fail-open）"}
    codes = set()
    for c in cs:
        codes.update(c["members"])
        codes.update(a["ts"] for a in c["anchors"])
    if bars is None:
        bars = load_bars(codes)
    w = window()
    out_chains = []
    risk: Dict[str, Dict[str, Any]] = {}
    for c in cs:
        anchors = []
        for a in c["anchors"]:
            b = broke(bars.get(a["ts"]) or [], asof)
            anchors.append({"ts": a["ts"], "name": a["name"], **b})
        hit = [a for a in anchors if a["broke"]]
        near = [a for a in anchors if a["near"] and not a["broke"]]
        level = "high" if hit else ("watch" if near else "ok")
        out_chains.append({"theme": c["theme"], "role": c["role"], "label": c["label"],
                           "concepts": c["concepts"], "members_n": len(c["members"]),
                           "anchors": anchors, "risk": level,
                           "why": ("链主破位：" + "、".join("%s(%s)" % (a["name"], a["why"]) for a in hit)) if hit
                                  else ("链主贴线：" + "、".join(str(a["name"]) for a in near) if near else "链主未破")})
        if level != "ok":
            for code in c["members"]:
                prev = risk.get(code)
                if not prev or (prev["level"] == "watch" and level == "high"):
                    risk[code] = {"level": level, "source": "intra", "chain": "%s/%s" % (c["theme"], c["label"]),
                                  "since": asof, "reason": out_chains[-1]["why"]}
    # 跨链传导
    for adj in CHAIN_ADJ:
        src = [ch for ch in out_chains if ch["risk"] == "high" and _kw_hit(ch["concepts"], adj["src_kw"])]
        if not src:
            continue
        lvl = "high" if adj.get("level") == "same" else "watch"
        for ch in out_chains:
            if ch in src or not _kw_hit(ch["concepts"], adj["dst_kw"]):
                continue
            for code in [m for cc in cs if "%s/%s" % (cc["theme"], cc["label"]) == "%s/%s" % (ch["theme"], ch["label"])
                         for m in cc["members"]]:
                prev = risk.get(code)
                if not prev or (prev["level"] == "watch" and lvl == "high"):
                    risk[code] = {"level": lvl, "source": "cross", "chain": "%s/%s" % (ch["theme"], ch["label"]),
                                  "since": asof, "reason": adj["why"]}
    return {"date": asof, "chains": out_chains, "risk_by_code": risk,
            "n_high": sum(1 for v in risk.values() if v["level"] == "high"),
            "n_watch": sum(1 for v in risk.values() if v["level"] == "watch"),
            "window": w, "switch": "on" if enabled() else "shadow"}


def _kw_hit(concepts: Sequence[str], kws: Sequence[str]) -> bool:
    return any(k in c for c in (concepts or []) for k in kws)


def read_contagion(state_file: str = "chain_contagion.json") -> Optional[Dict[str, Any]]:
    for cand in (os.path.join(DATA, state_file), os.path.join("data", state_file)):
        try:
            if os.path.exists(cand):
                return json.load(open(cand, encoding="utf-8"))
        except Exception as _e:
            _silent_alert("chain_contagion.py:read", _e)
    return None


def write_artifact(obj: Optional[Dict[str, Any]], state_file: str = "chain_contagion.json") -> bool:
    if not obj:
        return False
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = os.path.join(DATA, state_file + ".tmp")
        json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(DATA, state_file))
        return True
    except Exception as _e:
        _silent_alert("chain_contagion.py:write", _e)
        return False


def risk_of(ts_code: str, obj: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """给上层消费的唯一入口：**开关关着恒 None**（生产逐字不变 ✓）。"""
    if not enabled():
        return None
    obj = obj or read_contagion() or {}
    return ((obj.get("risk_by_code") or {}).get(ts_code) or {}).get("level")


if __name__ == "__main__":
    a = assess()
    print("as-of:", a.get("date"), "| chains:", len(a.get("chains") or []),
          "| high:", a.get("n_high"), "| watch:", a.get("n_watch"), "| switch:", a.get("switch"))
    for c in (a.get("chains") or [])[:12]:
        anchors = "、".join("%s:%s" % (x.get("name"), x.get("why")) for x in (c.get("anchors") or [])[:3])
        print("  [%-5s] %s / %s  (%d 成员)  %s" % (c["risk"], c["theme"], c["label"], c["members_n"], anchors))
    write_artifact(a)
    print("artifact:", read_contagion() is not None)
