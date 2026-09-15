# -*- coding: utf-8 -*-
"""extract_xlsx_themes.py — 5 个主题的**定向抽取**（2026-09-15 round 16）。

背景：round 15 的缺口扫描（总账 §23）在 md 未覆盖的 62% 里找到 8 条带日期的买入侧材料，
集中在 5 个主题。本脚本在**原始 xlsx 全表**（含发帖时间列）里按主题检索，
产出"每个主题的全部相关原话（带日期）"，供逐条精读与入账。

5 个主题（来自 §23）：
  ① BOLL 轨补/减     ② 日内 −2% 触发买入     ③ 抄底仓位与持有期
  ④ 缩量/白线在上不加仓 + 仓位上限           ⑤ 13/34 线买点（含"击不穿也是买点"）

口径：他本人（排除 `[quote]` 楼层）、xlsx 全部 sheet；命中即记（不做语义过滤，交给精读）。
用法::

    .venv/bin/python jobs/extract_xlsx_themes.py --theme boll --limit 40
    .venv/bin/python jobs/extract_xlsx_themes.py --all --json .dsh-tmp/buyside/xlsx_theme_evidence.json
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_XLSX = os.path.join(ROOT, "狼大回复汇总 20260814-1457&往期.xlsx")
OUT = os.path.join(ROOT, ".dsh-tmp", "buyside", "xlsx_theme_evidence.json")

THEMES = {
    "boll": {
        "desc": "BOLL 轨：中轨补 / 上轨减",
        "any": ("中轨", "上轨", "下轨", "BOLL", "boll", "布林"),
    },
    "intraday_dip2": {
        "desc": "日内 −2% 触发买入",
        "any": ("低过-2", "低过 -2", "跌过-2", "跌破-2", "跌了-2", "-2%", "-2个点", "-2 个点",
                "跌2个点", "跌两个点", "跌了2个点", "跌2个多点"),
        "ctx": ("买", "进", "抄", "补", "低吸"),
    },
    "dip_position": {
        "desc": "抄底仓位与持有期",
        "any": ("抄底",),
        "ctx": ("仓位", "成仓", "%", "％", "两天", "3天", "三天", "做个", "拿着"),
    },
    "no_add_shrink": {
        "desc": "缩量/白线在上不加仓 + 仓位上限",
        "any": ("缩量", "白线在上", "白线高于黄线"),
        "ctx": ("加仓", "别加", "不要加", "减", "仓位"),
    },
    "gap_up_nochase": {
        "desc": "高开不追（C4）：高开/缺口 + 是否追",
        "any": ("高开", "跳空", "缺口"),
        "ctx": ("不追", "别追", "追高", "不要买", "不买", "减", "卖"),
    },
    "step_refill": {
        "desc": "分步/一步一步/回补（C6）",
        "any": ("一步一步", "分步", "分批", "回补", "补回", "再买回", "慢慢买"),
        "ctx": ("买", "补", "进", "仓"),
    },
    "ma_line_buy": {
        "desc": "13/34 线买点（含击不穿也是买点）",
        "any": ("13天线", "34天线", "13日线", "34日线", "13线", "34线", "挂线上", "压回到"),
        "ctx": ("买", "挂", "建仓", "补", "进"),
    },
}


def load_posts(xlsx):
    import pandas as pd
    xl = pd.ExcelFile(xlsx)
    out = []
    for sh in xl.sheet_names:
        df = xl.parse(sh, header=None, dtype=str)
        for _, r in df.iterrows():
            d8 = ""
            m = re.match(r"(\d{4}-\d{2}-\d{2})", str(r.get(0) or ""))
            if m:
                d8 = m.group(1)
            for c in list(df.columns)[1:]:
                v = r.get(c)
                if isinstance(v, str) and len(v.strip()) >= 24 and "[quote]" not in v:
                    out.append({"sheet": sh, "date": d8, "text": v})
    return out


def sentences(text):
    return [s.strip() for s in re.split(r"[。！？!?；;\n]+", text) if len(s.strip()) >= 8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=DEFAULT_XLSX)
    ap.add_argument("--theme", default="", help="逗号分隔：%s；留空则 --all" % ",".join(THEMES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--json", default=OUT)
    args = ap.parse_args()

    want = list(THEMES) if (args.all or not args.theme) else [t.strip() for t in args.theme.split(",") if t.strip()]
    posts = load_posts(args.xlsx)
    print("[theme] xlsx 帖子 %d 条（他本人、非引用、≥24 字）" % len(posts))

    res = {}
    for t in want:
        cfg = THEMES.get(t)
        if not cfg:
            print("  未知主题 %s" % t)
            continue
        hits, seen = [], set()
        for p in posts:
            for sent in sentences(p["text"]):
                if not any(k in sent for k in cfg["any"]):
                    continue
                if cfg.get("ctx") and not any(k in sent for k in cfg["ctx"]):
                    continue
                key = sent[:30]
                if key in seen:
                    continue
                seen.add(key)
                hits.append({"date": p["date"], "sheet": p["sheet"], "sentence": sent[:220]})
        res[t] = {"desc": cfg["desc"], "n": len(hits), "hits": hits}
        print("\n===== %s（%s）：命中 %d 条 =====" % (t, cfg["desc"], len(hits)))
        for h in hits[:args.limit]:
            print("   [%s] %s" % (h["date"] or h["sheet"], h["sentence"][:150]))
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    old = {}
    if os.path.exists(args.json):
        try:
            old = json.load(open(args.json, encoding="utf-8"))
        except Exception:
            old = {}
    old.update(res)
    json.dump(old, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[theme] 写出 %s（主题 %s）" % (args.json, ",".join(res)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
