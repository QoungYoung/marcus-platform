# -*- coding: utf-8 -*-
"""lowdip_block_report.py —— 「低吸腿**命中后被拦**」逐条留痕（B6a ✓ 用户 2026-10-03 拍板 ✓）

**为什么要有它** ✓（与 `jobs/leg_miss_report.py` 分工互补 ✓）：
  · `leg_miss_report` 只覆盖「**没触线**」✗（离触发线多远 / 触碰时量比 / 缺哪条条件）；
  · 实测：254（触前低+缩量）命中 **25 个 symbol-day**，其中只有 **7 个成交（28%）** ✗，
    其余全被闸拦下，而拦因**只散在 `prod_<day>.log` 的文本行里**（`blocked … 缓跌且未命中任何买点`、
    `rejected … 两分法：底仓已达 65% 目标` 等）⇒ 事后**没法按闸/按票/按天统计** ✗。
  ⇒ 本脚本把「命中 → 拦 / 成交」结构化落盘 ✓。

**⚠️ 当日归因必须用 `id` 增量** ✓：`_summary/prod_<day>.json` 的 `triggers` 是**累加**的
  （后一天的 id 集合完全包含前一天的）且**没有时间字段** ⇒ 只能用「今日 id − 上一份报告的 max_id」
  做当日归属（自包含 ✓，不依赖"上一天是哪天" ✓）。第一版没做 ⇒ 命中被逐日重复计数（459 ✗ → 25 ✓）。

**输入** ✓：`<summary>/prod_<day>.json`（当日）、本目录上一份 `lowdip_block_report_*.jsonl`（取 `max_id`）、
           `<sandbox>/legs_switch.jsonl` ∪ `legs.jsonl`（当日腿）、`<sandbox>/stock_confirm_result_prev0818.json`（当日候选域）
**输出** ✓：`<sandbox>/lowdip_block_report_<day>.jsonl`（首行 header 带 `max_id` ✓，之后一行一个命中标的 ✓）
**开关** ✓：`WOLF_LOWDIP_BLOCK_REPORT`（**默认 1 = 开** ✓；置 0 关 ✓）
**零影响** ✓：只读产物、只写自己的报告文件；不碰判据、不碰生产库、不碰 t_conditions ✓。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys


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
LOWDIP = ("custom_prevlow", "custom_m5dump")


def enabled() -> bool:
    return str(os.getenv("WOLF_LOWDIP_BLOCK_REPORT", "1")).strip().lower() in ("1", "true", "yes", "on")


def _code(x) -> str:
    s = str(x or "")
    return s[2:] if s[:2] in ("SH", "SZ", "BJ") else "".join(c for c in s if c.isdigit())[:6]


def fam(reason: str) -> str:
    """拦因归类（键 = 判据里的关键短语 ✓，改判据时这里要跟着改 ✓）。"""
    r = str(reason or "")
    for k, v in (("缓跌且", "缓跌闸 FALLING_V2（未命中任何买点）"),
                 ("两分法", "两分法：底仓已满 ⇒ 不再放行"),
                 ("加仓口径", "加仓口径/上限"),
                 ("下去不补", "下去不补（低于最近卖出价）"),
                 ("低吸额度", "低吸额度余额不足"),
                 ("HPV", "[HPV] 位置高 ∧ 前日放量"),
                 ("埋伏腿同日去重", "埋伏腿同日去重"),
                 ("做T时段窗", "做T时段窗"),
                 ("追高", "不追高"),
                 ("停手", "指数/风控停手"),
                 ("禁买", "禁买名单"),
                 ("ST", "卫生过滤 ST")):
        if k in r:
            return v
    return (re.sub(r"[\d\.]+", "#", r)[:34] or "（空）")


def ctx_from_reason(reason: str) -> dict:
    """把缓跌闸那串「距最近线 X%、距前低 Y%、近2根跌幅 Z%、量比 W」解出来（解析不到就不给 ✓）。"""
    out = {}
    r = str(reason or "")
    for key, pat in (("dist_line_pct", r"距最近线\s*(-?[\d\.]+)%"),
                     ("dist_prev_low_pct", r"距前低\s*(-?[\d\.]+)%"),
                     ("last2_drop_pct", r"近2根跌幅\s*(-?[\d\.]+)%"),
                     ("vol_ratio", r"量比\s*(-?[\d\.]+)")):
        m = re.search(pat, r)
        if m:
            try:
                out[key] = float(m.group(1))
            except Exception as _e_sil1:
                _silent_alert("lowdip_block_report.py:76", _e_sil1)
    return out


def last_max_id(sandbox: str) -> int:
    best = 0
    for p in glob.glob(os.path.join(sandbox, "lowdip_block_report_*.jsonl")):
        try:
            with open(p, encoding="utf-8") as f:
                first = f.readline().strip()
            if first:
                o = json.loads(first)
                if str(o.get("kind")) == "header":
                    best = max(best, int(o.get("max_id") or 0))
        except Exception as _e_sil2:
            _silent_alert("lowdip_block_report.py:91", _e_sil2)
            continue
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--sandbox", default="")
    ap.add_argument("--summary", default="", help="prod_<day>.json 所在目录（默认 <sandbox 的上一级>/_summary）")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if not enabled():
        print("  [lowdip_block] 关（WOLF_LOWDIP_BLOCK_REPORT=0）⇒ 跳过 ✓")
        return 0
    day = str(a.day).replace("-", "")
    sb = a.sandbox or os.path.join(REPO, "data", "_bt_t35", day)
    smy = a.summary or os.path.join(os.path.dirname(sb.rstrip("/")), "_summary")
    pp = os.path.join(smy, "prod_%s.json" % day)
    if not os.path.exists(pp):
        print("  [lowdip_block] 缺 %s ⇒ 跳过 ✓" % pp)
        return 0
    d = json.load(open(pp, encoding="utf-8"))
    since = last_max_id(sb)
    # 当日腿（stage 用来看是哪一档发的腿 ✓）
    legs = {}
    for fn in ("legs_switch.jsonl", "legs.jsonl"):
        p = os.path.join(sb, fn)
        if not os.path.exists(p):
            continue
        for ln in open(p, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception as _e_sil3:
                _silent_alert("lowdip_block_report.py:127", _e_sil3)
                continue
            if str(o.get("side") or "buy") != "buy":
                continue
            legs[_code(o.get("symbol") or o.get("code"))] = {"stage": o.get("stage"), "src": o.get("src") or fn}
    # 当日候选域
    cdom = set()
    cp = os.path.join(sb, "stock_confirm_result_prev0818.json")
    if os.path.exists(cp):
        try:
            for e in json.load(open(cp, encoding="utf-8")).values():
                for s in (e.get("stocks") or []):
                    cdom.add(_code(s.get("code")))
        except Exception as _e_sil4:
            _silent_alert("lowdip_block_report.py:140", _e_sil4)
    # 当日 armed
    armed = {_code(x.get("symbol")) for x in (d.get("armed") or []) if "253" in x or "254" in x}
    # 当日成交（trades 有 created_at ⇒ 可当日过滤 ✓）
    filled = set()
    for t in (d.get("trades") or []):
        if str(t.get("direction")) != "买入":
            continue
        if str(t.get("created_at") or "")[:10].replace("-", "") != day:
            continue
        if "prevlow" in str(t.get("reason") or "") or "m5dump" in str(t.get("reason") or ""):
            filled.add(_code(t.get("symbol")))
    # 当日命中（id 增量 ✓）
    hit = {}
    max_id = since
    for t in (d.get("triggers") or []):
        tid = t.get("id")
        if tid is not None:
            max_id = max(max_id, int(tid))
        if tid is None or int(tid) <= since:
            continue
        if str(t.get("event_type")) not in LOWDIP:
            continue
        c = _code(t.get("symbol"))
        e = hit.setdefault(c, {"kind": str(t.get("event_type")), "n": 0, "blocked": [], "executed": 0,
                               "prices": [], "reasons": []})
        e["n"] += 1
        q = t.get("quote_price")
        if q:
            try:
                e["prices"].append(float(q))
            except Exception as _e_sil5:
                _silent_alert("lowdip_block_report.py:172", _e_sil5)
        if str(t.get("status")) == "executed":
            e["executed"] += 1
        r = str(t.get("reason") or "")
        if r:
            e["blocked"].append(r)
            e["reasons"].append(fam(r))
    rows = []
    for c, e in sorted(hit.items()):
        ok = c in filled
        why = ""
        if not ok:
            import collections as _c
            why = _c.Counter(e["reasons"]).most_common(1)[0][0] if e["reasons"] else "（无理由文本）"
        rec = {
            "kind": "row", "day": day, "symbol": c,
            "leg_kind": e["kind"], "outcome": ("成交" if ok else "拦"),
            "block_family": why,
            "n_records": e["n"], "n_executed_records": e["executed"],
            "first_quote_price": (e["prices"][0] if e["prices"] else None),
            "min_quote_price": (min(e["prices"]) if e["prices"] else None),
            "in_candidate_domain": bool(c in cdom), "armed_today": bool(c in armed),
            "leg_stage": (legs.get(c) or {}).get("stage"), "leg_src": (legs.get(c) or {}).get("src"),
            "reason_first": (e["blocked"][0][:400] if e["blocked"] else ""),
            "reason_last": (e["blocked"][-1][:400] if e["blocked"] else ""),
            "ctx_first": ctx_from_reason(e["blocked"][0]) if e["blocked"] else {},
        }
        rows.append(rec)
    out = a.out or os.path.join(sb, "lowdip_block_report_%s.jsonl" % day)
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "header", "day": day, "since_id": since, "max_id": max_id,
                                "n_hit": len(rows), "n_blocked": sum(1 for r in rows if r["outcome"] == "拦"),
                                "n_filled": sum(1 for r in rows if r["outcome"] == "成交")},
                               ensure_ascii=False) + "\n")
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("  [lowdip_block] %s 命中 %d 个｜拦 %d｜成交 %d ⇒ %s"
              % (day, len(rows), sum(1 for r in rows if r["outcome"] == "拦"),
                 sum(1 for r in rows if r["outcome"] == "成交"), os.path.basename(out)))
    except Exception as e:
        print("  [lowdip_block] ✗ 写盘失败 %s" % str(e)[:70])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
