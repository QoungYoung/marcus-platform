#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B反 锚点 agent（15:40 收盘后）—— 判断候选结构点算不算正式锚点，结果写 PG。

分工：
  · 程序：算候选点（最后一个已确认锚点之后的新低/新高），判"够不够格"（之后 3–5 日不再破）
  · agent：判"这是什么"（4-3低 / 4-5低 / 只是中继小低点），并给 label/role/evidence
  · 库：b_rebound_anchors，只追加不覆盖；错了写 superseded，不删

用法：
  python jobs/wolf_brebound_anchor_run.py              # dry-run（默认，只打印）
  python jobs/wolf_brebound_anchor_run.py --apply      # 真正写库
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

INSTRUMENTS = {"000001.SH": "上证指数", "588170.SH": "科创芯片ETF"}
CONFIRM_DAYS = 3          # 候选点之后至少经过这么多个交易日仍未被破，才够格提交给 agent
LOOKBACK = 90             # 给 agent 看的近期 K 线根数


def dsn() -> str:
    return os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:18789/marcus_trading"


def load_anchors(ins: str) -> list[dict]:
    import psycopg2
    with psycopg2.connect(dsn(), connect_timeout=8) as conn:
        cur = conn.cursor()
        cur.execute("select session_date, price, kind, label, role, to_char(confirmed_at,'YYYY-MM-DD') "
                    "from b_rebound_anchors where instrument=%s and status='confirmed' "
                    "order by session_date", (ins,))
        return [dict(date=r[0], price=float(r[1]), kind=r[2], label=r[3], role=r[4], confirmed=r[5])
                for r in cur.fetchall()]


def bars(ins: str) -> pd.DataFrame:
    from core.tushare_relay import relay_query
    api = "index_daily" if ins == "000001.SH" else "fund_daily"
    d = relay_query(api, ts_code=ins, start_date="20240101",
                    end_date=datetime.now().strftime("%Y%m%d"))
    d = d.sort_values("trade_date").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "pre_close", "vol"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce").astype(float)
    # 份额折算（ETF）
    k = [1.0] * len(d)
    for i in range(1, len(d)):
        if d.close[i - 1] and d.pre_close[i] and abs(d.pre_close[i] / d.close[i - 1] - 1) > 0.02:
            k[i] = d.pre_close[i] / d.close[i - 1]
    import numpy as np
    cum = np.cumprod(k[::-1])[::-1] / np.array(k)
    for c in ("open", "high", "low", "close"):
        d[c] = d[c].values * cum
    return d


def candidates(df: pd.DataFrame, anchors: list[dict]) -> list[dict]:
    """最后一个已确认锚点之后的新低/新高；距今天数 ≥ CONFIRM_DAYS 且之后未被破 ⇒ 够格。"""
    last = anchors[-1]["date"] if anchors else "19000101"
    seg = df[df.trade_date > last]
    out = []
    if not len(seg) or len(df) < CONFIRM_DAYS + 1:
        return out
    for kind, col, fn in (("low", "low", "min"), ("high", "high", "max")):
        idx = seg[col].idxmin() if fn == "min" else seg[col].idxmax()
        px = float(df.loc[idx, col])
        age = int(df.index[-1] - idx)
        after = df.loc[idx + 1:, col]
        broken = bool(after.min() < px) if kind == "low" else bool(after.max() > px)
        out.append(dict(kind=kind, date=str(df.loc[idx, "trade_date"]), price=px,
                        age=age, broken=broken, eligible=(age >= CONFIRM_DAYS and not broken)))
    return out


def ask_agent(ins: str, name: str, df: pd.DataFrame, anchors: list[dict], cands: list[dict]) -> list[dict]:
    tail = df.tail(LOOKBACK)
    rows = "\n".join(f"{r.trade_date} 开{r.open:.3f} 高{r.high:.3f} 低{r.low:.3f} 收{r.close:.3f} 量{r.vol:.0f}"
                     for r in tail.itertuples())
    anc = "\n".join(f"  {a['date']} {a['price']:.3f} {a['kind']} {a['label'] or ''} ({a['role'] or ''})"
                    for a in anchors[-8:]) or "  （无）"
    cl = "\n".join(f"  {c['date']} {c['price']:.3f} {c['kind']}｜距今天数 {c['age']}｜之后是否被破 "
                   f"{'是' if c['broken'] else '否'}｜够格 {'是' if c['eligible'] else '否'}" for c in cands) or "  （无）"
    prompt = (
        f"你是「狼大 B反 框架」的结构点标注助手。标的：{name}（{ins}）。\n"
        "任务：判断下面这些**候选结构点**，哪些够格成为正式锚点，以及它们是什么。\n\n"
        f"已确认的锚点（最后 8 个）：\n{anc}\n\n"
        f"程序算出的候选点（最后一个已确认锚点之后的新低/新高）：\n{cl}\n\n"
        f"近期日线（最近 {LOOKBACK} 根，ETF 已折算复权）：\n{rows}\n\n"
        "判断标准（来自账本）：\n"
        "1. 只有被他引用过、或与更大结构位重合的点，才配叫「结构点」；普通小低点/小高点不算。\n"
        "2. 低点要「之后 3–5 个交易日不再破 + 收盘站上」才算确认；高点同理。\n"
        "3. 结构点分三种 role：目标位（用来算空间）/ 已出现（已确认的拐点）/ 待确认（还在走）。\n"
        "4. 判断不了就 action=reject，宁可少标，不要多标。\n\n"
        "输出：**只输出一个 JSON 数组**，不要任何解释文字。每项：\n"
        '{"session_date":"YYYYMMDD","price":数字,"kind":"low|high","label":"如 4-5低",'
        '"role":"目标位|已出现|待确认","action":"confirm|reject","evidence":"引用上面的数字，一句话"}\n'
        "没有要确认的就输出 []。"
    )
    payload = json.dumps({"message": prompt,
                          "session_id": f"anchor_{ins}_{datetime.now():%Y%m%d}"}).encode()
    url = os.getenv("PI_SERVER_URL", "http://127.0.0.1:3001/chat")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        reply = json.loads(resp.read().decode()).get("reply", "")
    m = re.search(r"\[[\s\S]*\]", reply)
    if not m:
        print(f"[warn] {ins} agent 未返回 JSON：{reply[:160]}", file=sys.stderr)
        return []
    try:
        return json.loads(m.group(0))
    except Exception as e:
        print(f"[warn] {ins} JSON 解析失败：{e}", file=sys.stderr)
        return []


def write_anchors(ins: str, items: list[dict], eligible: list[dict]) -> int:
    """只写「程序判够格」的那几个点。agent 只能在够格的点里选，不能自己造锚点。"""
    import psycopg2
    ok = {(str(c["date"]), round(float(c["price"]), 3), c["kind"]) for c in eligible}
    n = 0
    with psycopg2.connect(dsn(), connect_timeout=8) as conn:
        cur = conn.cursor()
        for it in items:
            if str(it.get("action")) != "confirm":
                continue
            key = (str(it.get("session_date")), round(float(it.get("price", 0)), 3), str(it.get("kind")))
            if key not in ok:
                print(f"[skip] {key} 未被程序判为够格（距今<{CONFIRM_DAYS}日或已被破）", file=sys.stderr)
                continue
            try:
                cur.execute(
                    "insert into b_rebound_anchors(instrument,session_date,price,kind,label,role,status,"
                    "evidence,confirmed_at,created_by) values (%s,%s,%s,%s,%s,%s,'confirmed',%s,now(),'agent') "
                    "on conflict (instrument,session_date,price,kind) do nothing",
                    (ins, str(it["session_date"]), float(it["price"]), str(it["kind"]),
                     str(it.get("label") or "")[:32], str(it.get("role") or "已出现")[:16],
                     str(it.get("evidence") or "")[:500]))
                n += cur.rowcount
            except Exception as e:
                print(f"[warn] 写入失败 {it}: {str(e)[:80]}", file=sys.stderr)
        conn.commit()
    return n


def ask_wave_state(df: pd.DataFrame, anchors: list[dict], prev: dict | None) -> dict | None:
    """让 agent 维护「段位」五项。约束：completed 必须包含最后一个已确认锚点。"""
    anc = "\n".join(f"  {a['date']} {a['price']:.3f} {a['kind']} {a['label'] or ''}" for a in anchors[-8:])
    tail = df.tail(20)
    rows = "\n".join(f"{r.trade_date} 高{r.high:.2f} 低{r.low:.2f} 收{r.close:.2f} 量{r.vol:.0f}"
                     for r in tail.itertuples())
    pv = json.dumps(prev, ensure_ascii=False) if prev else "（无，这是第一天）"
    prompt = (
        "你是「狼大 B反 框架」的浪型维护助手。标的：上证指数。\n"
        "任务：根据已确认的结构点锚 + 最近走势，写出今天收盘后的**段位**五项。\n\n"
        f"已确认锚点（最后 8 个，权威）：\n{anc}\n\n"
        f"最近 20 根日线：\n{rows}\n\n"
        f"上一版段位：{pv}\n\n"
        "他的改判规则（原话）：\n"
        "  · 「收盘后如果没有收回跳空跌幅，整波浪型从 X 转为主跌1浪」\n"
        "  · 「延伸浪的终结一定是一个明确的结构」\n"
        "  · 「B反 是大级别的，持续至少 2 个月交易日以上的」\n\n"
        "要求：\n"
        "1. **completed 里必须包含最后一个已确认锚点**（日期+价格），否则视为无效。\n"
        "2. running 写「现在在走哪一段」，要能由最后一个锚点推出来。\n"
        "3. 只有出现明确依据才改 level，否则沿用上一版。\n"
        "4. 输出**只一个 JSON 对象**，不要解释：\n"
        '{"level":"","completed":"","running":"","next_watch":"","rejudge_trigger":"","evidence":"一句话，引用数字"}'
    )
    payload = json.dumps({"message": prompt,
                          "session_id": f"wave_{datetime.now():%Y%m%d}"}).encode()
    url = os.getenv("PI_SERVER_URL", "http://127.0.0.1:3001/chat")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        reply = json.loads(resp.read().decode()).get("reply", "")
    m = re.search(r"\{[\s\S]*\}", reply)
    if not m:
        print(f"[warn] 段位未返回 JSON：{reply[:160]}", file=sys.stderr)
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception as e:
        print(f"[warn] 段位 JSON 解析失败：{e}", file=sys.stderr)
        return None
    last = anchors[-1]
    if str(last["date"]) not in str(obj.get("completed", "")) and f"{last['price']:.2f}" not in str(obj.get("completed", "")):
        print(f"[skip] 段位与锚点不一致（completed 未含最后锚点 {last['date']} {last['price']}）", file=sys.stderr)
        return None
    return obj


def load_wave_state(asof: str) -> dict | None:
    import psycopg2
    with psycopg2.connect(dsn(), connect_timeout=8) as conn:
        cur = conn.cursor()
        cur.execute("select as_of,level,completed,running,next_watch,rejudge_trigger,evidence "
                    "from b_rebound_wave_state where as_of<=%s order by as_of desc limit 1", (asof,))
        r = cur.fetchone()
    if not r:
        return None
    return dict(as_of=r[0], level=r[1], completed=r[2], running=r[3],
                next_watch=r[4], rejudge_trigger=r[5], evidence=r[6])


def write_wave_state(asof: str, obj: dict) -> int:
    import psycopg2
    with psycopg2.connect(dsn(), connect_timeout=8) as conn:
        cur = conn.cursor()
        cur.execute(
            "insert into b_rebound_wave_state(as_of,level,completed,running,next_watch,"
            "rejudge_trigger,evidence,source) values (%s,%s,%s,%s,%s,%s,%s,'agent') "
            "on conflict (as_of) do update set level=excluded.level, completed=excluded.completed, "
            "running=excluded.running, next_watch=excluded.next_watch, "
            "rejudge_trigger=excluded.rejudge_trigger, evidence=excluded.evidence, "
            "source='agent', created_at=now()",
            (asof, obj.get("level", ""), obj.get("completed", ""), obj.get("running", ""),
             obj.get("next_watch", ""), obj.get("rejudge_trigger", ""), obj.get("evidence", "")))
        conn.commit()
    return 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    a = ap.parse_args()
    total = 0
    for ins, name in INSTRUMENTS.items():
        try:
            df = bars(ins)
            anchors = load_anchors(ins)
            cands = candidates(df, anchors)
            elig = [c for c in cands if c["eligible"]]
            print(f"\n=== {name}（{ins}）最后锚点 "
                  f"{anchors[-1]['date'] if anchors else '无'}｜候选 {len(cands)} 个｜够格 {len(elig)} 个")
            for c in cands:
                print(f"    {c['date']} {c['price']:.3f} {c['kind']} 距今{c['age']}日 "
                      f"{'已破' if c['broken'] else '未破'} {'✅够格' if c['eligible'] else ''}")
            if not elig:
                continue
            items = ask_agent(ins, name, df, anchors, cands)
            print(f"    agent 返回 {len(items)} 项：" +
                  json.dumps(items, ensure_ascii=False)[:400])
            if a.apply:
                n = write_anchors(ins, items, elig)
                total += n
                print(f"    写入 {n} 条")
        except Exception as e:
            print(f"[error] {name}: {type(e).__name__} {str(e)[:160]}", file=sys.stderr)
    # ── 段位（上证）──
    try:
        ins = "000001.SH"
        df = bars(ins); anchors = load_anchors(ins)
        asof = str(df.loc[df.index[-1], "trade_date"])
        prev = load_wave_state(asof)
        print(f"\n=== 段位（{asof}）上一版：{(prev or {}).get('level') or '无'}")
        obj = ask_wave_state(df, anchors, prev)
        if obj:
            print("    agent：" + json.dumps(obj, ensure_ascii=False)[:400])
            if a.apply:
                write_wave_state(asof, obj); total += 1
                print("    段位已写库")
    except Exception as e:
        print(f"[error] 段位: {type(e).__name__} {str(e)[:160]}", file=sys.stderr)

    if not a.apply:
        print("\n（dry-run；加 --apply 才写库）")
    else:
        print(f"\n本次共写入 {total} 条锚点")


if __name__ == "__main__":
    main()
