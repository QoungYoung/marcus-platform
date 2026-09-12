# -*- coding: utf-8 -*-
"""wolf_actual_mainline_llm.py — 用 **dsh 直读**判定「狼大真正在做的方向」（2026-09-12）。

**为什么不用关键词**（用户明确要求）：关键词会把"他提到/评论/明确不做"的方向也算成关注
——例如他说过「我不做红利」「不做海外链」「我看不懂，我表示不参与」，这些恰恰**不是**他在做的。
本脚本让 LLM 只读**他自己的操作记录**，输出"在做什么 / 明确不做什么"。

**输入**：`seg2026_merged.json` 里该日的 `actions`（kind/what/quote）与 `market_view`
（都是我们**逐条精读**出来的结构化记录，quote 为原话）。
**输出**（存库 + JSON）：每天一个对象
  { "doing":   [{"dir":"自由文本方向(用他的说法)","action":"建仓/加仓/减仓/做T/持有/埋伏","evidence":"原话"}],
    "not_doing":[{"dir":"...","why":"原话"}],
    "notes": "无法判断的部分" }
⚠️ **方向名保持他的说法**（如"液冷/国算/大光/药/红利"），**不**强行映射到我们的 13 主题；
   映射留作单独一步（同样用 LLM，不用关键词）。

通道：dsh `POST http://marcus-dsh:3001/chat {message, session_id}` → `{reply}`（项目既有的用法）。
用法：python jobs/wolf_actual_mainline_llm.py [--limit N] [--from 20260105] [--to 20260814] [--sleep 1.5]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")

CHAT_URL = os.getenv("MAIN_LINE_CHAT_URL", "http://marcus-dsh:3001/chat")
CORPUS = os.getenv("WOLF_CORPUS", "/app/data/_bt_batch2/seg2026_merged.json")
TIMEOUT = float(os.getenv("WOLF_LLM_TIMEOUT", "180"))

DDL = """
CREATE TABLE IF NOT EXISTS wolf_actual_mainline (
    trade_date VARCHAR(8) NOT NULL PRIMARY KEY,
    status     VARCHAR(12) NOT NULL DEFAULT 'pending',
    n_doing    INTEGER DEFAULT 0,
    attempts   INTEGER DEFAULT 0,
    error      TEXT,
    reply_raw  TEXT,
    payload    JSONB,
    fetched_at TIMESTAMPTZ
)
"""


def ensure_table(db) -> None:
    from sqlalchemy import text
    db.execute(text(DDL))
    db.commit()


def prompt_for(d8: str, actions: List[Dict[str, Any]], market_view: str) -> str:
    lines = [
        "你在帮我判定一位A股交易者（网名『狼大』）**当天真正在做什么方向**。",
        "规则（务必遵守）：",
        "1. 只依据下面【他自己的操作】列表判断，**不要**把他评论别人/回答提问/讲道理的内容算作他在做；",
        "2. 他明确说『不做/不参与/看不懂/不去』的方向，放进 not_doing，**不算** doing；",
        "3. 方向名请**沿用他的说法**（例如：半导体/大光/光存/CPO/液冷/国算/AI软/券商/电力/航天/药/红利…），"
        "**不要**改写成别的分类体系；",
        "4. 每条都要给出**原话证据**（从 quote 里截取）；判断不了就写进 notes，不要猜。",
        "",
        "【日期】" + d8,
        "【他当天的行情判断】" + (market_view[:400] if market_view else "（无）"),
        "",
        "【他自己的操作】",
    ]
    for i, a in enumerate(actions[:12], 1):
        lines.append("%d) [%s] %s" % (i, a.get("kind") or "", (a.get("what") or "")[:200]))
        q = (a.get("quote") or "")[:260]
        if q:
            lines.append("   原话: " + q)
    if not actions:
        lines.append("（当天没有记录到他的操作）")
    lines += [
        "",
        "只输出一个 JSON 对象（不要 markdown、不要多余文字）：",
        '{"doing":[{"dir":"方向(他的说法)","action":"建仓/加仓/减仓/做T/持有/埋伏/观察","evidence":"原话"}],'
        '"not_doing":[{"dir":"方向","why":"原话"}],"notes":"无法判断的部分"}',
    ]
    return "\n".join(lines)


def _extract_json(reply: str) -> Optional[Dict[str, Any]]:
    if not reply:
        return None
    s = reply.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        s = m.group(1)
    else:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j + 1]
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def call_dsh(d8: str, message: str) -> Dict[str, Any]:
    import requests
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    body = {"message": message, "session_id": "wolfmain_" + d8}
    r = requests.post(CHAT_URL, json=body, headers={"Content-Type": "application/json"},
                      timeout=TIMEOUT, verify=False)
    r.raise_for_status()
    return r.json() or {}


def load_corpus(path: str) -> Dict[str, Dict[str, Any]]:
    d = json.load(open(path, encoding="utf-8"))
    out: Dict[str, Dict[str, Any]] = {}
    for day in d.get("days", []):
        dt = (day.get("date") or "").replace("-", "")
        if dt:
            out[dt] = {"actions": day.get("actions") or [], "market_view": day.get("market_view") or ""}
    return out


def run(days: List[str], corpus: Dict[str, Dict[str, Any]], sleep_sec: float = 1.5,
        save: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, "days": len(days), "ok_days": 0, "failed": [], "n_doing": 0}
    db = None
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        ensure_table(db)
        for i, d8 in enumerate(days, 1):
            rec = corpus.get(d8) or {"actions": [], "market_view": ""}
            msg = prompt_for(d8, rec["actions"], rec["market_view"])
            status, payload, raw, err = "ok", None, None, None
            for att in (1, 2):
                try:
                    j = call_dsh(d8, msg)
                    raw = str(j.get("reply") or "")[:20000]
                    payload = _extract_json(raw)
                    if payload is None:
                        raise ValueError("reply 不是合法 JSON")
                    status = "ok"
                    break
                except Exception as e:
                    err = "%s:%s" % (type(e).__name__, str(e)[:80])
                    status = "failed"
                    time.sleep(2.0 * att)
            n_doing = len((payload or {}).get("doing") or [])
            if status == "ok":
                out["ok_days"] += 1
                out["n_doing"] += n_doing
            else:
                out["failed"].append(d8)
            if save:
                try:
                    db.execute(text("""
                        INSERT INTO wolf_actual_mainline (trade_date, status, n_doing, attempts, error, reply_raw, payload, fetched_at)
                        VALUES (:d, :st, :n, 2, :err, :raw, CAST(:p AS jsonb), now())
                        ON CONFLICT (trade_date) DO UPDATE
                          SET status=EXCLUDED.status, n_doing=EXCLUDED.n_doing, error=EXCLUDED.error,
                              reply_raw=EXCLUDED.reply_raw, payload=EXCLUDED.payload, fetched_at=now()
                    """), {"d": d8, "st": status, "n": n_doing, "err": err, "raw": raw,
                           "p": json.dumps(payload or {}, ensure_ascii=False)})
                    db.commit()
                except Exception as e:
                    db.rollback()
                    out["failed"].append("%s(db:%s)" % (d8, str(e)[:40]))
            doing = ", ".join(x.get("dir", "") for x in ((payload or {}).get("doing") or [])[:4])
            print("[main] %d/%d %s %s doing=[%s]%s" % (i, len(days), d8, status, doing,
                  (" err=%s" % err) if err else ""), flush=True)
            time.sleep(max(0.0, float(sleep_sec)))
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:120]
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
    return out


def main() -> int:
    argv = sys.argv
    corpus_p = CORPUS
    if "--corpus" in argv:
        corpus_p = argv[argv.index("--corpus") + 1]
    corpus = load_corpus(corpus_p)
    days = sorted(corpus.keys())
    if "--from" in argv:
        d0 = argv[argv.index("--from") + 1].replace("-", "")
        days = [d for d in days if d >= d0]
    if "--to" in argv:
        d1 = argv[argv.index("--to") + 1].replace("-", "")
        days = [d for d in days if d <= d1]
    if "--limit" in argv:
        days = days[:int(argv[argv.index("--limit") + 1])]
    sleep_sec = 1.5
    if "--sleep" in argv:
        sleep_sec = float(argv[argv.index("--sleep") + 1])
    save = "--dry-run" not in argv
    print("[main] dsh 直读判定：%d 天（%s→%s）%s" % (len(days), days[0] if days else "-",
          days[-1] if days else "-", "" if save else " [dry-run]"), flush=True)
    res = run(days, corpus, sleep_sec=sleep_sec, save=save)
    print("[main] 汇总:", {k: res.get(k) for k in ("ok", "days", "ok_days", "n_doing")},
          "| 失败:", (res.get("failed") or [])[:8], flush=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
