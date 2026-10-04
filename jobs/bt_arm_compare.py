#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多臂同口径对照（T6 复盘沉淀出来的三张表：仓位 / 买侧漏斗 / 出口结构）。

## 为什么要它

T6 复盘（见 `docs/wolf-buy-parameter-ledger.md`「2026-09-22（续3）」）发现：判断一条臂"好不好"，
光看末值不够——T6 的病是**参与度**（平均仓位 15.8%、买腿成交率 0.9%）与**出口结构**
（71% 破位腿、止盈腿只占 25%）。这三张表就是那次的度量口径，固化成脚本以免每次手搓。

## 口径（与看板/账本一致，改动请同步那两处）

- **曲线**：直接调 `jobs/bt_dashboard.py` 的 `Store.snapshot()`（逐日产物 `_summary/prod_*.json` + 成交重建），
  于是"仓位"=`mv/equity`，与页面上的柱子同源。
- **成交**：`_summary/prod_*.json` 里的 `trades` 是**累计口径** ⇒ 按 `id` 去重后统计（这是踩过的坑）。
- **买侧漏斗**：布腿 = 各日沙箱 `legs.jsonl` + `legs_switch.jsonl` 里 `side=buy`（或 type 含 buy）的行数；
  成交 = 去重后 `direction=买入` 的笔数。⚠️ 分母受"候选池大小"影响 ⇒ **横向只比趋势，不比倍数**。
- **出口结构**：卖出腿按 `reason/腿型` 分 止盈类 / 破位·止损类 / 其他（止盈类用
  `wolf_base_hold.is_take_reason`，与 方案③ 的判定同源）。
- **仓位分档**与**空仓天数**：空仓 = `mv/equity < 1%`。

## 用法

```
.venv/bin/python jobs/bt_arm_compare.py \
    --arm T6=data/_bt_t6:drabt6:.dsh-tmp/wolfbt/logs/size_run_t6.log \
    --arm T5=data/_bt_t5:drabt5:.dsh-tmp/wolfbt/logs/size_run_t5.log \
    --arm T7=data/_bt_t7:drabt7:.dsh-tmp/wolfbt/logs/size_run_t7.log
```
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import statistics as st
import sys
from collections import Counter, defaultdict


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


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, "jobs")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

TAKE_EV = {"high_sell", "wolf_dao_t_sell", "wolf_profit_take_sell", "wolf_fib_target_sell",
           "wolf_board_half_sell", "wolf_confirm_sell", "wolf_boll_sell", "wolf_boll_mid_sell"}
# 破位/止损类腿型（英文腿型名，取自 t_triggers.event_type 与 reason 里的「（kind）」）
STOP_EV = {"custom_support_sell", "custom_vwap_sell", "custom_level_sell", "custom_trail_sell",
           "stop_loss", "wolf_passive_stop_sell", "wolf_defensive_t_reduce", "wolf_index_level_stop",
           "wolf_logic_time_stop", "wolf_derisk_cut", "wolf_stop_loss", "high_sell_then_buy_back"}
# 中文兜底关键词（reason 里没有腿型时用）
STOP_KW = ("止损", "破位", "支撑", "破线", "趋势线", "量能分层", "清仓", "避险", "离场", "反抽")


def _load_dashboard():
    spec = importlib.util.spec_from_file_location("bt_dashboard_mod", os.path.join(ROOT, "jobs", "bt_dashboard.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def curve_from_pg(root: str, account: str, until: str = ""):
    """从**活库成交 + bars 收盘**重建逐日曲线（与看板同公式：现金/持仓/最近≤当日收盘计价）。

    为什么要自己算：看板 `Store.snapshot()` 用「主日志 last_completed」当曲线终点 —— 对**已跑完的臂**
    日志启发式会失准（实测给 T5/j13 都算成 1 天）。这里改用"该臂产物里的最大日"当终点，自洽。
    ⚠️ 活库只保留部分历史账户（实测 drabt6/drabt5 的行会被后续臂清掉）⇒ 结果可落 `--save-curve` 快照。
    """
    import sqlite3
    try:
        import psycopg2
    except Exception:
        return []
    mod = _load_dashboard()
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL",
                                          "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"),
                                connect_timeout=4)
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        cur.execute("SELECT initial_capital FROM paper_account_info WHERE account_id=%s", (account,))
        row = cur.fetchone()
        initial = float(row[0]) if row and row[0] else 250000.0
        cur.execute("SELECT id,symbol,direction,price,volume,profit,trade_date FROM paper_trades "
                    "WHERE account_id=%s AND COALESCE(voided,0)=0 ORDER BY id", (account,))
        tr = [{"id": r[0], "symbol": r[1], "direction": r[2], "price": float(r[3] or 0),
               "volume": int(r[4] or 0), "profit": float(r[5] or 0),
               "trade_date": str(r[6]).replace("-", "")} for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        print("  ⚠️ PG 取数失败: %s" % str(e)[:70])
        return []
    days = sorted({x for x in (re.search(r"prod_(\d{8})", p).group(1)
                              for p in glob.glob(os.path.join(root, "_summary", "prod_*.json")))}) \
        if glob.glob(os.path.join(root, "_summary", "prod_*.json")) else []
    if not days or not tr:
        return []
    last = max(days)
    if until:
        last = min(last, until)
        tr = [t for t in tr if t["trade_date"] <= until]
    syms = sorted({t["symbol"] for t in tr})
    # ── 计价空间（2026-09-24）────────────────────────────────────────────────
    # 复权臂（WOLF_ADJ_PRICE=1）成交价在前复权空间；若用不复权收盘计价 ⇒ 持仓市值被放大 f 倍
    # （drabt14 0128 虚影 +12,166、单日 −11,098 实为 −4,298）。判据复用看板 detect_price_space。
    _bars_raw = os.path.join(ROOT, "data/_bt_full", "bars.sqlite")
    _bars_adj = os.path.join(ROOT, "data/_bt_full", "bars_adj.sqlite")
    _sp = mod.detect_price_space([{"symbol": t["symbol"], "trade_date": t["trade_date"],
                                   "price": t["price"]} for t in tr], _bars_raw, _bars_adj)
    _use = _bars_adj if (_sp.get("space") == "adj" and os.path.exists(_bars_adj)) else _bars_raw
    print("     计价空间 %s（%s）⇒ 用 %s" % (_sp.get("space"), _sp.get("why"), os.path.basename(_use)))
    bars = sqlite3.connect(_use)
    bars.execute("PRAGMA temp_store=MEMORY")   # 否则大 DISTINCT 会报 unable to open database file
    q = "SELECT trade_date,close FROM bars WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date"
    # 只用"成交涉及票"的收盘 + 全市场交易日历（用任一活跃标的口径拿日历）
    cal = [d for (d,) in bars.execute(
        "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (min(t["trade_date"] for t in tr), last))]
    closes = {}
    for s in syms:
        ts = s[2:] + "." + s[:2] if len(s) >= 8 else s
        closes[s] = [(d, c) for d, c in bars.execute(q, (ts, min(cal), last)) if c]
    ptr = {s: 0 for s in syms}
    last_close = {}
    cash, realized, peak = initial, 0.0, initial
    vols = defaultdict(int)
    out = []
    by_day = defaultdict(list)
    for t in tr:
        by_day[t["trade_date"]].append(t)
    for day in cal:
        for t in by_day.get(day, []):
            amt = t["price"] * t["volume"]
            if t["direction"] in ("买入", "buy"):
                cash -= amt * mod.BUY_FEE
                vols[t["symbol"]] += t["volume"]
            else:
                cash += amt * mod.SELL_FEE
                vols[t["symbol"]] -= t["volume"]
                realized += t["profit"]
        for s in syms:
            seq = closes.get(s) or []
            i = ptr[s]
            while i < len(seq) and seq[i][0] <= day:
                last_close[s] = seq[i][1]
                i += 1
            ptr[s] = i
        mv = sum(v * last_close.get(s, 0.0) for s, v in vols.items() if v > 0)
        eq = cash + mv
        peak = max(peak, eq)
        out.append({"day": day, "equity": round(eq, 2), "cash": round(cash, 2), "mv": round(mv, 2),
                    "realized": round(realized, 2),
                    "dd_pct": round((eq - peak) / peak * 100.0, 3) if peak else 0.0})
    return out


def curve_of(root: str, account: str, log: str):
    """逐日曲线（equity/cash/mv/realized）—— 与看板同源。"""
    mod = _load_dashboard()
    a = argparse.Namespace(root=root, bars=os.path.join(ROOT, "data/_bt_full/bars.sqlite"),
                           mins=os.path.join(ROOT, "data/_bt_full/mins"), log=log,
                           pg=os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"),
                           account=account, ttl=999, no_auto=True, auto_follow=False)
    try:
        snap = mod.Store(a).snapshot()
    except Exception as e:                                    # pragma: no cover
        print("  ⚠️ 曲线取数失败: %s" % str(e)[:80])
        return []
    return snap.get("curve") or []


def trades_of(root: str, until: str = "") -> list:
    """累计成交按 id 去重 ⇒ 真实成交列表（`until` = 截断到该日，供**同窗口**跨臂对照）。"""
    seen = {}
    for p in sorted(glob.glob(os.path.join(root, "_summary", "prod_*.json"))):
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception as _e_sil1:
            _silent_alert("bt_arm_compare.py:182", _e_sil1)
            continue
        for t in (j.get("trades") or []):
            if t.get("id") is None:
                continue
            d = str(t.get("trade_date") or t.get("created_at") or "")[:10].replace("-", "")
            if until and d and d > until:
                continue
            seen[t["id"]] = t
    return list(seen.values())


def legs_buy_count(root: str, until: str = "") -> int:
    """布腿（买侧）行数：各日沙箱 legs*.jsonl（`until` = 只数到该日）。"""
    n = 0
    for p in glob.glob(os.path.join(root, "2026*", "legs*.jsonl")):
        if until and os.path.basename(os.path.dirname(p)) > until:
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception as _e_sil2:
                _silent_alert("bt_arm_compare.py:206", _e_sil2)
                continue
            side = str(o.get("side") or o.get("direction") or "")
            ty = str(o.get("type") or "")
            if side == "buy" or "buy" in ty:
                n += 1
    return n


def _kind_of(t: dict) -> str:
    """腿型：优先 event_type；退化到 reason 里的「（kind）」。"""
    ev = str(t.get("event_type") or "").strip()
    if ev:
        return ev
    m = re.search(r"（([a-z_0-9]+)）", str(t.get("reason") or ""))
    return m.group(1) if m else ""


def exit_class(t: dict) -> str:
    """出口分类：止盈类 / 破位·止损类 / 其他。

    判定顺序（2026-09-22 修：先按**腿型**，中文关键词只作兜底 —— 否则 `custom_support_sell`
    这类英文腿型会被归进"其他"，把 T6 的 71% 破位错报成 21%）：
      ① 腿型 ∈ 止盈类集合，或 reason 命中止盈关键词（与 方案③ 的 `is_take_reason` 同源）
      ② 腿型 ∈ 破位/止损集合，或 reason 命中中文兜底关键词
    """
    kind = _kind_of(t)
    reason = str(t.get("reason") or "")
    if kind in TAKE_EV:
        return "止盈类"
    try:
        from app.services.wolf_base_hold import is_take_reason
        if is_take_reason(reason):
            return "止盈类"
    except Exception:
        if any(k in reason for k in ("高抛", "止盈", "fib", "profit_take")):
            return "止盈类"
    if kind in STOP_EV or any(k in reason for k in STOP_KW):
        return "破位/止损类"
    return "其他"


def report(tag: str, spec: str, until: str = "") -> dict:
    root, account, log = (spec.split(":") + ["", ""])[:3]
    print("=" * 78)
    print("%s   root=%s account=%s%s" % (tag, root, account, ("  [截断到 %s]" % until) if until else ""))
    if not os.path.isdir(root):
        print("  ⚠️ 目录不存在")
        return {}
    cur = curve_from_pg(root, account, until)
    if not cur:
        cur = curve_of(root, account, log)      # 退化到看板（含日志启发式）
    if until:
        cur = [c for c in cur if c["day"] <= until]
    tr = trades_of(root, until)
    buys = [t for t in tr if str(t.get("direction")) in ("买入", "buy")]
    sells = [t for t in tr if str(t.get("direction")) in ("卖出", "sell")]
    out = {"tag": tag, "days": len(cur), "buys": len(buys), "sells": len(sells),
           "legs_buy": legs_buy_count(root, until)}
    # ① 仓位
    if cur:
        eq0 = cur[0].get("equity") or 0
        eqN = cur[-1].get("equity") or 0
        expo = [(c.get("mv") or 0) / (c.get("equity") or 1) for c in cur if c.get("equity")]
        out.update({"day_from": cur[0]["day"], "day_to": cur[-1]["day"], "equity0": eq0, "equityN": eqN,
                    "ret_pct": (eqN / eq0 - 1) * 100 if eq0 else 0,
                    "expo_mean": st.mean(expo) * 100 if expo else 0,
                    "expo_med": st.median(expo) * 100 if expo else 0,
                    "empty_days": sum(1 for x in expo if x < 0.01), "n_expo": len(expo)})
        print("  ① 仓位 : %s→%s  %d 天  权益 %.0f→%.0f（%+.2f%%）  仓位均值 %.1f%%／中位 %.1f%%  空仓 %d/%d 天" % (
            out.get("day_from"), out.get("day_to"), out["days"], eq0, eqN, out["ret_pct"],
            out["expo_mean"], out["expo_med"], out["empty_days"], out["n_expo"]))
    # ② 买侧漏斗
    if out["legs_buy"]:
        out["fill_rate"] = 100.0 * len(buys) / out["legs_buy"]
        print("  ② 漏斗 : 布腿(买) %d → 买成交 %d ⇒ 成交率 %.1f%%" % (
            out["legs_buy"], len(buys), out["fill_rate"]))
    # ③ 出口结构
    if sells:
        c = Counter(exit_class(t) for t in sells)
        out["exit"] = {k: (c[k], round(100.0 * c[k] / len(sells))) for k in ("止盈类", "破位/止损类", "其他")}
        print("  ③ 出口 : 卖 %d 笔 ⇒ 止盈类 %d(%.0f%%)  破位/止损类 %d(%.0f%%)  其他 %d(%.0f%%)" % (
            len(sells), c["止盈类"], 100 * c["止盈类"] / len(sells),
            c["破位/止损类"], 100 * c["破位/止损类"] / len(sells), c["其他"], 100 * c["其他"] / len(sells)))
    if buys:
        nt = [float(t["price"]) * int(t["volume"]) for t in buys if t.get("price")]
        if nt:
            out["buy_notional_med"] = st.median(nt)
            print("      买笔名义：中位 %.0f 元（占账户 %.1f%%）" % (st.median(nt), 100 * st.median(nt) / 250000))
    agg = defaultdict(lambda: {"b": 0, "s": 0})
    for t in tr:
        s = t.get("symbol")
        if str(t.get("direction")) in ("买入", "buy"):
            agg[s]["b"] += int(t.get("volume") or 0)
        else:
            agg[s]["s"] += int(t.get("volume") or 0)
    full = sum(1 for a in agg.values() if a["b"] > 0 and a["s"] >= a["b"])
    if agg:
        out["full_sell_pct"] = 100.0 * full / len(agg)
        print("      标的 %d 只，被全部卖光 %d 只（%.0f%%）" % (len(agg), full, out["full_sell_pct"]))
    return out


def per_day(root: str, until: str = "") -> dict:
    """逐日计数：买/卖/止盈类卖/破位类卖（供两臂同期差异定位）。"""
    out = defaultdict(lambda: {"buy": 0, "sell": 0, "take": 0, "stop": 0})
    for t in trades_of(root, until):
        d = str(t.get("trade_date") or t.get("created_at") or "")[:10].replace("-", "")
        if not d or (until and d > until):
            continue
        if str(t.get("direction")) in ("买入", "buy"):
            out[d]["buy"] += 1
        else:
            out[d]["sell"] += 1
            k = exit_class(t)
            if k == "止盈类":
                out[d]["take"] += 1
            elif k == "破位/止损类":
                out[d]["stop"] += 1
    return out


def diff_mode(a: str, b: str, until: str = "") -> None:
    """两臂逐日差异（`A:B`，A=新臂）。差异=新臂−旧臂；同日不同则是改动生效的落点。"""
    ta, ra = a.split("=", 1)[0], a.split("=", 1)[1].split(":")[0]
    tb, rb = b.split("=", 1)[0], b.split("=", 1)[1].split(":")[0]
    da, db = per_day(ra, until), per_day(rb, until)
    days = sorted(set(da) | set(db))
    print("== 逐日差异 %s(新) − %s(旧)  %s" % (ta, tb, ("截断到 " + until) if until else "全窗"))
    print("%-10s %-18s %-18s %s" % ("日", "新臂 买/卖/止盈/破位", "旧臂 买/卖/止盈/破位", "差(买/卖/止盈/破位)"))
    cum = {"buy": 0, "sell": 0, "take": 0, "stop": 0}
    for d in days:
        A, B = da.get(d, {}), db.get(d, {})
        f = lambda x: "%d/%d/%d/%d" % (x.get("buy", 0), x.get("sell", 0), x.get("take", 0), x.get("stop", 0))
        dd = {k: A.get(k, 0) - B.get(k, 0) for k in cum}
        if any(dd.values()):
            for k in cum:
                cum[k] += dd[k]
            print("%-10s %-18s %-18s %+d/%+d/%+d/%+d" % (d, f(A), f(B), dd["buy"], dd["sell"], dd["take"], dd["stop"]))
    print("累计差异：买 %+d / 卖 %+d / 止盈类 %+d / 破位类 %+d" % (cum["buy"], cum["sell"], cum["take"], cum["stop"]))


def main():
    ap = argparse.ArgumentParser(description="多臂同口径对照（仓位/漏斗/出口结构）")
    ap.add_argument("--arm", action="append",
                    help="标签=沙箱根:账户:日志（可重复）")
    ap.add_argument("--json", help="把结果写到该 json 文件")
    ap.add_argument("--save-curve", help="把各臂曲线快照写到该 json（PG 只保留部分历史 ⇒ 跨臂对照必须落快照）")
    ap.add_argument("--curve-from", help="从曲线快照 json 读（缺哪臂补哪臂）")
    ap.add_argument("--until", default="", help="截断到该日（YYYYMMDD）⇒ 与还在跑的臂做**同窗口**对照")
    ap.add_argument("--diff", help="两臂逐日差异：\"T7=data/_bt_t7:drabt7:T6=data/_bt_t6:drabt6\"")
    a = ap.parse_args()
    if not a.arm and not a.diff:
        ap.error("要么给 --arm，要么给 --diff")
    if a.diff:
        left, right = a.diff.split(":", 1)
        diff_mode(left, right, a.until)
        return
    saved = {}
    if a.curve_from and os.path.exists(a.curve_from):
        try:
            saved = json.load(open(a.curve_from, encoding="utf-8"))
        except Exception:
            saved = {}
    res = []
    for spec in a.arm:
        tag, rest = spec.split("=", 1)
        r = report(tag, rest, a.until)
        if not (r or {}).get("days") and saved.get(tag):
            print("  （PG 无该账户 ⇒ 用曲线快照：%d 天）" % len(saved[tag]))
            r.update(saved[tag])
        res.append(r)
    if a.save_curve:
        for r in res:
            if r.get("days"):
                saved[r["tag"]] = {k: v for k, v in r.items() if k in
                                   ("days", "expo_mean", "expo_med", "empty_days", "n_expo",
                                    "ret_pct", "equity0", "equityN")}
        json.dump(saved, open(a.save_curve, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n曲线快照已写 %s" % a.save_curve)
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写 %s" % a.json)


if __name__ == "__main__":
    main()
