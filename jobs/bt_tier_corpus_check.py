# -*- coding: utf-8 -*-
"""档位判定 + 语料回放校验（2026-09-18 用户拍板"先做这个"）。

目的：在把中段风控（t_chop_guard 三条）接到执行层之前，先回答**两个问题**：
  ① 我们能不能用**可计算的量**把 2026-01 每天判成"主升/调整/风险/下跌"这一档？
  ② 判出来的档位能不能解释狼大**当天说过的仓位**（语料动作回放校验）？

输出：逐日对照表（我们的档位/区间 vs 语料原话给的仓位/动作）+ 对齐率 + 不一致日与疑似缺失输入。
用法：.venv/bin/python jobs/bt_tier_corpus_check.py [--md docs/wolf-daily-log-xls2026.md] [--month 2026-01]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.request
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


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_CSV = os.path.join(REPO, "data", "指数数据", "index_daily", "000001.SH.csv")

# 语料里"说仓位"的句子（用于回放校验）
QUOTE_PAT = re.compile(r"(仓位|满仓|空仓|减到|加到|清多单|清仓|降到|控[制在]*|降至|只留|留一点|底仓)")
BAND_PAT = re.compile(r"(\d{2})\s*%\s*[-~－]\s*(\d{2})\s*%")
ONE_PCT_PAT = re.compile(r"(\d{2})\s*%")


def index_rows(path: str = INDEX_CSV) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                d = str(r["trade_date"]).replace("-", "")
                out.append({"day": d, "close": float(r["close"]),
                            "amount": float(r.get("amount") or 0) or None})
            except Exception as _e_sil1:
                _silent_alert("bt_tier_corpus_check.py:40", _e_sil1)
                continue
    out.sort(key=lambda r: r["day"])
    return out


def enrich(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    closes = [r["close"] for r in rows]
    amts = [r["amount"] for r in rows]
    for i, r in enumerate(rows):
        c = closes[i]
        for n in (5, 20, 60):
            r["ma%d" % n] = round(sum(closes[max(0, i - n + 1):i + 1]) / min(n, i + 1), 2)
        r["above_ma5"] = c >= r["ma5"]
        r["above_ma20"] = c >= r["ma20"]
        r["above_ma60"] = c >= r["ma60"]
        r["ma5_slope5"] = round((r["ma5"] / rows[i - 5]["ma5"] - 1) * 100, 2) if i >= 5 else 0.0
        r["_prev_close"] = closes[i - 1] if i else None
        win = [x for x in amts[max(0, i - 20):i + 1] if x]
        r["amt"] = amts[i]
        r["amt_pct"] = (sum(1 for x in win if x <= (amts[i] or 0)) / len(win) * 100) if (win and amts[i]) else None
        hi20 = max(closes[max(0, i - 20):i + 1])
        r["dd_from_hi20"] = round((c / hi20 - 1) * 100, 2)
        # 连续收盘低于 MA5 的天数
        n = 0
        for j in range(i, max(-1, i - 10), -1):
            if rows[j]["close"] < rows[j]["ma5"]:
                n += 1
            else:
                break
        r["days_below_ma5"] = n
    return rows


CTRL_D20 = float(os.getenv("WOLF_CTRL_D20", "7.0"))      # 两融 20 日增速阈值（管控期代理，2026-01 校准）
CTRL_HI = 0.70                                           # 管控期：他说"仓位尽量控制收盘 60%-70%"
CTRL_LO = 0.60
EVENT_DROP = float(os.getenv("WOLF_EVENT_DROP", "0.8"))   # 单日跌幅阈值 → 事件修正（0130 他减到 40% 以内）
EVENT_HI = 0.40


def margin_ctx(day: str, root: str = "data/_bt_size") -> Dict[str, Any]:
    """管控期代理：读该回放日沙箱里的 etf_share_flow.json（as-of）→ 两融 20 日增速。"""
    p = os.path.join(REPO, root, day.replace("-", ""), "etf_share_flow.json")
    try:
        j = json.load(open(p, encoding="utf-8"))
        m = j.get("margin") or {}
        return {"ok": True, "date": str(j.get("date")), "d20_pct": m.get("d20_pct"),
                "rzye_yi": round(float(m.get("rzye_now") or 0) / 1e8)}
    except Exception:
        return {"ok": False}


def apply_modifiers(r: Dict[str, Any], lo: float, hi: float, why: str,
                    mctx: Dict[str, Any]) -> Tuple[float, float, str]:
    """② 管控期折减（两融增速）→ ③ 事件修正（单日急跌）。取最严。"""
    notes = []
    d20 = mctx.get("d20_pct") if mctx.get("ok") else None
    if d20 is not None and float(d20) >= CTRL_D20:
        if hi > CTRL_HI or lo > CTRL_LO:
            hi, lo = min(hi, CTRL_HI), min(lo, CTRL_LO)
            notes.append("管控期折减(两融20日+%.1f%%)→≤%.0f%%" % (float(d20), CTRL_HI * 100))
    prev = r.get("_prev_close")
    if prev and r["close"] / prev - 1 <= -EVENT_DROP / 100:
        hi, lo = min(hi, EVENT_HI), min(lo, EVENT_HI - 0.10)
        notes.append("事件修正(单日 %+.2f%%)→≤%.0f%%" % ((r["close"] / prev - 1) * 100, EVENT_HI * 100))
    return lo, hi, (why + ("；" + "；".join(notes) if notes else ""))


def tier_of(r: Dict[str, Any]) -> Tuple[str, float, float, str]:
    """档位判定 v1（只用可计算量）：返回 (档位, 区间下限, 区间上限, 依据)。"""
    if not r["above_ma60"]:
        return "下跌趋势", 0.0, 0.30, "收盘跌破 MA60（语料：下跌趋势就不做/跌破 60 日线大减）"
    if not r["above_ma20"]:
        return "风险", 0.20, 0.30, "收盘跌破 MA20 但仍在 MA60 上（语料：有风险 30）"
    if r["days_below_ma5"] >= 2:
        return "调整(弱)", 0.55, 0.65, "连续 ≥2 日收盘低于 MA5（语料：收盘收破 5 日线降一档；实测他 0114/0115 收盘 55-60%）"
    if not r["above_ma5"]:
        return "调整(弱)", 0.55, 0.65, "当日收盘破 MA5（语料：破 5 日线降一档；实测他 0114 收盘 55-60%、0130 特殊）"
    if r["ma5_slope5"] < 0:
        return "调整", 0.50, 0.70, "站上 MA5 但 MA5 5 日斜率 <0（语料：调整期以控仓为主 60-70）"
    return "主升/强势", 0.70, 1.00, "站上 MA5 且 MA5 上行（语料：主升 75%+、日内最高 80% 收盘 60%）"


def corpus_january(md: str, month: str) -> Dict[str, Dict[str, Any]]:
    """从语料抽取该月每天的"仓位/动作"句子。"""
    out: Dict[str, Dict[str, Any]] = {}
    day = None
    buf: List[str] = []
    for ln in open(md, encoding="utf-8"):
        m = re.match(r"^###\s+(\d{4}-\d{2}-\d{2})", ln.strip())
        if m:
            if day:
                out[day] = {"quotes": buf}
            day = m.group(1)
            buf = []
            continue
        if day and day.startswith(month) and QUOTE_PAT.search(ln):
            s = ln.strip().lstrip("-*> ").strip()
            # 排除「不是 A 股总仓位」的说法（0129 的 57% 是海外期货、0112 的 25% 是打野个股）
            if any(k in s for k in ("期货", "黄金", "原油", "杠杆", "打野", "个股仓位",
                                    "盈利", "换股", "调仓", "这部分仓位")):
                continue
            if s and not s.startswith("|"):
                buf.append(s[:150])
    if day:
        out[day] = {"quotes": buf}
    # 从引文里抽"他说了多少仓位"
    for d, v in out.items():
        stated = None
        # 先看「收盘 X%」这种明确口径（0115 说"日内最高80%、收盘回到60%"，收盘才是他的档）
        for q in v["quotes"]:
            mc = re.search(r"收盘[^0-9%]{0,8}(\d{2})\s*%", q)
            if mc:
                x = float(mc.group(1)) / 100
                stated = (x, x)
                break
        for q in v["quotes"]:
            if stated:
                break
            mb = BAND_PAT.search(q)
            if mb:
                stated = (float(mb.group(1)) / 100, float(mb.group(2)) / 100)
                break
            mo = ONE_PCT_PAT.search(q)
            if mo and ("仓位" in q or "收盘" in q or "减到" in q or "满仓" in q):
                x = float(mo.group(1)) / 100
                stated = (x, x)
                break
        v["stated"] = stated
    return out


def our_pos_pct(snap_url: str) -> Dict[str, float]:
    """我们那一跑（data/_bt_size）逐日仓位占比（看板接口，失败 → {}）。"""
    try:
        with urllib.request.urlopen(snap_url, timeout=6) as fh:
            d = json.load(fh)
        return {r["day"]: round((r.get("mv") or 0) / r["equity"] * 100, 1)
                for r in (d.get("curve") or []) if r.get("equity")}
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=os.path.join(REPO, "docs", "wolf-daily-log-xls2026.md"))
    ap.add_argument("--month", default="2026-01")
    ap.add_argument("--snap", default="http://127.0.0.1:8801/api/snapshot")
    a = ap.parse_args()

    rows = enrich(index_rows())
    by_day = {r["day"]: r for r in rows}
    corpus = corpus_january(a.md, a.month)
    ours = our_pos_pct(a.snap)

    print("== 档位判定 + 语料回放校验（%s）" % a.month)
    print("日期        收盘    MA5     MA20    MA60   站上5 斜率5 连跌 额分位 档位        区间      语料仓位   我们仓位 一致?")
    n_cmp = n_ok = 0
    lines_out = []
    for d in sorted(corpus):
        if not d.startswith(a.month):        # 只看本月（原实现把 7/8 月也列出来了）
            continue
        r = by_day.get(d.replace("-", ""))
        if not r:
            continue
        tier, lo, hi, why = tier_of(r)
        mctx = margin_ctx(d)
        lo, hi, why = apply_modifiers(r, lo, hi, why, mctx)
        c = corpus[d]
        stated = c.get("stated")
        op = ours.get(d.replace("-", ""))
        ok = ""
        if stated:
            n_cmp += 1
            # 一致：他说仓位落在我们区间内（或区间与他给的重叠）
            if stated[0] <= hi + 0.05 and stated[1] >= lo - 0.05:
                ok = "✓"
                n_ok += 1
            else:
                ok = "✗"
        print("%s %8.1f %7.1f %7.1f %7.1f  %-5s %6.2f %4d %6s %-10s %3.0f%%-%-3.0f%% %-9s %-8s %s"
              % (d, r["close"], r["ma5"], r["ma20"], r["ma60"], r["above_ma5"], r["ma5_slope5"],
                 r["days_below_ma5"], ("%.0f%%" % r["amt_pct"]) if r["amt_pct"] is not None else "-",
                 tier, lo * 100, hi * 100,
                 ("%.0f-%.0f%%" % (stated[0] * 100, stated[1] * 100)) if stated else "-",
                 ("%.1f%%" % op) if op is not None else "-", ok))
        lines_out.append({"day": d, "tier": tier, "band": [lo, hi], "why": why,
                          "stated": stated, "quotes": c["quotes"][:3], "ours_pct": op,
                          "close": r["close"], "ma5": r["ma5"], "ma60": r["ma60"],
                          "amt_pct": r["amt_pct"], "days_below_ma5": r["days_below_ma5"]})
    print("== 对齐率：%d/%d = %.0f%%（他有明确仓位说法的日子）" % (n_ok, n_cmp, (n_ok / n_cmp * 100) if n_cmp else 0))
    print("== 不一致的日子（需要补输入或改判据）：")
    for x in lines_out:
        if x["stated"] and not (x["stated"][0] <= x["band"][1] + 0.05 and x["stated"][1] >= x["band"][0] - 0.05):
            print("   %s 我们判 %s(%.0f-%.0f%%) 他说 %.0f-%.0f%% ｜ %s"
                  % (x["day"], x["tier"], x["band"][0] * 100, x["band"][1] * 100,
                     x["stated"][0] * 100, x["stated"][1] * 100, (x["quotes"] or ["-"])[0][:90]))
    out = os.path.join(REPO, ".dsh-tmp", "wolfbt", "tier_corpus_check.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(lines_out, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("== 明细已落盘：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
