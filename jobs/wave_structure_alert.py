# -*- coding: utf-8 -*-
"""浪型**结构预警**（2026-10-21 ✓）—— 出现"明确的结构"就往 QQ 推一条 ✓。

为什么做这个（而不是"识别延伸浪"✗）：他 10-21 原话「**延伸浪的终结一定是一个明确的结构**」✓，
并给了四种：**底分 / 红三兵 / 岛反 / 低位顶分破位** ✓。这些是**纯图形判据** ✓（不需要数浪、
不需要度 ✗），所以**可以预警** ✓。

四种结构（判据事前声明 ✓ 不许看结果再改）：
  · 底分型（向上 ✓）：最近 3 根 K，中间低点最低 ∧ 今日收盘 > 3 根前那根收盘
  · 红三兵（向上 ✓）：连续 3 根阳线 ∧ 收盘逐日抬高
  · 岛反（向上 ✓）：前一根向下跳空 ∧ 今日向上跳空（缺口不重叠）
  · 低位顶分破位（向下 ✗）：近 4 根里出现顶分 ∧ 今日收盘 < 该顶分最低点 ∧ 距 60 日高 ≥ 5%（"低位"）

同时给上下文：距 60 日高/低、量比、20 日振幅、银行 5 日相对强弱（"权重被压"✓）、他说的"上方缺口"位置 ✓。

去重：`data/wave_alerts.json` 按 (日期, 指数, 结构) 记一次 ✓ —— 同一天同一结构只推一次 ✓。

用法::
    python jobs/wave_structure_alert.py --dry          # 只看今天有没有结构（不推送 ✓）
    python jobs/wave_structure_alert.py                # 有结构就推 QQ ✓
    python jobs/wave_structure_alert.py --stats        # 历史统计（各结构出现后 5/10 日如何 ✓ 带基线 ✓）
"""
import argparse
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tushare_relay import relay_query   # noqa: E402

DATA = os.path.join(os.environ.get("DATA_DIR", "").strip() or "data")
ALERTS = os.path.join(DATA, "wave_alerts.json")
INDEXES = [
    ("000001.SH", "上证指数"),
    ("399006.SZ", "创业板指"),
    ("000688.SH", "科创50"),
]
START = "20260101"
STAT_START = "20160101"     # ★ 统计用长窗口 ✓（预警用短窗口 ✓ 节省取数）
HOLD = (5, 10)


def load(code, start=None):
    df = relay_query("index_daily", ts_code=code, start_date=(start or START), end_date="20261031")
    rows = []
    for _, r in (df.iterrows() if df is not None else []):
        rows.append({"d": str(r["trade_date"]), "o": float(r["open"]), "h": float(r["high"]),
                     "l": float(r["low"]), "c": float(r["close"]), "amt": float(r.get("amount") or 0)})
    return sorted(rows, key=lambda x: x["d"])


def structures(rows, i):
    """→ [(kind, direction, note)] ；i 必须是已收盘的那根 ✓"""
    out = []
    if i < 4:
        return out
    a, b, c = rows[i - 2], rows[i - 1], rows[i]
    # 底分型（向上）
    if b["l"] < a["l"] and b["l"] < c["l"] and c["c"] > a["c"]:
        out.append(("底分型", "up", "低点 %.2f（跌破即失效 ✗）" % b["l"]))
    # 红三兵（向上）
    if all(x["c"] > x["o"] for x in (a, b, c)) and c["c"] > b["c"] > a["c"]:
        out.append(("红三兵", "up", "三连阳且收盘逐日抬高 ✓"))
    # 岛反（向上，罕见）
    if i >= 2 and rows[i - 1]["h"] < rows[i - 2]["l"] and c["l"] > rows[i - 1]["h"]:
        out.append(("岛反", "up", "先向下跳空、再向上跳空 ✓"))
    # 低位顶分破位（向下）
    for k in range(i - 3, i):
        if k - 1 < 0 or rows[k]["h"] > rows[k - 1]["h"] and rows[k]["h"] > rows[k + 1]["h"] and c["c"] < rows[k]["l"]:
            hi60 = max(x["h"] for x in rows[max(0, i - 59):i + 1])
            if hi60 and (c["c"] / hi60 - 1) * 100 <= -5.0:
                out.append(("低位顶分破位", "down", "跌破 %.2f（顶分低点 ✗）" % rows[k]["l"]))
            break
    return out


def bank_rel(rows, i, bk, brow):
    try:
        j = brow.index(rows[i]["d"])
    except ValueError:
        return None
    if i < 5 or j < 5:
        return None
    sh5 = (rows[i]["c"] / rows[i - 5]["c"] - 1) * 100
    bk5 = (bk[j]["c"] / bk[j - 5]["c"] - 1) * 100   # ★ brow 是日期串表 ✓ 取值要用 bk ✗
    return bk5 - sh5


def ctx(rows, i, rel):
    lo20, hi20 = min(x["l"] for x in rows[i - 19:i + 1]), max(x["h"] for x in rows[i - 19:i + 1])
    amt20 = statistics.mean([x["amt"] for x in rows[i - 20:i]])
    hi60 = max(x["h"] for x in rows[max(0, i - 59):i + 1])
    return ("20 日振幅 %.1f%% ｜ 量比 %.2fx ｜ 距 60 日高 %.1f%% ｜ 银行 5 日相对 %s"
            % ((hi20 / lo20 - 1) * 100 if lo20 else 0,
               rows[i]["amt"] / amt20 if amt20 else 0,
               (rows[i]["c"] / hi60 - 1) * 100 if hi60 else 0,
               ("%+.1f%%（权重被压 ✓）" % rel) if rel is not None and rel <= -1 else
               (("%+.1f%%" % rel) if rel is not None else "缺")))


def push(text):
    try:
        from core.qq_notifier import send_c2c_message
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[ws-alert] 推送跳过：未配置 QQ_BOT_RECIPIENT ✗")
            return False
        ok = True
        for i in range(0, len(text), 1100):
            ok = bool(send_c2c_message(openid, text[i:i + 1100])) and ok
        print("[ws-alert] 推送%s" % ("成功 ✓" if ok else "失败 ✗"))
        return ok
    except Exception as e:
        print("[ws-alert] 推送异常: %s" % str(e)[:120])
        return False


def seen_load():
    try:
        return json.load(open(ALERTS, encoding="utf-8"))
    except Exception:
        return {}


def seen_save(d):
    try:
        os.makedirs(DATA, exist_ok=True)
        json.dump(d, open(ALERTS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[ws-alert] 存档失败: %s" % str(e)[:80])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印，不推送 ✓")
    ap.add_argument("--force", action="store_true", help="忽略去重（重推 ✓）")
    ap.add_argument("--stats", action="store_true", help="历史统计（各结构出现后 5/10 日 ✓）")
    args = ap.parse_args()

    sh = load("000001.SH")
    if len(sh) < 65:
        print("数据不足 ✗ %d" % len(sh))
        return 1
    bk = load("399986.SZ")
    bmap, brow = {}, [x["d"] for x in bk]

    if args.stats:
        pool = [("000001.SH", "上证"), ("399006.SZ", "创业板"), ("000688.SH", "科创50")]
        print("历史统计（多指数合并 ✓ %s 起 ✓）—— 判据与线上一致 ✓，含基线 ✓" % STAT_START)
        agg = {}          # kind -> [excess returns]  （超额 = 结构日之后收益 - 同日全样本均值 ✓）
        base_all = []
        for code, name in pool:
            rows = load(code, STAT_START)
            if len(rows) < 80:
                print("  %s 数据不足 ✗ %d" % (name, len(rows)))
                continue
            base_idx = list(range(25, len(rows) - max(HOLD)))
            rets = {n: {i: (rows[i + n]["c"] / rows[i]["c"] - 1) * 100 for i in base_idx} for n in HOLD}
            for n in HOLD:
                base_all.extend(rets[n].values())
            kinds = {}
            for i in base_idx:
                for kind, _d, _n in structures(rows, i):
                    kinds.setdefault(kind, []).append(i)
            for kind, idxs in kinds.items():
                for n in HOLD:
                    b = statistics.mean(rets[n].values())
                    agg.setdefault((kind, n), []).extend([rets[n][i] for i in idxs])
            print("  %s：%d 根 ✓ %s ~ %s（结构日 %s）" % (
                name, len(rows), rows[0]["d"], rows[-1]["d"],
                {k: len(v) for k, v in sorted(kinds.items())}))
        print()
        for n in HOLD:
            b = [x for x in base_all if x == x]
            print("【%d 日后】基线：均值 %+.2f%% ｜ 上涨占比 %.0f%%（n=%d）" % (
                n, statistics.mean(b), 100.0 * sum(1 for x in b if x > 0) / len(b), len(b)))
            for (kind, nn), rs in sorted(agg.items()):
                if nn != n or not rs:
                    continue
                up = 100.0 * sum(1 for x in rs if x > 0) / len(rs)
                base_up = 100.0 * sum(1 for x in b if x > 0) / len(b)
                print("  %-12s n=%-4d 均值 %+.2f%% ｜ 上涨 %.0f%% ｜ 差基线 %+.2f%% ｜ %s" % (
                    kind, len(rs), statistics.mean(rs), up, up - base_up,
                    "★ 有效（同向且幅度差 ≥3%%）" if (up - base_up >= 3 and statistics.mean(rs) > 0)
                    else ("✗ 反向" if up - base_up <= -3 else "≈ 基线 ✓ 无优势")))
        return 0

    # ── 三个指数逐个检查 ✓（原来只查了上证 ✗ 2026-10-21 修）──
    bk = load("399986.SZ")
    brow = [x["d"] for x in bk]
    hits, new, seen = [], 0, seen_load()
    today = ""
    for code, name in INDEXES:
        rows = sh if code == "000001.SH" else load(code)
        if len(rows) < 65:
            print("  %s 数据不足 ✗ %d" % (name, len(rows)))
            continue
        today = rows[-1]["d"]
        found = structures(rows, len(rows) - 1)
        print("%s %s 收 %.2f ｜ %d 个结构" % (name, rows[-1]["d"], rows[-1]["c"], len(found)))
        for kind, direction, note in found:
            hits.append((name, kind, direction, note))
            key = "%s|%s|%s" % (rows[-1]["d"], code, kind)
            if key not in seen or args.force:
                seen[key] = 1
                new += 1

    if not hits:
        print("今天三个指数都没有结构 ✓（他的口径：结构没出 ⇒ 这一段还没结束，继续观察 ✓）")
        return 0

    # ★ 推送正文**只有三行结构** ✓（统计/上下文只进日志 ✓ —— 2026-10-21「提示就该短」✓）
    out = ["🐺 结构提示（%s）" % today]
    for name, kind, direction, note in hits:
        out.append("· %s %s：%s —— %s" % (name, "▲" if direction == "up" else "▼", kind, note))
    out.append("（提示而已 ✓ 不是买卖信号 ✗）")
    text = "\n".join(out)
    print("\n" + text)
    print("\n上下文（只进日志 ✓）：%s" % ctx(sh, len(sh) - 1, bank_rel(sh, len(sh) - 1, bk, brow)))
    if args.dry:
        print("\n（--dry ✓ 不推送）")
        return 0
    if new == 0:
        print("\n这些结构今天已推过 ✓（去重）")
        return 0
    push(text)
    seen_save(seen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
