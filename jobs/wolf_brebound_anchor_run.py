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


def gen_anchors(df: pd.DataFrame, n_hold: int = 4, gap: float = 0.08, move: float = 0.10) -> list[dict]:
    """按三条**可计算**规则生成结构点（替代「被他引用过」这条无法执行的判据）。

    ① 一段只留极值：与上一个同类点价差 <gap 的不单独成点（同向时更极端者胜，替换旧的）
    ② 时效确认：之后 n_hold 个交易日不再破
    ③ 级别拐点：之后 20 日反向幅度 ≥ move（这条把小波动全滤掉）
    level 由反向幅度机械定：≥25% 记大级别，否则小级别。
    回测（2021-2026）：上证 23→1.1 个/年，新能车 29→6.2，机器人 29→6.7。
    """
    c, h, l, dates = df.close.values, df.high.values, df.low.values, df.trade_date.values
    out: list[dict] = []
    for i in range(5, len(df) - 25):
        is_low = l[i] == l[i - 5:i + 6].min()
        is_high = h[i] == h[i - 5:i + 6].max()
        if not (is_low or is_high):
            continue
        kind = "low" if is_low else "high"
        px = float(l[i] if is_low else h[i])
        if is_low and l[i + 1:i + 1 + n_hold].min() < px:
            continue
        if is_high and h[i + 1:i + 1 + n_hold].max() > px:
            continue
        fwd = (h[i + 1:i + 21].max() / px - 1) if is_low else (1 - l[i + 1:i + 21].min() / px)
        if fwd < move:
            continue
        prev = [x for x in out if x["kind"] == kind]
        if prev and abs(px / prev[-1]["price"] - 1) < gap:
            if (is_low and px > prev[-1]["price"]) or (is_high and px < prev[-1]["price"]):
                continue
            out = [x for x in out if not (x["kind"] == kind and x["price"] == prev[-1]["price"])]
        out.append(dict(kind=kind, date=str(dates[i]), price=px,
                        level=("大级别" if fwd >= 0.25 else "小级别"),
                        fwd_move=round(fwd * 100, 1),
                        age=int(len(df) - 1 - i), broken=False, eligible=True))
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
        "程序已经按三条可计算规则筛过候选（你看到的都过关）：\n"
        "  ① 与上一个同类点价差 ≥8%（一段只留极值）\n"
        "  ② 之后 4 个交易日不再破\n"
        "  ③ 之后 20 日反向幅度 ≥10%（级别拐点）\n"
        "你的任务只是**给它起名和定性**，不要再自己造点：\n"
        "  1. label 用人话（如「4-3低」「B反高点」「前一轮顶」），不要生造「X高」「次高」这类没信息量的名字。\n"
        "  2. role 三选一：目标位（用来算空间）/ 已出现（已确认的拐点）/ 待确认（还在走）。\n"
        "  3. 若该点与他某次发言里提到的点位明显对应，写进 evidence；对不上就只写价格依据。\n"
        "  4. 拿不准就 action=reject。\n\n"
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
    lv = {(str(c["date"]), round(float(c["price"]), 3), c["kind"]): c.get("level", "小级别")
          for c in eligible}
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
                    "evidence,confirmed_at,created_by,level) values (%s,%s,%s,%s,%s,%s,'confirmed',%s,now(),'agent',%s) "
                    "on conflict (instrument,session_date,kind) do nothing",
                    (ins, str(it["session_date"]), float(it["price"]), str(it["kind"]),
                     (str(it.get("label") or "").strip() or ("低点" if str(it.get("kind")) == "low" else "高点"))[:32],
                     str(it.get("role") or "已出现")[:16],
                     str(it.get("evidence") or "")[:500], lv.get(key, "小级别")))
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
        '{"level_key":"稳定短标签，从这些里选一个 4-2反弹|4-3下跌|4-4反弹|4-5下跌|下跌1浪|B反中|看不清",'
        '"level":"","completed":"","running":"","next_watch":"","rejudge_trigger":"","evidence":"一句话，引用数字"}'
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
        cur.execute("select as_of,level,completed,running,next_watch,rejudge_trigger,evidence,level_key "
                    "from b_rebound_wave_state where as_of<=%s order by as_of desc limit 1", (asof,))
        r = cur.fetchone()
    if not r:
        return None
    return dict(as_of=r[0], level=r[1], completed=r[2], running=r[3],
                next_watch=r[4], rejudge_trigger=r[5], evidence=r[6], level_key=r[7])


def write_wave_state(asof: str, obj: dict) -> int:
    import psycopg2
    with psycopg2.connect(dsn(), connect_timeout=8) as conn:
        cur = conn.cursor()
        cur.execute(
            "insert into b_rebound_wave_state(as_of,level,completed,running,next_watch,"
            "rejudge_trigger,evidence,source,level_key) values (%s,%s,%s,%s,%s,%s,%s,'agent',%s) "
            "on conflict (as_of) do update set level=excluded.level, completed=excluded.completed, "
            "running=excluded.running, next_watch=excluded.next_watch, "
            "rejudge_trigger=excluded.rejudge_trigger, evidence=excluded.evidence, "
            "level_key=excluded.level_key, "
            "source='agent', created_at=now()",
            (asof, obj.get("level", ""), obj.get("completed", ""), obj.get("running", ""),
             obj.get("next_watch", ""), obj.get("rejudge_trigger", ""), obj.get("evidence", ""),
             str(obj.get("level_key", ""))[:24]))
        conn.commit()
    return 1


def push(text: str) -> bool:
    try:
        from core.qq_notifier import send_c2c_message
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[warn] 未配置 QQ_BOT_RECIPIENT，跳过推送", file=sys.stderr)
            return False
        return bool(send_c2c_message(openid, text))
    except Exception as e:
        print(f"[warn] 推送异常: {str(e)[:100]}", file=sys.stderr)
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    a = ap.parse_args()
    try:
        from b_rebound_schema import ensure_schema          # 直跑：sys.path[0] = jobs/
    except ImportError:                                     # 被当包导入（pytest 等）
        from jobs.b_rebound_schema import ensure_schema
    try:
        ensure_schema()      # 本地/新环境缺这两张表时自愈（2026-10-30 14:50 就是缺表 ✗）
    except Exception as e:
        print(f"[warn] 建表失败（继续，读不到会走文件兜底）: {str(e)[:90]}", file=sys.stderr)
    total = 0
    added: list[str] = []
    wave_change = ""
    for ins, name in INSTRUMENTS.items():
        try:
            df = bars(ins)
            anchors = load_anchors(ins)
            cands = gen_anchors(df)
            elig = cands
            print(f"\n=== {name}（{ins}）最后锚点 "
                  f"{anchors[-1]['date'] if anchors else '无'}｜规则生成 {len(cands)} 个｜库中已有 {len(anchors)} 个")
            for c in cands:
                print(f"    {c['date']} {c['price']:.3f} {c['kind']} {c['level']} "
                      f"反向{c['fwd_move']}% 距今{c['age']}日")
            if not elig:
                continue
            items = ask_agent(ins, name, df, anchors, cands)
            print(f"    agent 返回 {len(items)} 项：" +
                  json.dumps(items, ensure_ascii=False)[:400])
            if a.apply:
                n = write_anchors(ins, items, elig)
                total += n
                for it in items:
                    if str(it.get("action")) == "confirm" and \
                       (str(it.get("session_date")), round(float(it.get("price", 0)), 3),
                        str(it.get("kind"))) in {(str(c["date"]), round(float(c["price"]), 3), c["kind"]) for c in elig}:
                        _lab = (str(it.get("label") or "").strip()
                                or ("低点" if str(it.get("kind")) == "low" else "高点"))
                        added.append(f"{name} {it['session_date']} {float(it['price']):.3f} {_lab}")
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
                oldk, newk = (prev or {}).get("level_key"), obj.get("level_key")
                if newk and oldk != newk:
                    wave_change = f"段位改判：{oldk or '无'} → {newk}"
    except Exception as e:
        print(f"[error] 段位: {type(e).__name__} {str(e)[:160]}", file=sys.stderr)

    if not a.apply:
        print("\n（dry-run；加 --apply 才写库）")
    else:
        print(f"\n本次共写入 {total} 条（锚点 {len(added)} + 段位）")
        if added or wave_change:
            lines = ["【B反锚点】"]
            for x in added[:4]:
                lines.append(f"＋ {x}")
            if wave_change:
                lines.append(wave_change[:110])
            push("\n".join(lines))
            print("[QQ] 已推送")
        else:
            print("[QQ] 无变化，静默")


if __name__ == "__main__":
    main()
