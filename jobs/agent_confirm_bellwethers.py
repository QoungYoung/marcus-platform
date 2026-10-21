# -*- coding: utf-8 -*-
"""用 DSH agent 代人工确认风向标：只读原始行情 + chain_map，不读 proxy。

流程：
  1. 从 subthemes 成分、日线 close/high/amount、chain_map leader/second 生成 raw candidates
  2. 调 DSH 容器 HTTP `/chat`，默认 keep；只有真接力才 switch
  3. switch 必须在 raw candidates 且创新高、相对旧风向标更强
  4. 写 confirmed_daily / changes / audit；agent 失败保持原表
"""
from __future__ import annotations
from datetime import datetime
import glob, json, os, re
import numpy as np, pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
for _p in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "backend/app/services"),
           os.path.join(ROOT, "app/services"), ROOT):
    if os.path.isdir(_p): sys.path.insert(0, _p)
from wolf_card.subthemes import stocks as sub_stocks, parent

OUT = os.path.join(ROOT, "data/core_p05")
os.makedirs(OUT, exist_ok=True)
DAILY = os.path.join(OUT, "confirmed_daily.json")
ANCHOR = os.path.join(ROOT, "config/core_p05_confirmed.json")
CHANGES = os.path.join(OUT, "confirmed_changes.csv")
CHAT_URL = (os.environ.get("BELLWETHER_CHAT_URL") or os.environ.get("MAIN_LINE_CHAT_URL")
            or os.environ.get("PI_SERVER_URL") or "http://marcus-dsh:3001/chat")
TIMEOUT = int(os.environ.get("DSH_AGENT_TIMEOUT", "600"))


def _json(p, default=None):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return default


def _names():
    import sqlite3
    try:
        con = sqlite3.connect(os.path.join(ROOT, ".dsh-tmp/buyside/prod_data/stock_pool.db"))
        return dict(con.execute("select ts_code,name from stock_pool"))
    except Exception:
        return {}


def _chain_roles(date8, theme):
    files = sorted([p for p in glob.glob(os.path.join(ROOT, "data/chain_map_2026*.json")) if "_v2_cmp" not in p])
    use = None
    for p in files:
        d = os.path.basename(p).replace("chain_map_", "").replace(".json", "")
        if d <= date8: use = p
    if not use: return None
    try: x = json.load(open(use, encoding="utf-8"))
    except Exception: return None
    roles = {}
    for t in x.get("themes", []):
        if t.get("theme") != theme: continue
        for seg in t.get("segments", []):
            for i, z in enumerate(seg.get("leading_verified") or [], 1):
                roles[z.get("ts")] = {"role": "leader", "rank": i, "segment": seg.get("label")}
            lead = {z.get("ts") for z in seg.get("leading_verified") or []}
            for z in seg.get("verified_all") or []:
                if z.get("ts") in lead: continue
                roles[z.get("ts")] = {"role": "second", "rank": None, "segment": seg.get("label")}
    return roles or None


def _build_packet():
    b = pd.read_parquet(os.path.join(ROOT, "data/bars_combined.parquet"),
                        columns=["ts_code", "trade_date", "close", "amount"])
    h = pd.read_parquet(os.path.join(ROOT, ".dsh-tmp/buyside/bars.parquet"),
                        columns=["ts_code", "trade_date", "high"])
    b = b.merge(h, on=["ts_code", "trade_date"], how="left")
    close = b.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    high = b.pivot_table(index="trade_date", columns="ts_code", values="high", aggfunc="last").sort_index()
    amt = b.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
    d = str(close.index[-1])
    names = _names()
    daily = _json(DAILY) or _json(ANCHOR, {})
    items = []
    for it in daily.get("items", []):
        sub = it.get("subtheme"); old = it.get("code")
        if not sub or not old: continue
        codes = [c for c in sub_stocks(sub) if c in close.columns]
        if not codes: continue
        roles = _chain_roles(d, parent(sub))
        rows = []
        for c in codes:
            try:
                s = close[c].loc[:d].dropna()
                if len(s) < 25: continue
                a = amt[c].loc[:d].dropna().tail(20).mean()
                hi = high[c].loc[:d].dropna().tail(21)
                new_high = bool(pd.notna(hi.iloc[:-1].max()) and s.iloc[-1] >= hi.iloc[:-1].max() * 0.995)
                r5 = float(s.iloc[-1] / s.iloc[-6] - 1)
                r20 = float(s.iloc[-1] / s.iloc[-21] - 1)
                role = (roles or {}).get(c, {})
                rows.append({"code": c, "name": names.get(c, c), "r5": round(r5*100,2),
                             "r20": round(r20*100,2), "amt20": round(float(a),1) if pd.notna(a) else None,
                             "new_high": new_high, "chain_role": role.get("role"),
                             "chain_rank": role.get("rank")})
            except Exception:
                continue
        # 原始候选：优先 chain leader/second + 创新高 + 5日相对强；无 chain 时按新高/r5
        rows = [x for x in rows if (roles is None or x.get("chain_role") is not None or x["code"] == old)]
        rows.sort(key=lambda x: (bool(x["new_high"]), x.get("chain_rank") == 1, x["r5"] or -99, x["amt20"] or 0), reverse=True)
        top = rows[:8]
        oldrow = next((x for x in rows if x["code"] == old), None)
        items.append({"subtheme": sub, "parent": parent(sub), "current": it,
                      "current_metrics": oldrow, "raw_candidates": top})
    return d, items


def _run_agent(d, items):
    rp = os.path.join(OUT, f"agent_review_{d}.json")
    packet = {"date": d, "source": "raw_bars_chain_map", "items": items,
              "criterion": "默认keep；只有真接力（同子主题、创新高、相对旧风向标强、旧龙头走弱、广度）才switch"}
    json.dump(packet, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    packet_text = json.dumps(packet, ensure_ascii=False)
    prompt = f"""你是 Marcus core 线的每日风向标确认员，代人工确认。
这是原始日线 + chain_map 候选，不是 proxy，不要使用任何 proxy 分数。

=== REVIEW（当天复核包）===
{packet_text}
=== END REVIEW ===

对每个子主题：
- 默认 keep；
- 只有明确真接力才 switch：同子主题/链段、创新高、5日相对旧风向标更强、旧风向标走弱、广度没崩；
- switch 目标必须来自 raw_candidates；
- 单日涨幅、成交额大、跨链大票都不算接力。

只输出 JSON，不要 markdown：
{{"date":"{d}","items":[{{"subtheme":"AI芯片","decision":"keep|switch","code":"...","name":"...","reason":"不超过40字"}}]}}
必须覆盖每个子主题；不确定就 keep。
"""
    last = None
    for attempt in (1, 2):
        try:
            r = requests.post(CHAT_URL, json={"message": prompt, "session_id": f"bellwether_{d}"},
                              timeout=TIMEOUT)
            r.raise_for_status()
            raw = str((r.json() or {}).get("reply") or "").strip()
            txt = re.sub(r"^```(?:json)?\s*", "", raw); txt = re.sub(r"\s*```\s*$", "", txt)
            return json.loads(txt), rp
        except Exception as e:
            last = e
    return {"items": [], "error": str(last)}, rp


def main():
    d, items = _build_packet()
    if not items:
        print("no raw candidates"); return
    ans, rp = _run_agent(d, items)
    by_sub = {x.get("subtheme"): x for x in ans.get("items", []) if isinstance(x, dict)}
    daily = _json(DAILY) or _json(ANCHOR, {})
    changes, rejected = [], []
    for it in daily.get("items", []):
        sub = it.get("subtheme"); dec = by_sub.get(sub) or {}
        packet = next((x for x in items if x["subtheme"] == sub), None)
        raw_codes = {x["code"]: x for x in (packet or {}).get("raw_candidates", [])}
        if str(dec.get("decision", "keep")).lower() != "switch":
            continue
        code = dec.get("code"); target = raw_codes.get(code)
        if not target:
            rejected.append({"subtheme": sub, "code": code, "reason": "not in raw_candidates"})
            continue
        old_metrics = (packet or {}).get("current_metrics") or {}
        if not target.get("new_high") or (target.get("r5") or -99) < (old_metrics.get("r5") or -99) + 2:
            rejected.append({"subtheme": sub, "code": code, "reason": "guard: no new high or rel<2pp"})
            continue
        old = {"code": it.get("code"), "name": it.get("name")}
        it.update({"code": code, "name": target.get("name") or code})
        changes.append({"date": d, "subtheme": sub, "old_code": old["code"], "old_name": old["name"],
                        "new_code": code, "new_name": target.get("name") or code,
                        "reason": dec.get("reason", "agent switch")})
    daily = {"date": d, "source": "dsh_agent_raw_confirmation", "items": daily.get("items", [])}
    json.dump(daily, open(DAILY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"date": d, "review": rp, "agent": ans, "changes": changes, "rejected": rejected},
              open(os.path.join(OUT, f"agent_confirm_{d}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if changes:
        df = pd.DataFrame(changes)
        if os.path.exists(CHANGES):
            df = pd.concat([pd.read_csv(CHANGES), df], ignore_index=True)
        df.to_csv(CHANGES, index=False, encoding="utf-8-sig")
    print("date", d, "subs", len(items), "changes", len(changes), "rejected", len(rejected))
    if ans.get("error"): print("agent_error", ans["error"])


if __name__ == "__main__":
    main()
