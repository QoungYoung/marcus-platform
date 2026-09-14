# -*- coding: utf-8 -*-
"""R9 数据积累：盘口五档快照记录（只记不用）。

用法（本地/容器）:
  python jobs/record_orderbook.py --once                      # 记一次（持仓标的）
  python jobs/record_orderbook.py --symbols SH588170,SH512480 --once
  python jobs/record_orderbook.py --loop --interval 300        # 交易时段每 5 分钟记一次
标的默认 = 当前持仓（stock 账户）。
"""
import argparse, os, sys, time
from datetime import datetime

ROOT = "/app" if os.path.isdir("/app/app") else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))


def _held_symbols() -> list:
    try:
        from app.services.t_gateway import get_sellable_ledger
        return sorted((get_sellable_ledger(account_id=os.getenv("WOLF_POSITION_ACCOUNT", "stock")) or {}).keys())
    except Exception as e:
        print("[orderbook] 取持仓失败:", str(e)[:100])
        return []


def _in_trading() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.strftime("%H:%M")
    return ("09:30" <= hm <= "11:30") or ("13:00" <= hm <= "15:00")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--tag", default="r9")
    args = ap.parse_args()
    from app.services.t_orderbook import record
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or _held_symbols()
    if not syms:
        print("[orderbook] 无标的"); return 1
    if args.once:
        print("[orderbook] 记一次 %s → %d 行" % (syms, record(syms, tag=args.tag)))
        return 0
    print("[orderbook] 循环模式 每 %ds（仅交易时段）" % args.interval)
    while True:
        try:
            if _in_trading():
                n = record(syms, tag=args.tag)
                print("[orderbook] %s 写入 %d 行" % (datetime.now().strftime("%H:%M:%S"), n), flush=True)
        except Exception as e:
            print("[orderbook] 异常:", str(e)[:120])
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
