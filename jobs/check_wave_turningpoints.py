# -*- coding: utf-8 -*-
"""路线 C：用语料里"行情结束/尾段"类**实时声明**当 ground truth，检验现有判据能不能亮灯。

语料侧：docs/wolf-daily-log-*.md 中的拐点声明（他都是当天实时说的）。
判据侧（全部只用**当日及之前**的数据，无前视）：
  A. `apps/main_line/wave_level.judge_wave(date)` —— 确定性口径（均线/高低点/突破/回撤/动量）
  B. ③层 live 判定：`data/wave_state_*.json` 快照里 level == "down"（稀疏，只有 34 个点）
  C/D. 基线代理：收盘 < MA20 / 收盘 < MA60
命中口径：在 [d-5, d+5] 个交易日内首次亮灯 → 记 lead/lag（负数=提前）。
用法: .venv/bin/python jobs/check_wave_turningpoints.py
"""
import glob, json, os, re, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))
import pandas as pd

KW = ("行情结束", "尾段", "走完了", "见顶了", "结束今天", "大级别.*结束")
DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


def turning_points():
    """→ [(date8, quote, src_file)]，按文件结构解析日期。"""
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "docs", "wolf-daily-log-*.md"))):
        cur = None
        for ln, line in enumerate(open(p, encoding="utf-8"), 1):
            m = re.match(r"^##\s*(20\d{2}-\d{2}-\d{2})", line)
            if m:
                cur = m.group(1).replace("-", "")
                continue
            if not any(re.search(k, line) for k in KW):
                continue
            hit = DATE_RE.search(line)          # 表格行常带 （YYYY-MM-DD）
            d8 = hit.group(0).replace("-", "") if hit else cur
            if not d8 or not (line.strip().startswith(("-", "|", ">", "*"))):
                continue
            out.append((d8, line.strip()[:110], os.path.basename(p)))
    # 去重（同日同句）
    seen, uniq = set(), []
    for d8, q, f in out:
        if (d8, q[:40]) in seen:
            continue
        seen.add((d8, q[:40]))
        uniq.append((d8, q, f))
    return sorted(uniq)


def main():
    _long = os.path.join(ROOT, "data", "指数数据", "index_daily", "000001.SH.csv")
    if os.path.exists(_long):           # 2016→今（由本地 5 分钟聚 + 官方日线），见 jobs/build_index_daily_from_5min.py
        import csv as _csv
        rows = sorted((r["trade_date"].replace("-", ""), float(r["close"]))
                      for r in _csv.DictReader(open(_long, encoding="utf-8")))
    else:
        idx = json.load(open(os.path.join(ROOT, "data", "index_daily_000001.json"), encoding="utf-8"))
        rows = sorted((str(r["trade_date"]).replace("-", ""), float(r["close"])) for r in idx)
    days = [d for d, _ in rows]
    close = pd.Series([c for _, c in rows], index=pd.DatetimeIndex(
        [datetime.strptime(d, "%Y%m%d") for d in days]))

    import wave_level
    wave_level._load_sh = lambda: close                     # 绕开缺失的本地文件，喂同一份指数日线

    snaps = {}
    for p in glob.glob(os.path.join(ROOT, "data", "wave_state_*.json")):
        d = os.path.basename(p).replace("wave_state_", "").replace(".json", "").replace("-", "")
        try:
            j = json.load(open(p, encoding="utf-8"))
            snaps[d] = str(j.get("level") or "")
        except Exception:
            pass

    def ma_at(i, n):
        return sum(c for _, c in rows[i - n + 1:i + 1]) / n if i + 1 >= n else None

    pts = turning_points()
    print("语料拐点声明 %d 条（docs/wolf-daily-log-*.md）" % len(pts))
    print("%-10s %-14s %-16s %-10s %-16s %s" % ("日期", "wave_level", "③live快照", "<MA20", "<MA60", "原话"))
    stat = {"wave_level": [0, 0], "wave_non": [0, 0], "live": [0, 0], "ma20": [0, 0], "ma60": [0, 0]}
    tested = 0
    for d8, q, f in pts:
        if d8 not in days:
            near = [d for d in days if abs((datetime.strptime(d, "%Y%m%d") - datetime.strptime(d8, "%Y%m%d")).days) <= 3]
            if not near:
                continue
            d8 = near[0]
        i = days.index(d8)
        if i < 260:
            continue
        tested += 1
        lab = "?"
        try:
            lab = wave_level.judge_wave(datetime.strptime(d8, "%Y%m%d"))[0]
        except Exception as e:
            lab = "err:%s" % type(e).__name__
        live = snaps.get(d8, "-")
        # 命中判定：d-5..d+5 内首次亮灯
        def first_hit(pred):
            for k in range(-5, 6):
                j = i + k
                if 0 <= j < len(days) and pred(j):
                    return k
            return None
        wl_non = first_hit(lambda j: not (wave_level.judge_wave(datetime.strptime(days[j], "%Y%m%d"))[0] or "主升").startswith("主升")
                           if j >= 260 else False)
        wl_hit = first_hit(lambda j: "调整" in (wave_level.judge_wave(datetime.strptime(days[j], "%Y%m%d"))[0] or "")
                           if j >= 260 else False)
        live_hit = first_hit(lambda j: snaps.get(days[j]) == "down")
        ma20_hit = first_hit(lambda j: ma_at(j, 20) and rows[j][1] < ma_at(j, 20))
        ma60_hit = first_hit(lambda j: ma_at(j, 60) and rows[j][1] < ma_at(j, 60))
        stat.setdefault("wave_non", [0, 0])
        for key, h in (("wave_level", wl_hit), ("wave_non", wl_non), ("live", live_hit),
                       ("ma20", ma20_hit), ("ma60", ma60_hit)):
            stat[key][0] += 1 if h is not None else 0
            stat[key][1] += 1
        print("%-10s %-14s %-16s %-10s %-16s %s" % (
            d8, lab, live, ("%+d" % ma20_hit) if ma20_hit is not None else "miss",
            ("%+d" % ma60_hit) if ma60_hit is not None else "miss", q[:46]))
    print("\n可检验（指数历史 ≥260 日）%d 条；命中率（±5 交易日窗口内亮灯）：" % tested)
    for k, (h, n) in stat.items():
        print("  %-11s %s/%s = %.0f%%" % (k, h, n, 100 * h / n if n else 0))


if __name__ == "__main__":
    main()
