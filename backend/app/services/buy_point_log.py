# -*- coding: utf-8 -*-
"""buy_point_log.py — 系统每日实际买入动作日志（P0：让 ±5日一致率可算、可回放）

只做记录，不做任何拦截/执行变更。每条 = 一次实际发出的自动买入动作：
  at/date/source(候选池|长期池|T腿|Pi)/symbol/account/intent/price/volume/amount/
  pct/final_grade/tier_cap/trigger/reason
输出: data/buy_point_log.jsonl（append，幂等无锁保护由单行 append 近似提供）。
开关: BUY_POINT_LOG_ENABLED=0 可关闭（默认开）。
"""
import json
import os
from datetime import datetime

def _path():
    try:
        from app.config import get_settings
        ws = str(get_settings().workspace_path)
    except Exception:
        ws = os.environ.get("MARCUS_WORKSPACE", "/app")
    return os.path.join(ws, "data", "buy_point_log.jsonl")

def enabled():
    return os.getenv("BUY_POINT_LOG_ENABLED", "1").strip().lower() not in ("0", "false", "no")

def log_buy_point(source, symbol, name="", account="stock", intent="new_base",
                  price=None, volume=None, amount=None, pct=None,
                  grade=None, tier_cap=None, trigger=None, reason="", **extra) -> bool:
    """记录一次实际买入动作；失败只打印不抛异常。"""
    if not enabled():
        return False
    try:
        now = datetime.now()
        row = {
            "at": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "source": source,
            "symbol": symbol,
            "name": name or "",
            "account": account,
            "intent": intent,
            "price": float(price) if price is not None else None,
            "volume": int(volume) if volume is not None else None,
            "amount": round(float(amount), 2) if amount is not None else None,
            "pct": round(float(pct), 4) if pct is not None else None,
            "final_grade": grade or "",
            "tier_cap_pct": float(tier_cap) if tier_cap is not None else None,
            "trigger": trigger or "",
            "reason": reason or "",
        }
        for k, v in (extra or {}).items():
            row.setdefault(str(k), v)
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print("[buy_point_log] 写入失败:", e)
        return False

def read_recent(n=20):
    """读取最近 n 条（复盘/评测用）。"""
    out = []
    try:
        path = _path()
        if not os.path.exists(path):
            return out
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        return out[-n:]
    except Exception:
        return out

if __name__ == "__main__":
    log_buy_point(source="smoke", symbol="SH600000", name="测试", intent="new_base",
                  price=10.0, volume=100, amount=1000.0, pct=0.5, grade="pass")
    print("recent:", json.dumps(read_recent(1), ensure_ascii=False)[:400])
