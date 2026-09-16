# -*- coding: utf-8 -*-
"""bt_prod_progress.py — 生产链年跑的进度/成绩速览。

读 `data/_bt_year/_summary/prod_<day>.json`（`jobs/bt_prod_run.py` 每日产出的结果），
汇总：跑了几天 / 布腿 / 触发 / 成交 / 持仓 / 决策对象是否允许买入 / 被拦原因 TOP。

用法：python jobs/bt_prod_progress.py [--root data/_bt_year] [--tail 12]
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



PG_URL = os.getenv("BT_PG_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")


def _pg(sql: str, args=()):
    """直连**本地 PG**（生产模拟盘的真实落库），避免用某天 JSON 里的**快照**当现状。

    ⚠️ 为什么必须这样：`prod_<day>.json` 里的 `account` 是**写那一天结果时读到的快照**，
    它永远落后于当前账户——实测「已完成 4 天」时最后一天 JSON 里还是 250000，
    而 PG 里早已是 156251（已有成交），用户一眼就看出不对。
    """
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(PG_URL)
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        print("[进度] ⚠️ 本地 PG 取数失败：%s" % str(e)[:80])
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"))
    ap.add_argument("--tail", type=int, default=12)
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.root, "_summary", "prod_*.json")))
    if not files:
        print("还没有跑完任何一天（找 %s/_summary/prod_*.json）" % a.root)
        return 1
    days, legs = [], 0
    # ⚠️ 每日 prod_<day>.json 里的 triggers/trades 是**该时刻的全量快照**（不是当日增量）
    #    → 直接累加会重复计数（实测 4 天累加 36 次，库里其实只有 14 条）。按 id 去重。
    trig_ids, fill_ids = set(), set()
    status = collections.Counter()
    reasons = collections.Counter()
    blocked = 0
    pos_last, last = 0, None
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        days.append(d["day"])
        legs += len(d.get("armed") or [])
        for _t in (d.get("triggers") or []):
            trig_ids.add(_t.get("id"))
        for _t in (d.get("trades") or []):
            fill_ids.add(_t.get("id"))
        pos_last = len(d.get("positions") or [])
        last = d
        for t in d.get("triggers") or []:
            status[t.get("status")] += 1
            if t.get("status") == "blocked":
                blocked += 1
                reasons[(t.get("reason") or "")[:60]] += 1
    print("已完成 %d 天：%s → %s" % (len(days), days[0], days[-1]))
    _acct = (_pg("SELECT available_cash, frozen_cash FROM paper_account_info WHERE account_id='stock'") or [{}])[0]
    _ntr = (_pg("SELECT count(*) c FROM paper_trades WHERE account_id='stock' AND coalesce(voided,0)=0") or [{}])[0].get("c")
    _npos = (_pg("SELECT count(*) c FROM paper_positions WHERE account_id='stock' AND volume>0") or [{}])[0].get("c")
    print("账户（实时·本地 PG）: 可用 %s + 冻结 %s | 成交 %s 笔 | 持仓 %s 只"
          % (_acct.get("available_cash"), _acct.get("frozen_cash"), _ntr, _npos))
    print("布腿 %d 条 / 触发 %d 条（按 id 去重，来自逐日 JSON）" % (legs, len(trig_ids)))
    print("触发状态：%s" % dict(status))
    if blocked:
        print("被拦 TOP5：")
        for r, n in reasons.most_common(5):
            print("  %4d  %s" % (n, r))
    if last:
        dec = last.get("decision") or {}
        print("最后一天决策对象：cut=%s 允许买入=%s 缺失层=%s" % (dec.get("cut"), dec.get("allowed"), dec.get("missing")))
        acct = (last.get("account") or [{}])[0]
        if acct:
            # 这是**写那一天结果时读到的快照**，不是现状（现状见上面"实时·本地 PG"行）
            print("最后一天快照（写 %s 时）: 可用 %s / 初始 %s"
                  % (last.get("day"), acct.get("available_cash"), acct.get("initial_capital")))
    print("最近 %d 天：" % min(a.tail, len(days)))
    for f in files[-a.tail:]:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        dec = d.get("decision") or {}
        print("  %s 腿%3d 触发%3d 成交%2d 持仓%2d 允许买入=%s" % (
            d["day"], len(d.get("armed") or []), len(d.get("triggers") or []),
            len(d.get("trades") or []), len(d.get("positions") or []), dec.get("allowed")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
