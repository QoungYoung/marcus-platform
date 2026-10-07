# -*- coding: utf-8 -*-
"""wolf_selection_reasons.py — 用 dsh 归类「狼大**为什么**选这个方向」（2026-09-12）。

**目的**：回答"他怎么选主线"——不是我们推测，而是从他的**操作 + 原话证据**里归类依据。

输入：`wolf_actual_mainline`（dsh 直读的 `doing[]`：dir / action / evidence）+ 当日 `market_view`。
每批 ≤12 条送 dsh，要求对每条给出：
  · reasons（**可多选，最多 2 个**）：B1 低位/回调埋伏｜B2 相对强弱（同链条跌得少/先企稳）｜
    B3 资金结构（获利盘/长期资金/筹码/公募持仓/拥挤度）｜B4 量能与盘口（放量缩量/黄白线/两融）｜
    B5 消息与政策催化（研报/新闻/事件）｜B6 主线竞争与轮动｜B7 持仓延续/成本/做T｜B8 其他或未说明
  · quote：依据原话（取自 evidence 或当日 market_view）
  · confidence：high / mid / low
落表 `wolf_selection_reasons`，并打印分布与代表性原话。

用法：python jobs/wolf_selection_reasons.py [--limit N] [--sleep 1.0]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List


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


sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")

CHAT_URL = os.getenv("MAIN_LINE_CHAT_URL", "http://marcus-dsh:3001/chat")
BATCH = int(os.getenv("WOLF_SR_BATCH", "12"))

DDL = """
CREATE TABLE IF NOT EXISTS wolf_selection_reasons (
    trade_date VARCHAR(8) NOT NULL,
    seq        INTEGER    NOT NULL,
    dir_text   TEXT,
    action     TEXT,
    reasons    JSONB,
    quote      TEXT,
    confidence TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, seq)
)
"""

REASON_DESC = {
    "B1": "低位/回调埋伏（没涨、跌下来、缩量）",
    "B2": "相对强弱（同链条跌得少/先企稳/强于大盘）",
    "B3": "资金结构（获利盘/长期资金/筹码集中度/公募持仓/拥挤度）",
    "B4": "量能与盘口（放量/缩量/黄白线/两融）",
    "B5": "消息与政策催化（研报/新闻/政策/事件）",
    "B6": "主线竞争与轮动（方向之间竞争/轮动切换）",
    "B7": "持仓延续/成本/做T（已持有、摊成本、做T）",
    "B8": "其他或未说明",
}


def _extract_json(reply: str) -> Dict[str, Any]:
    s = (reply or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if m:
        s = m.group(1)
    else:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j + 1]
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else {}
    except Exception:
        return {}


def load_entries(db) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    out = []
    for r in db.execute(text("SELECT trade_date, payload FROM wolf_actual_mainline "
                             "WHERE status='ok' ORDER BY trade_date")).mappings().all():
        p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"] or "{}")
        mv = ""
        for i, x in enumerate(p.get("doing") or []):
            out.append({"date": r["trade_date"], "seq": i, "dir": x.get("dir"),
                        "action": x.get("action"), "evidence": x.get("evidence"),
                        "market_view": mv})
    return out


def ask(entries: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    import requests
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception as _e_sil1:
        _silent_alert("wolf_selection_reasons.py:94", _e_sil1)
    lines = ["你在分析一位A股交易者（狼大）**为什么选某个方向**。下面每条是他当天的一个操作，",
             "请判断他选/持有这个方向的**依据**，类别（**最多选 2 个**）："]
    for k, v in REASON_DESC.items():
        lines.append("  %s = %s" % (k, v))
    lines += ["", "要求：依据必须能在 evidence 里找到支撑；找不到就填 B8 并把 confidence 设为 low；",
              "quote 从 evidence 里**原样截取**（不要改写）。", "",
              "只输出 JSON，不要 markdown：",
              '{"items":[{"date":"YYYYMMDD","seq":0,"dir":"方向","reasons":["B1","B4"],"quote":"原话","confidence":"high|mid|low"}]}', ""]
    for i, e in enumerate(entries):
        lines.append("%d) date=%s dir=%s action=%s" % (i, e["date"], e.get("dir"), e.get("action")))
        lines.append("   evidence: " + str(e.get("evidence") or "")[:220])
    r = requests.post(CHAT_URL, json={"message": "\n".join(lines),
                                      "session_id": "wolfsr_%s_%d" % (entries[0]["date"], entries[0]["seq"])},
                      headers={"Content-Type": "application/json"}, timeout=240, verify=False)
    r.raise_for_status()
    obj = _extract_json(str((r.json() or {}).get("reply") or ""))
    res = {}
    for it in (obj.get("items") or []):
        try:
            key = (str(it.get("date")), int(it.get("seq")))
            res[key] = it
        except Exception as _e_sil2:
            _silent_alert("wolf_selection_reasons.py:117", _e_sil2)
            continue
    return res


def main() -> int:
    from app.database import SessionLocal
    from sqlalchemy import text
    argv = sys.argv
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    sleep_sec = 1.0
    if "--sleep" in argv:
        sleep_sec = float(argv[argv.index("--sleep") + 1])
    db = SessionLocal()
    try:
        db.execute(text(DDL))
        db.commit()
        entries = load_entries(db)
        if limit:
            entries = entries[:limit]
        print("[sr] 待归类 %d 条，每批 %d" % (len(entries), BATCH), flush=True)
        done = 0
        for i in range(0, len(entries), BATCH):
            chunk = entries[i:i + BATCH]
            got: Dict[Any, Dict[str, Any]] = {}
            for att in (1, 2):
                try:
                    got = ask(chunk)
                    if got:
                        break
                except Exception as e:
                    print("[sr] 批 %d 第 %d 次失败: %s: %s" % (i // BATCH + 1, att, type(e).__name__, str(e)[:60]), flush=True)
                    time.sleep(3)
            for e in chunk:
                it = got.get((e["date"], e["seq"])) or {}
                rs = [r for r in (it.get("reasons") or []) if r in REASON_DESC][:2] or ["B8"]
                db.execute(text("""
                    INSERT INTO wolf_selection_reasons (trade_date, seq, dir_text, action, reasons, quote, confidence)
                    VALUES (:d, :s, :dir, :a, CAST(:r AS jsonb), :q, :c)
                    ON CONFLICT (trade_date, seq) DO UPDATE
                      SET reasons=EXCLUDED.reasons, quote=EXCLUDED.quote, confidence=EXCLUDED.confidence
                """), {"d": e["date"], "s": e["seq"], "dir": e.get("dir"), "a": e.get("action"),
                       "r": json.dumps(rs), "q": (it.get("quote") or "")[:500],
                       "c": it.get("confidence") or "low"})
                done += 1
            db.commit()
            print("[sr] %d/%d 已归类" % (min(i + BATCH, len(entries)), len(entries)), flush=True)
            time.sleep(max(0.0, sleep_sec))
        # 汇总
        import collections
        cnt = collections.Counter()
        for (rs,) in db.execute(text("SELECT reasons FROM wolf_selection_reasons")).all():
            v = rs if isinstance(rs, list) else json.loads(rs or "[]")
            for r in v:
                cnt[r] += 1
        tot = sum(1 for _ in db.execute(text("SELECT 1 FROM wolf_selection_reasons")).all())
        print("[sr] === 依据分布（共 %d 条，可多选）===" % tot, flush=True)
        for k, v in cnt.most_common():
            print("   %s %-46s %4d 次 (%.0f%%)" % (k, REASON_DESC[k], v, 100.0 * v / max(1, tot)), flush=True)
        for k, _ in cnt.most_common(4):
            rows = db.execute(text("SELECT trade_date, dir_text, quote FROM wolf_selection_reasons "
                                   "WHERE reasons ? :k AND quote <> '' ORDER BY random() LIMIT 3"),
                              {"k": k}).all()
            print("[sr] %s 代表原话：" % k, flush=True)
            for d, dr, q in rows:
                print("     %s %s → %s" % (d, dr, (q or "")[:110]), flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
