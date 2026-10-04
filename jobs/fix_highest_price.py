# -*- coding: utf-8 -*-
"""fix_highest_price.py —— 按"买入日之后的真实最高价"重算 `paper_positions.highest_price`（账本 §9.532）

**为什么需要**（用户指出「埋伏仓卖出总闸是配套设计，不存在挡住」✓ —— 他对，我定性错了 ✗）：
  · 「埋伏仓卖出总闸」**只在未转正时生效** ✓；**转正后**代码原文即「**不再享受埋伏豁免，交回普通管理**」✓
  · 而**转正的判据** ＝ `highest_price ≥ 成本×(1+WOLF_AMBUSH_PROMOTE_PCT%)`（pins=10 ✓）
  · ⚠️ 但 `highest_price` 平时由 `stop_loss_monitor` 更新 ✗ —— **它在回测里不跑** ✗（`t_monitor:2913` 注释已记 ✓）
    由 `t_monitor:2940/2968` 兜底 ✓；**而反复重置账户后，它会被"当时的价"重标** ✗
    （实测：`SH603629` 成本 49.03 ⇒ 记录最高 **42.41** ✗，而真实区间最高 **119.35** ✓）
  ⇒ ⇒ **转正判据永远为假 ⇒ 豁免永远生效 ⇒ 不止盈** ✗

**本脚本**：按 `entry_date` 之后的**真实日线最高**重算 ✓（无前视：只取 ≤ 指定日 ✓）
用法：`.venv/bin/python jobs/fix_highest_price.py [--account drabt35] [--upto 20260428] [--apply]`
"""
from __future__ import annotations
import os, sqlite3, sys


def conv(s: str) -> str:
    s = str(s).strip().upper()
    return (s[2:] + "." + s[:2]) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s


def main() -> int:
    acc, upto, apply = "drabt35", "20260428", False
    for i, a in enumerate(sys.argv):
        if a == "--account" and i + 1 < len(sys.argv): acc = sys.argv[i + 1]
        if a == "--upto" and i + 1 < len(sys.argv): upto = sys.argv[i + 1]
        if a == "--apply": apply = True
    import psycopg2
    dsn = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
    cc = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" %
                         os.path.dirname(os.path.dirname(os.path.abspath(__file__))), uri=True)
    cc.execute("PRAGMA temp_store=MEMORY")
    cn = psycopg2.connect(dsn, connect_timeout=4)
    cn.autocommit = True
    c = cn.cursor()
    c.execute("SELECT symbol, avg_price, highest_price, entry_date FROM paper_positions WHERE account_id=%s", (acc,))
    rows = c.fetchall()
    n_fix = 0
    for sym, ap, hp, ed in rows:
        try:
            d0 = str(ed).replace("-", "")[:8]
            r = list(cc.execute("SELECT max(close), trade_date FROM bars WHERE ts_code=? AND trade_date>=? AND trade_date<=?",
                                (conv(sym), d0, upto)))
            real = float(r[0][0]) if r and r[0][0] else 0.0
        except Exception:
            real = 0.0
        if real > 0 and abs(real - float(hp or 0)) > 0.005:
            n_fix += 1
            promo = real >= float(ap or 0) * 1.10
            print("  %-9s 成本%7.2f｜记录最高%7.2f ⇒ **真实最高%7.2f**（%+.1f%%）⇒ 转正=%s %s"
                  % (sym, float(ap or 0), float(hp or 0), real, (real / float(ap or 0) - 1) * 100 if ap else 0,
                     "**是 ✓**" if promo else "否", "→ 已修 ✓" if apply else "(演练 ✓，加 --apply 落库)"))
            if apply:
                c.execute("UPDATE paper_positions SET highest_price=%s, updated_at=now() WHERE account_id=%s AND symbol=%s",
                          (real, acc, sym))
    print("  ⇒ 需修 %d / %d 只 ✓%s" % (n_fix, len(rows), "（已落库 ✓）" if apply else "（演练模式 ✓）"))
    cn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
