# -*- coding: utf-8 -*-
"""bt_legs_truth.py — 从**生产调度器日志**抽取逐日「布腿真值」（全窗口，不依赖 DB 保留期）。

为什么需要它：`t_conditions` 只保留近期（实测最早 2026-08-15），而回测窗口要到 2026-06-03；
但生产每天把**每个任务的完整输出**写进 `logs/scheduler_<date>.jsonl`（从 2026-06-03 起每天一份），
其中就包含两条布腿路径的全部产腿信息 → 可重建逐日真值。

两条布腿路径（都写 `t_conditions.publisher='switch'`）：
  ① 08:18 `tranche_ladder_report`（`SWITCH_AUTO_EXEC=1`）→ `switch_builder.build_plan()`
     输出行 `- buy_new(新方向低吸): 002714、603477、300761`（**不带市场前缀**）
  ② 09:20 `rotation_switch_arm`
     输出两行 `DECISION sell_legs [...] buy_chains [...] buy_legs [...]` / `ARMED [...]`（Python 字面量）

⚠️ 生产实际有**三条布腿通道**（对账必须分层，详见 docs/backtest-faithful-daily-flow.md §9.11）：
  ① 调度任务（cron）→ 任务日志 + scheduler 行
  ② 手工"立即执行"任务 → 任务日志 + scheduler 行（时刻任意，如 09-09 08:15、09-10 14:11）
  ③ **手工/agent 直接跑脚本**（`docker exec python jobs/rotation_switch_arm.py`）→ **不写任何日志**
     （实测 09-09 07:26/08:56/13:25、09-10 10:14、09-14 07:53；`find /app/logs -newermt` 在这些时刻无任何文件，
     宿主/容器也没有能产生这些时刻的 crontab/systemd timer）
  → ① 是本脚本的真值（可复现）；②③ 只能靠 `--with-db` 与 `t_conditions` 相减得到，属**不可复现**部分。

⚠️ 实测统计（2026-09-04→09-15）：调度通道 **51 条买腿 / 6 个交易日**；DB 实际 102 条（含工作日手工 33 条、
   周末开发测试痕迹 18 条）。DB 本身也不完整（`t_conditions` 只保留 2026-08-15 起）→ 真值以 scheduler 日志为准。

⚠️ `ARMED` 里**买卖腿混在一起**（`buy_253/254` vs `sell_vwap_break`）：09-04 的 4 条 ARMED 全是卖腿，
   若不过滤 `type` 会把分母虚增（本脚本已只认 `type` 以 `buy` 开头）。

用法：
  python jobs/bt_legs_truth.py --sched-dir data/_bt_full/_sched --out data/_bt_full/_legs_truth.json
  python jobs/bt_legs_truth.py --sched-dir ... --with-db --dsn ... --from 20260815 --to 20260915   # 顺带做 DB 对账
  python jobs/bt_legs_truth.py --truth data/_bt_full/_legs_truth.json --replay <legs_by_day.json>   # 与回测对账
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import sys

LEG_TASKS = ("tranche_ladder_report", "rotation_switch_arm")

# 不带前缀的六位代码 → 市场前缀
def norm(code: str) -> str:
    c = re.sub(r"[^0-9]", "", str(code))
    if len(c) != 6:
        return str(code).upper()
    if c[0] == "6":
        return "SH" + c
    if c[0] in ("0", "3"):
        return "SZ" + c
    if c[0] in ("4", "8"):
        return "BJ" + c
    return c


def _literal_after(text: str, key: str):
    """从 `key [ ... ]` 里取出第一个**括号平衡**的 Python 字面量并 literal_eval（列表里可能套字典/元组）。"""
    i = text.find(key)
    if i < 0:
        return None
    j = text.find("[", i + len(key))
    if j < 0:
        return None
    depth, k, quote = 0, j, None
    while k < len(text):
        ch = text[k]
        if quote:
            if ch == "\\":
                k += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "[({":
            depth += 1
        elif ch in "])}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    try:
        return ast.literal_eval(text[j:k + 1])
    except Exception:
        return None


def parse_tranche(out: str):
    """08:18 报告 → {'buy_new': [...], 'keep': [...], 'sell_old': [...]}（代码归一化为带前缀）。"""
    res = {"buy_new": [], "keep": [], "sell_old": []}
    for line in (out or "").splitlines():
        line = line.strip()
        for key, field in (("buy_new(新方向低吸)", "buy_new"), ("keep(强的留)", "keep"),
                           ("sell_old(卖旧)", "sell_old")):
            if line.startswith("- " + key):
                val = line.split(":", 1)[1].strip() if ":" in line else ""
                if val and val != "无":
                    res[field] = [norm(x) for x in re.split(r"[、,，\s]+", val) if x.strip()]
    return res


def parse_arm(out: str):
    """09:20 布腿器 → {'buy_legs': [...], 'armed': [...], 'sell_legs': [...]}（symbol 已是带前缀大写）。

    ⚠️ `ARMED` 里**买腿和卖腿混在一起**（`buy_253`/`buy_254` vs `sell_vwap_break`）：
    只把 `type` 以 `buy` 开头的算买腿（实测 09-04 的 4 条 ARMED 全是 sell_vwap_break → 误当买腿会虚增分母）。
    """
    res = {"buy_legs": [], "armed": [], "armed_sell": [], "sell_legs": [], "buy_chains": []}
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("DECISION"):
            for key, field in (("buy_legs", "buy_legs"), ("sell_legs", "sell_legs"), ("buy_chains", "buy_chains")):
                val = _literal_after(line, key)
                if isinstance(val, list):
                    res[field] = [(x.get("symbol") if isinstance(x, dict) else x) for x in val]
        elif line.startswith("ARMED"):
            val = _literal_after(line, "ARMED")
            if isinstance(val, list):
                for x in val:
                    sym = x.get("symbol") if isinstance(x, dict) else x
                    kind = str(x.get("type") or "") if isinstance(x, dict) else ""
                    if kind.startswith("buy"):
                        res["armed"].append(sym)
                    elif kind:
                        res["armed_sell"].append(sym)
                    else:
                        res["armed"].append(sym)  # 老格式（无 type）→ 按买腿记
    return res


def load_sched(sched_dir: str, d_from=None, d_to=None):
    days = {}
    for path in sorted(glob.glob(os.path.join(sched_dir, "scheduler_*.jsonl"))):
        day = os.path.basename(path).replace("scheduler_", "").replace(".jsonl", "").replace("-", "")
        if d_from and day < d_from:
            continue
        if d_to and day > d_to:
            continue
        runs = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                tid = j.get("task_id")
                if tid not in LEG_TASKS or j.get("status") != "success":
                    continue
                out = j.get("output") or ""
                rec = {"task": tid, "started_at": j.get("started_at"), "finished_at": j.get("finished_at"),
                       "execution_id": j.get("execution_id")}
                if "失败" in out or "Traceback" in out:
                    rec["error"] = out.strip()[:200]
                if tid == "tranche_ladder_report":
                    rec.update(parse_tranche(out))
                else:
                    rec.update(parse_arm(out))
                    rec["gate_confirmed_today"] = [
                        s.split("GATE_CONFIRMED_TODAY", 1)[1].strip()
                        for s in (j.get("error") or "").splitlines() if "GATE_CONFIRMED_TODAY" in s][:1]
                runs.append(rec)
        runs.sort(key=lambda r: r.get("started_at") or "")
        src = {}
        for r in runs:
            if r["task"] == "tranche_ladder_report":
                for s in r.get("buy_new") or []:
                    src.setdefault(s, []).append("tranche_0818@" + (r.get("started_at") or "")[11:16])
            else:
                for s in (r.get("armed") or []):
                    src.setdefault(s, []).append("arm@{}".format((r.get("started_at") or "")[11:16]))
        days[day] = {"runs": runs, "sources": src, "union_buy": sorted(src)}
    return days


def db_legs(dsn, d_from, d_to):
    import psycopg2
    c = psycopg2.connect(dsn)
    cur = c.cursor()
    cur.execute("""SELECT trade_date, symbol, trigger_kind, to_char(created_at,'HH24:MI:SS')
                   FROM t_conditions
                   WHERE trade_date BETWEEN %s AND %s AND direction='buy' AND publisher='switch'
                     AND trigger_kind IN ('custom_m5dump','custom_prevlow')
                   GROUP BY 1,2,3,4 ORDER BY 1,2""", (d_from, d_to))
    out = {}
    for d, sym, kind, ts in cur.fetchall():
        d = str(d)
        out.setdefault(d, {}).setdefault(str(sym).upper(), {})[kind] = ts
    cur.close(); c.close()
    return out


def compare(truth_days, replay_path):
    with open(replay_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    by_day = rep.get("by_day") or rep
    rows = []
    tot_p = tot_hit = tot_r = 0
    for day in sorted(set(list(truth_days.keys()) + list(by_day.keys()))):
        p = set(truth_days.get(day, {}).get("union_buy") or [])
        r = set()
        for leg in ((by_day.get(day) or {}).get("legs") or []):
            sym = leg.get("symbol") if isinstance(leg, dict) else leg
            if sym:
                r.add(str(sym).upper())
        hit = p & r
        tot_p += len(p); tot_hit += len(hit); tot_r += len(r)
        rows.append({"day": day, "prod": sorted(p), "replay": sorted(r),
                     "hit": sorted(hit), "only_prod": sorted(p - r), "only_replay": sorted(r - p)})
    recall = (tot_hit / tot_p) if tot_p else None
    precision = (tot_hit / tot_r) if tot_r else None
    return {"rows": rows, "prod_total": tot_p, "replay_total": tot_r, "hit_total": tot_hit,
            "recall": recall, "precision": precision}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sched-dir", default="data/_bt_full/_sched")
    ap.add_argument("--out", default=None)
    ap.add_argument("--from", dest="d_from", default=None)
    ap.add_argument("--to", dest="d_to", default=None)
    ap.add_argument("--with-db", action="store_true")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--truth", default=None, help="已生成的真值 json（配合 --replay 做对账）")
    ap.add_argument("--replay", default=None, help="bt_days.py 产出的 legs_by_day.json")
    args = ap.parse_args()

    if args.truth and args.replay:
        truth = json.load(open(args.truth, encoding="utf-8"))["days"]
        res = compare(truth, args.replay)
        for row in res["rows"]:
            if not row["prod"] and not row["replay"]:
                continue
            print(f"{row['day']}  真值{len(row['prod'])} 回放{len(row['replay'])} 命中{len(row['hit'])}"
                  f"  仅真值={row['only_prod']}  仅回放={row['only_replay']}")
        print(f"\n合计：真值 {res['prod_total']} / 回放 {res['replay_total']} / 命中 {res['hit_total']}")
        print("召回率 %.1f%%   精确率 %.1f%%" % (100 * (res["recall"] or 0), 100 * (res["precision"] or 0)))
        return

    days = load_sched(args.sched_dir, args.d_from, args.d_to)
    if args.with_db:
        dsn = args.dsn or os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@postgres:5432/marcus_trading"
        lo = args.d_from or (min(days) if days else None)
        hi = args.d_to or (max(days) if days else None)
        db = db_legs(dsn, lo, hi)
        for day, syms in db.items():
            d = days.setdefault(day, {"runs": [], "sources": {}, "union_buy": []})
            d["db"] = {s: v for s, v in syms.items()}
            d["db_only"] = sorted(set(syms) - set(d["union_buy"]))
    out = {"meta": {"sched_dir": args.sched_dir,
                    "days": len(days),
                    "with_db": bool(args.with_db),
                    "note": "真值来自 logs/scheduler_*.jsonl；手工/agent 直接跑脚本的布腿（无日志）不在此列，见 --with-db 的 db_only"},
           "days": days}
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
    if not args.quiet:
        n_legs = sum(len(d["union_buy"]) for d in days.values())
        days_with = [d for d in sorted(days) if days[d]["union_buy"]]
        print(f"真值覆盖 {len(days)} 天（{min(days) if days else '-'}→{max(days) if days else '-'}），"
              f"有腿的天 {len(days_with)}，腿次合计 {n_legs}")
        for d in days_with:
            def _n(r):
                if r["task"] == "tranche_ladder_report":
                    return len(set(r.get("buy_new") or []))
                return len(set((r.get("armed") or []) or (r.get("buy_legs") or [])))
            runs = ", ".join("%s@%s→%d" % (r["task"].replace("tranche_ladder_report", "tranche0818")
                                           .replace("rotation_switch_arm", "arm"),
                                           (r.get("started_at") or "")[11:16], _n(r))
                            for r in days[d]["runs"])
            extra = ""
            if days[d].get("db_only"):
                extra = f"   ⚠️仅DB(无日志布腿)={days[d]['db_only']}"
            print(f"  {d}  {len(days[d]['union_buy']):2d} 腿  [{runs}]{extra}")
        if args.out:
            print("→", args.out)


if __name__ == "__main__":
    main()
