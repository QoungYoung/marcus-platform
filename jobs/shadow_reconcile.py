# -*- coding: utf-8 -*-
"""shadow_reconcile.py — 「对齐后 vs 现状」逐日对账（影子产物汇总，2026-09-15 round 4）。

影子产物（都不改决策，只记录）：
  · `data/theme_volfund_shadow_<date>.json` —— 他称的「选板块第一要素」会拦哪些主题（P1 门）
  · `data/rank_v3_<as_of>.json`             —— v3 排序会选哪只 vs 现行 leader 实际选哪只
  · `data/dip_tol_shadow_<date>.json`       —— C1：254 按语料值(tol=0)会少成交哪些票

用法：.venv/bin/python jobs/shadow_reconcile.py [--dir <生产 data 目录的本地镜像>] [--date YYYYMMDD]
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("--date", default="")
    args = ap.parse_args()
    d = args.dir

    def pick(pattern, date=None):
        fs = sorted(glob.glob(os.path.join(d, pattern)))
        if date:
            fs = [f for f in fs if date in f]
        return fs[-1] if fs else None

    print("=" * 78)
    print("影子对账（只读；影子模式不改任何决策）  目录=%s" % d)
    print("=" * 78)

    f = pick("theme_volfund_shadow_*.json", args.date or None)
    print("\n【P1】他称的「选板块第一要素」会拦谁")
    if not f:
        print("   （无影子文件：需要 worker 跑过 rotation_switch_arm / theme_buyable）")
    else:
        j = _load(f) or {}
        th = j.get("themes") or {}
        blocked = {k: v.get("why") for k, v in th.items() if not v.get("pass")}
        allowed = [k for k, v in th.items() if v.get("pass")]
        print("   日期=%s  检查过的主题=%d  会拦=%d  会放行=%d" % (j.get("date"), len(th), len(blocked), len(allowed)))
        for k, why in blocked.items():
            print("     ✋ %-14s %s" % (k, str(why)[:90]))
        if allowed:
            print("     ✅ 放行：%s" % ", ".join(allowed))

    f = pick("rank_v3_*.json", args.date or None)
    print("\n【① v3 排序】会选什么 vs 现行 leader 实际选什么")
    if not f:
        print("   （无影子文件）")
    else:
        j = _load(f) or {}
        for theme, t in (j.get("themes") or {}).items():
            v3 = [x.get("symbol") for x in (t.get("v3") or [])]
            ldr = [x.get("symbol") for x in (t.get("leader") or [])]
            same = set(v3) == set(ldr)
            print("   %-12s mode=%-6s 主题分位=%-6s 域=%-3s v3=%-18s leader=%-18s %s"
                  % (theme, t.get("mode"), t.get("theme_r5_qtile"), t.get("domain_n"),
                     ",".join(v3) or "—", ",".join(ldr) or "—", "相同" if same else "**不同**"))

    f = pick("dip_tol_shadow_*.json", args.date or None)
    print("\n【C1】254 按语料值(tol=0)会「少成交」的票")
    if not f:
        print("   （无影子文件：需部署含影子的 t_monitor，见 docs/wolf-buy-parameter-ledger.md §11）")
    else:
        j = _load(f) or {}
        items = j.get("items") or {}
        print("   日期=%s  记录=%d 只" % (j.get("date"), len(items)))
        for sym, it in list(items.items())[:10]:
            print("     %-10s 前低=%-8s 今低=%-8s 差=%s%%  %s"
                  % (sym, it.get("prev_low"), it.get("today_low"), it.get("gap_pct"), it.get("note")))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
