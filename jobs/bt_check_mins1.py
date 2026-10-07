# -*- coding: utf-8 -*-
"""bt_check_mins1.py — `data/_bt_full/mins1/` 的**体检 + 1min→5min 聚合一致性**（只读缓存，不联网）。

四段报告：
  ① **库存**：文件数 / bar 数 / 占用 / 覆盖的 (代码,日) / 逐日覆盖（回放每天要多少标的、有几只有 1min）。
  ② **脏数据**：上游 `stk_mins` 1min 实测有三种脏（见 `bt_fetch_mins1.py` 头注释）——
       · 同 `trade_time` 重复行（实测值完全相同，去重即可；482 根=241×2）；
       · 盘中之外的行：09:25~09:29（盘前）与 15:01~15:30+（**把 15:00 的快照重复贴了几十行**）；
       · 11:31~12:59 午休行。
     本段给出"每类脏影响多少文件 / 多少行 / 多少量"，作为替身层清洗口径的依据。
  ③ **聚合一致性**：把 1min 按**上游 5min 口径**聚合，与既有 5min 缓存（`data/_bt_full/mins/`）逐 bar 比。
     上游 5min 口径（实测钉死，见下）：
       · `09:30` 单独一根（= 09:30 那一分钟）；
       · 其余每分钟归到"**>= 该分钟的第一个 5min 刻度**"（09:31~09:35 → 09:35；11:26~11:30 → 11:30；
         13:01~13:05 → 13:05；14:56~15:00 → 15:00）→ 一天 **49 根**。
     既有缓存的 5min 文件**形状不齐**（49 / 48 / 52 / 22 …，见 `bt_pack_mins.py` 的"同槽多行"注释），
     本段按"既有文件根数"分组统计，避免把"上游口径不同"误判成"我们聚合错"。
  ④ **指数**：`t_regime.INDEX_SYMBOLS`（hs300/sh/sz）+ 扩展指数，逐日的 1min/5min 有无矩阵。

用法：
  python jobs/bt_check_mins1.py                        # 全量（~1 分钟）
  python jobs/bt_check_mins1.py --json data/_bt_full/mins1/_check.json
  python jobs/bt_check_mins1.py --sample 200           # 聚合对比只抽 200 对（更快）
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

sys.path[:0] = []
import bt_env  # noqa: E402
bt_env.add_paths()

REPO = bt_env.REPO
DEFAULT_MINS1 = os.path.join(REPO, "data", "_bt_full", "mins1")
DEFAULT_LEGACY = os.path.join(REPO, "data", "_bt_full", "mins")

# 5min 结束刻度（上游口径）
LABELS = ["09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:10", "10:15", "10:20",
          "10:25", "10:30", "10:35", "10:40", "10:45", "10:50", "10:55", "11:00", "11:05", "11:10",
          "11:15", "11:20", "11:25", "11:30", "13:05", "13:10", "13:15", "13:20", "13:25", "13:30",
          "13:35", "13:40", "13:45", "13:50", "13:55", "14:00", "14:05", "14:10", "14:15", "14:20",
          "14:25", "14:30", "14:35", "14:40", "14:45", "14:50", "14:55", "15:00"]
INDEXES = ["000300.SH", "000001.SH", "399001.SZ", "000905.SH", "000852.SH",
           "399006.SZ", "000688.SH", "000016.SH"]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _key(path):
    a = os.path.basename(path)[:-5].split("_")
    return a[0] + "_" + a[1], a[2], a[3]          # (code_EX, freq, day)


def slot_of(hm: str):
    """上游 5min 口径：09:30 单独；其余 → 第一个 >= 的刻度。"""
    if hm == "09:30":
        return "09:30"
    if "13:00" <= hm <= "13:01":
        return "13:05"
    for L in LABELS:
        if hm <= L:
            return L
    return None


def load_1min(path):
    """→ (bars, dirt)：bars 已「去重 + 只留盘中」；dirt 记原始脏量。"""
    d = json.load(open(path, encoding="utf-8"))
    raw = d.get("bars") or []
    seen, clean, out_of, dup = set(), [], 0, 0
    for b in raw:
        t = str(b[1])
        hm = t[11:16]
        if not (("09:30" <= hm <= "11:30") or ("13:00" <= hm <= "15:00")):
            out_of += 1
            continue
        if hm in seen:
            dup += 1
            continue
        seen.add(hm)
        c = _num(b[5])
        if c is None:
            continue
        o, h, l = _num(b[2]), _num(b[3]), _num(b[4])
        clean.append({"hm": hm, "open": o if o is not None else c, "high": h if h is not None else c,
                      "low": l if l is not None else c, "close": c,
                      "vol": _num(b[6]) or 0.0, "amount": _num(b[7]) or 0.0})
    clean.sort(key=lambda x: x["hm"])
    return clean, {"raw": len(raw), "dup": dup, "out_of_session": out_of}


def load_5min(path):
    d = json.load(open(path, encoding="utf-8"))
    out = {}
    for b in d.get("bars") or []:
        t = str(b[1])
        hm = t[11:16]
        c = _num(b[5])
        if c is None:
            continue
        o, h, l = _num(b[2]), _num(b[3]), _num(b[4])
        out[hm] = [o if o is not None else c, h if h is not None else c, l if l is not None else c,
                   c, _num(b[6]) or 0.0, _num(b[7]) or 0.0]
    return out


def agg_5min(bars):
    buck = collections.OrderedDict()
    for b in bars:
        s = slot_of(b["hm"])
        if s:
            buck.setdefault(s, []).append(b)
    out = {}
    for s, g in buck.items():
        out[s] = [g[0]["open"], max(x["high"] for x in g), min(x["low"] for x in g),
                  g[-1]["close"], sum(x["vol"] for x in g), sum(x["amount"] for x in g)]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mins1", default=DEFAULT_MINS1)
    ap.add_argument("--legacy", default=DEFAULT_LEGACY)
    ap.add_argument("--index", default="")
    ap.add_argument("--sample", type=int, default=0, help="聚合对比抽样对数（0=全部）")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    m1dir, lgdir = os.path.abspath(a.mins1), os.path.abspath(a.legacy)
    idx_file = a.index or os.path.join(m1dir, "_idx5", "")   # 兼容旧布局
    rep = {}

    # ── ① 库存 ─────────────────────────────────────────────────────
    f1 = sorted(glob.glob(os.path.join(m1dir, "*_1min_*.json")))
    f5 = sorted(glob.glob(os.path.join(m1dir, "*_5min_*.json")))
    nbar1 = sum(len(json.load(open(p, encoding="utf-8")).get("bars") or []) for p in f1)
    nbar5 = sum(len(json.load(open(p, encoding="utf-8")).get("bars") or []) for p in f5)
    size = sum(os.path.getsize(p) for p in f1 + f5)
    p1 = {_key(p): p for p in f1}
    p5 = {_key(p): p for p in f5}
    days1 = collections.Counter(k[2] for k in p1)
    codes1 = {k[0] for k in p1}
    codes5 = {k[0] for k in p5}
    print("① 库存")
    print("  1min 文件 %d（%d bar） / 5min 文件 %d（%d bar） / 占用 %.1f MB"
          % (len(f1), nbar1, len(f5), nbar5, size / 1048576.0))
    print("  1min 覆盖 %d 个代码 × %d 个交易日（日均 %.1f 个代码）"
          % (len(codes1), len(days1), (sum(days1.values()) / max(1, len(days1)))))
    print("  5min（本目录内，全部为指数）：%s" % sorted(codes5))
    rep["inventory"] = {"files_1min": len(f1), "bars_1min": nbar1, "files_5min": len(f5),
                        "bars_5min": nbar5, "bytes": size, "codes_1min": len(codes1),
                        "days_1min": len(days1)}

    # ── ② 脏数据 ───────────────────────────────────────────────────
    dirt = collections.Counter()
    dirty_files = []
    for p in f1:
        raw = json.load(open(p, encoding="utf-8")).get("bars") or []
        hms = [str(b[1])[11:16] for b in raw]
        dup = len(hms) - len(set(hms))
        oos = sum(1 for h in hms if not (("09:30" <= h <= "11:30") or ("13:00" <= h <= "15:00")))
        uniq = len(set(hms))
        if dup or oos or uniq != 241:
            dirty_files.append((os.path.basename(p), len(raw), uniq, dup, oos))
        if dup:
            dirt["files_dup"] += 1
        if oos:
            dirt["files_out_of_session"] += 1
        if uniq > 241:
            dirt["files_uniq_gt241"] += 1
        elif uniq < 241:
            dirt["files_uniq_lt241"] += 1
        else:
            dirt["files_clean_241"] += 1
    print("② 脏数据（上游 stk_mins 1min 的三种脏）")
    print("  %s" % dict(dirt))
    print("  样例（文件, 原始行, 唯一分钟, 重复行, 盘外行）：%s" % dirty_files[:5])
    rep["dirt"] = {"counts": dict(dirt), "samples": dirty_files[:30]}

    # ── ③ 聚合一致性 ───────────────────────────────────────────────
    # 注意：p1 的 key 带 freq=1min、既有缓存带 5min → 必须**按 (code, day) 对齐**（曾经写成
    # set(p1) & set(lgf) → 频率不同 → 恒 0 对，静默给出"0 对"的假结论）
    lgf = {(_key(p)[0], _key(p)[2]): p for p in glob.glob(os.path.join(lgdir, "*_5min_*.json"))}
    p1d = {(_key(p)[0], _key(p)[2]): p for p in f1}
    pairs = sorted(set(p1d) & set(lgf))
    if a.sample:
        pairs = pairs[::max(1, len(pairs) // a.sample)][:a.sample]
    res = collections.Counter()
    shape = collections.defaultdict(collections.Counter)
    by_kind = collections.defaultdict(collections.Counter)
    samples = []
    for k in pairs:
        code, day = k
        kind = "index" if code.replace("_", ".") in INDEXES else "stock"
        b1, _d = load_1min(p1d[k])
        l5 = load_5min(lgf[k])
        g = agg_5min(b1)
        # ⚠️ 分类必须**互斥**：先判"六字段全等"，再判"前五字段等、仅 amount 差"，否则
        #    `ex + vx` 会把 exact 的文件重复计入 → 把"完全一致"误判成"不匹配"（踩过一次）
        ex = vx = tx = rd = miss = 0
        for s, v in g.items():
            y = l5.get(s)
            if y is None:
                miss += 1
                continue
            if all(abs(v[i] - y[i]) < 1e-9 for i in range(6)):
                ex += 1
            elif all(abs(v[i] - y[i]) < 1e-9 for i in (0, 1, 2, 3, 4)):   # vol 等、仅 amount 尾数
                vx += 1
            elif all(abs(v[i] - y[i]) <= 1e-6 * max(1.0, abs(y[i])) for i in range(6)):
                tx += 1
            else:
                rd += 1
                if len(samples) < 6:
                    samples.append((code + " " + day, s, [round(x, 2) for x in v], [round(x, 2) for x in y]))
        res["pairs"] += 1
        res["slots"] += len(g)
        res["exact_all6"] += ex
        res["vol_exact_amount_rounding"] += vx
        res["tiny_diff"] += tx
        res["real_diff"] += rd
        res["slot_missing"] += miss
        # 上游对 5min 的 vol/amount 有 ±1 股 / ±几十元的**聚合尾数差**（实测 000021_SZ 20260130
        # 13:35：我们 20,862,703 vs 上游 20,862,704）→ 单独归 "near"，别混进 "differ" 当口径错误
        _aligned = (len(g) == len(l5) and not miss)
        _cls = ("all_exact" if (ex == len(g) and _aligned) else
                ("vol_exact_amount_diff" if (ex + vx == len(g) and _aligned) else
                 ("near_rounding" if (ex + vx + tx == len(g) and _aligned) else "differ")))
        shape[(len(l5), len(g))][_cls] += 1
        by_kind[kind]["pairs"] += 1
        by_kind[kind][_cls] += 1
    print("③ 1min→5min 聚合 vs 既有 5min 缓存（%d 对）" % res["pairs"])
    print("  aggregate（口径：09:30 单独 + 归到 >= 的第一个刻度 → 49 根/日）")
    print("  slots=%d  六字段完全一致 %d（%.1f%%）  vol 一致/amount 尾数差 %d（%.1f%%）  微小差 %d  真差异 %d  既有缺槽 %d"
          % (res["slots"], res["exact_all6"], 100.0 * res["exact_all6"] / max(1, res["slots"]),
             res["vol_exact_amount_rounding"],
             100.0 * res["vol_exact_amount_rounding"] / max(1, res["slots"]),
             res["tiny_diff"], res["real_diff"], res["slot_missing"]))
    print("  按『既有 5min 根数』分组：")
    for s, c in sorted(shape.items()):
        print("     %s → %s" % (s, dict(c)))
    print("  按标的类别：%s" % {k: dict(v) for k, v in by_kind.items()})
    print("  真差异样例：%s" % samples[:3])
    rep["agg"] = {"counts": dict(res), "shape": {"%s" % (k,): dict(v) for k, v in shape.items()},
                  "by_kind": {k: dict(v) for k, v in by_kind.items()}, "samples": samples[:20]}

    # ── ③b 同源对照：1min 聚合 vs **本目录内新抓的 `_5min_` 文件**（指数） ──
    #     比 ③ 更干净：③ 的既有 5min 缓存形状不齐（48 根=floor 口径 / 52~58 根=含重复行 / 指数只有 close），
    #     这里两边都来自同一上游、同一天 → 差异只可能来自"我们的聚合口径"。
    pairsB = sorted({(_key(p)[0], _key(p)[2]) for p in f1} & {(_key(p)[0], _key(p)[2]) for p in f5})
    b_ex = b_vx = b_tx = b_rd = b_deg = 0
    for code, day in pairsB:
        p1 = next(q for q in f1 if _key(q)[0] == code and _key(q)[2] == day)
        p5 = next(q for q in f5 if _key(q)[0] == code and _key(q)[2] == day)
        b1, _ = load_1min(p1)
        y = load_5min(p5)
        if not y:
            continue
        if all((v[4] or 0) == 0 for v in y.values()):      # 兜底来的 close-only 5min → 不可比
            b_deg += 1
            continue
        g = agg_5min(b1)
        for sname, v in g.items():
            r = y.get(sname)
            if r is None:
                continue
            if all(abs(v[i] - r[i]) < 1e-9 for i in range(6)):
                b_ex += 1
            elif all(abs(v[i] - r[i]) < 1e-9 for i in range(5)):
                b_vx += 1
            elif all(abs(v[i] - r[i]) <= 1e-6 * max(1.0, abs(r[i])) for i in range(6)):
                b_tx += 1
            else:
                b_rd += 1
    tot_b = b_ex + b_vx + b_tx + b_rd
    print("③b 同源对照（1min 聚合 vs 新抓 `_5min_`，指数）：slots=%d 六字段全等 %d（%.1f%%）"
          " vol 等/amount 尾数差 %d（%.1f%%） 微小差 %d 真差异 %d（跳过 close-only 文件 %d 个）"
          % (tot_b, b_ex, 100.0 * b_ex / max(1, tot_b), b_vx, 100.0 * b_vx / max(1, tot_b),
             b_tx, b_rd, b_deg))
    rep["agg_same_source"] = {"slots": tot_b, "exact": b_ex, "vol_exact": b_vx,
                              "tiny": b_tx, "real_diff": b_rd, "skipped_close_only": b_deg}

    # ── ④ 指数矩阵 ─────────────────────────────────────────────────
    print("④ 指数（1min / 5min 覆盖的交易日数）")
    idx_rep = {}
    for ts in INDEXES:
        code = ts.replace(".", "_")
        d1 = sorted(k[2] for k in p1 if k[0] == code)
        d5 = sorted(k[2] for k in p5 if k[0] == code)
        idx_rep[ts] = {"1min_days": len(d1), "5min_days": len(d5),
                       "1min_days_list": d1, "5min_days_list": d5}
        print("  %-11s 1min %3d 天  5min %3d 天（1min 首/末 %s/%s）"
              % (ts, len(d1), len(d5), d1[0] if d1 else "-", d1[-1] if d1 else "-"))
    rep["index"] = idx_rep

    # ── ⑤ 计划完成度 ───────────────────────────────────────────────
    plan_p = os.path.join(m1dir, "_plan.json")
    if os.path.exists(plan_p):
        plan = json.load(open(plan_p, encoding="utf-8"))
        stats_p = os.path.join(m1dir, "_stats.json")
        st = json.load(open(stats_p, encoding="utf-8")) if os.path.exists(stats_p) else {}
        print("⑤ 抓取计划：%s" % st)
        rep["stats"] = st
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print("[check] 报告已写 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
