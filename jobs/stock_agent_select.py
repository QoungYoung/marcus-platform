#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_agent_select.py — 盘后把 P3 候选交给 DSH agent 选股，并校验落盘。

输入：data/mainline/{date}/stock_agent_facts.json
输出：stock_agent_review.json / stock_agent_validated.json / stock_agent_selected.json
只做选择，不下单。
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def compact(facts):
    out = []
    for t in facts.get("themes", []):
        cands = []
        for c in t.get("candidates", []):
            cands.append({
                "symbol": c.get("symbol"), "name": c.get("name"),
                "ret20": c.get("ret20"), "ret60": c.get("ret60"), "ret120": c.get("ret120"),
                "range_pos_120": c.get("range_pos_120"), "dist_high120": c.get("dist_high120"),
                "chip": {k: (c.get("chip") or {}).get(k) for k in ["winner_rate", "concentration", "price_vs_cost50", "price_vs_cost85", "status"]},
                "holders": {k: (c.get("holders") or {}).get(k) for k in ["holder_num", "trend", "status"]},
                "seats": {k: (c.get("seats") or {}).get(k) for k in ["list_days", "inst_net", "hot_money_net"]},
                "block": {k: (c.get("block_trade") or {}).get(k) for k in ["count", "amount", "avg_price_vs_close", "inst_buy", "inst_sell"]},
                "earnings": {k: (c.get("earnings") or {}).get(k) for k in ["end_date", "revenue", "n_income", "yoy_revenue", "yoy_n_income", "announce_date", "status"]},
                "leader": c.get("leader"),
                "position": {k: (c.get("position") or {}).get(k) for k in ["state", "entry_mode", "risk_flags", "wave"]},
            })
        out.append({"theme_id": t.get("theme_id"), "name": t.get("name"), "verdict": t.get("verdict"), "candidates": cands})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()
    base = os.path.join(ROOT, "data", "mainline", a.date)
    fp = os.path.join(base, "stock_agent_facts.json")
    # ★ 2026-11-03：原来空等 90×10s=15 分钟 ⇒ 上游没产出时通知里只剩一串 "waiting for …" 且
    #   失败点落在**下游**（2026-11-02 实况：facts 16:55 没触发 ⇒ 这里空等 15 分钟 rc=2 ✗）。
    #   现改为 18×10s=3 分钟并点名上游任务；链化后（jobs/stock_agent_chain.py）正常永远秒过 ✓
    for _ in range(18):
        if os.path.exists(fp):
            break
        print(f"waiting for P3 facts: {fp}（上游 stock_agent_facts_daily / 链第 1 步）", flush=True)
        time.sleep(10)
    if not os.path.exists(fp):
        print(f"missing {fp} ⇒ 上游候选事实没产出（先跑 jobs/stock_agent_facts.py）", flush=True)
        return 2
    facts = json.load(open(fp, encoding="utf-8"))
    payload = compact(facts)
    prompt = """你是狼大体系选股 agent。只从候选里选，不取数、不编代码、不下单。
当前 gate：wave/mainline_active 见每只 position.wave；defense 或 mainline_active=false 时只能 watch/reject，不能 probe。
规则：核心>风向标>龙头>龙2/二供>杂毛；高位不追、低位才考虑老龙头；老龙头必须有数据证据（seed_role/top3/limit_up/席位），不能只凭世界知识；业绩过期/缺失要扣分；同主题最多3只，含reject。
输出 JSON 数组：
[{"theme_id":"...","theme_view":"...","selected":[{"symbol":"...","name":"...","classification":"核心|风向标|龙头|龙2|二供|杂毛","old_leader":true,"verdict":"watch|reject","confidence":0.0,"evidence":["..."],"counter_evidence":["..."]}],"needs_human":false}]
候选事实：
""" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    url = os.environ.get("PI_SERVER_URL") or os.environ.get("DSH_CHAT_URL") or "http://dsh:3001/chat"
    r = requests.post(url, json={"message": prompt, "session_id": f"stock_agent_{a.date}", "mode": "chat"}, timeout=a.timeout)
    r.raise_for_status()
    reply = (r.json() or {}).get("reply", "")
    m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", reply)
    if not m:
        m = re.search(r"(\[[\s\S]*\])", reply)
    if not m:
        print("agent 未返回 JSON"); print(reply[:1000]); return 3
    review = json.loads(m.group(1))
    rp = os.path.join(base, "stock_agent_review.json")
    json.dump(review, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("review 写入 stock_agent_review.json")
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "stock_agent_review.py"),
                    "--date", a.date, "--review", rp], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
