# -*- coding: utf-8 -*-
"""scan_tbuild_params.py — `t_build.BUILD_PARAMS_DEFAULT` 建仓参数 × 语料检索（总账 §43，round 33）。

④ 类载体（字典型默认值）首判：只覆盖「买什么/何时买/买多少」三组里**可能有他原话**的键 ——
时机窗（quiet_end / afternoon_ban_from / drawdown_min_pct / vol_ratio_max）、
频率与并发（batch_per_symbol_per_day / max_daily_auto / max_symbols_being_built）、
体量（single_order_pct / per_symbol_cap / total_floor_cap）。
评分门槛组（cand_score_min 等）他**没有评分体系** → 不检索，直接按自设备案。

只做定位，判读逐条精读；语料 = 仓库根 xlsx（不手打）。

用法::

    WOLF_XLSX='狼大回复汇总 20260814-1457&往期.xlsx' .venv/bin/python jobs/scan_tbuild_params.py
    WOLF_XLSX='...' .venv/bin/python jobs/scan_tbuild_params.py 2025-01-01   # 只看当前时代
"""
import os
import re
import sys

import pandas as pd

XLSX = os.getenv("WOLF_XLSX", "/app/狼大回复汇总 20260814-1457&往期.xlsx")

GROUPS = [
    ("A1 早盘不追 / 开盘先观察（quiet_end=09:45）",
     re.compile(r"(开盘|早盘|9[:：]3|九点半|半小时|前半小时).{0,26}(不追|不买|别买|先看|观察|等|冷静|不动|别动)")),
    ("A2 午后不新开 / 下午再看（afternoon_ban_from=13:00）",
     re.compile(r"(下午|午后|13[:：]|1点半|两点).{0,26}(不买|不建|不新开|不追|再说|先看|看情况|不动|接回)")),
    ("A3 回踩才买 / 回踩幅度（drawdown_min_pct=1.0）",
     re.compile(r"(回踩|压回|回调|回落).{0,20}([0-9]+(\.[0-9]+)?)\s*(个点|%|％|毛|块)")),
    ("A4 放量不追 / 量比（vol_ratio_max=2.0）",
     re.compile(r"(放量|量比|爆量).{0,24}(不追|别追|不买|谨慎|小心|等|缩量)")),
    ("B1 一天买几只/几笔（max_daily_auto=3）",
     re.compile(r"(今天|当日|一天).{0,12}(买|建|开|加).{0,12}([0-9一二三四五六七八九十]+)\s*(只|笔|次|个)")),
    ("B2 同时持有/在途几只（max_symbols_being_built=5）",
     re.compile(r"(同时|一起|手上|手里|持仓).{0,14}([0-9一二三四五六七八九十]{1,2})\s*[只个]")),
    ("C1 单票占比（single_order_pct / per_symbol_cap）",
     re.compile(r"(单票|一只票|个股).{0,20}([0-9]{1,2})\s*(%|％)")),
    ("C2 底仓/总仓分档（total_floor_cap 0.4–0.7）",
     re.compile(r"(底仓|总仓|仓位).{0,18}([0-9]{2})\s*(%|％)")),
]


def load_posts(since=""):
    xl = pd.ExcelFile(XLSX)
    out = []
    for sh in xl.sheet_names:
        df = xl.parse(sh, header=None, dtype=str)
        for _, r in df.iterrows():
            m = re.match(r"(\d{4}-\d{2}-\d{2})", str(r.get(0) or ""))
            d8 = m.group(1) if m else ""
            if since and d8 and d8 < since:
                continue
            for c in list(df.columns)[1:]:
                v = r.get(c)
                if isinstance(v, str) and len(v.strip()) >= 12 and "[quote]" not in v:
                    out.append((d8, v.strip()))
    return out


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else ""
    posts = load_posts(since)
    print("原帖（不含引用块%s）: %d" % ("，since " + since if since else "", len(posts)))
    for name, rx in GROUPS:
        hits = sorted((d, t) for d, t in posts if rx.search(t))
        print("\n===== %s：%d 条 =====" % (name, len(hits)))
        for d, t in hits[:8]:
            print("· [%s] %s" % (d or "?", t[:220].replace("\n", " ⏎ ")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
