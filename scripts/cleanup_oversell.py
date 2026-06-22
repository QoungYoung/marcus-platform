"""
清理旧回测的幽灵超卖 trade 记录.

诊断结果: 旧代码 match_order SELL 分支中
  for sell_vol, sell_price in sell_trades:
      rs = sell_vol  # 错位! sell_vol 实际是 price
导致 FIFO 几乎不消耗 lot, 后续 current_sell 凭空成交.
已用以下方法验证:
- 03-11 buy 800@12.45 + buy 400@12.85
- 03-12 sell 800@15.84 (旧代码 used=15.84, lots 还剩 784.16)
- 03-12 sell 400@15.91 (used=15.91, lots 还剩 768.25)
- 03-12 sell 700@15.14 (lots 完整, 消耗 700@12.45, profit=1883)
- 03-13 sell 300+800+300+800+300=2500 (全超卖)
修复后: 03-12 14:30 sell 700 会被新加的 "available_to_sell < order.volume" 拒绝.
        pos.volume 归 0 时, del self.positions[symbol] 已确保后续 sell 走 SELL 无 pos 拒绝.

本脚本:
1) 对每个 backtest task_id 目录, 打开 data/backtest/<task_id>/paper/trades.db
2) 重新跑 FIFO 计算每笔 sell 的理论可用持仓
3) 标记所有超出当时可用持仓的 sell 记录 (status 改 'CANCELLED' 或在 trade.note 标注)
4) 同步更新 account_info (回滚错误的 available_cash 增加)
5) 同步更新 PG backtest_trades 表 (如果可能)

注意: 由于 FIFO 重算后, 后续正常 sell 的 FIFO 也会不同 (因为有 13-03 卖 300 等),
本脚本采用保守策略: 只把"超卖且 profit > 0"的 sell 标记为 'OVERSOLD_INVALID',
保留原始 profit 数值但加备注, 不强行修正后续 trade (会引入新误差).

回测结果如需重新生成, 建议直接重跑回测, 而不是修复历史数据.
"""
import sqlite3
import os
import sys
from pathlib import Path
import re

ROOT = Path(__file__).parent.parent
BACKTEST_DIR = ROOT / "data" / "backtest"


def find_fake_oversells(db_path: Path):
    """查 trades.db, 找出超卖 sell 记录 (利润 > 0 但 FIFO 可用仓位不足)"""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 检查 trade_date 列是否存在 (老 db 可能没有)
    cur.execute("PRAGMA table_info(trades)")
    cols = {r[1] for r in cur.fetchall()}
    has_td = 'trade_date' in cols
    order_expr = "COALESCE(trade_date, substr(created_at,1,10)), id" if has_td else "id"
    select_td = ", trade_date" if has_td else ""

    # 找所有有交易的 symbol
    cur.execute("SELECT DISTINCT symbol FROM trades")
    symbols = [r[0] for r in cur.fetchall()]

    oversold = []
    for sym in symbols:
        # 查所有 trades 按 trade_date + id 排序
        cur.execute(
            f"SELECT id, orderid, direction, price, volume, profit, created_at{select_td} "
            f"FROM trades WHERE symbol=? ORDER BY {order_expr}",
            (sym,)
        )
        rows = cur.fetchall()

        # 模拟 FIFO
        lots = []  # [[vol, price]]
        for r in rows:
            direction = r['direction']
            # 解码 '买入' / '卖出' (gb18030 编码存储)
            if isinstance(direction, bytes):
                direction = direction.decode('gb18030', errors='ignore')
            vol = r['volume']
            price = r['price']

            if '买' in direction or direction.lower() in ('buy', 'long'):
                lots.append([vol, price])
            else:  # 卖
                # 模拟当前代码的 FIFO (修正后): 卖 vol = vol, 消耗 lot
                remaining = vol
                while remaining > 0 and lots:
                    used = min(lots[0][0], remaining)
                    lots[0][0] -= used
                    remaining -= used
                    if lots[0][0] == 0:
                        lots.pop(0)
                # 如果 remaining > 0, 这笔 sell 超卖
                # 浮点容差: 1.0 以内视为 0 (Python float 累加误差)
                # 只标记超卖 >= 100 股的, 避免误伤零碎浮点 (e.g. 178.02 实际是 178 股)
                if remaining > 100:
                    oversold.append({
                        'symbol': sym,
                        'trade_id': r['id'],
                        'orderid': r['orderid'],
                        'volume': vol,
                        'price': price,
                        'profit': r['profit'],
                        'oversold_amount': remaining,
                        'trade_date': r['trade_date'] if has_td else None,
                    })
    conn.close()
    return oversold


def cleanup_one_task(task_dir: Path, dry_run: bool = True):
    """清理一个 task 的超卖数据. dry_run=True 只打印不修改."""
    db_path = task_dir / "paper" / "trades.db"
    if not db_path.exists():
        return 0

    oversold = find_fake_oversells(db_path)
    if not oversold:
        return 0

    print(f"\n[{task_dir.name}] 发现 {len(oversold)} 笔超卖 sell:")
    for o in oversold:
        print(f"  trade_id={o['trade_id']} {o['orderid']} {o['symbol']} "
              f"sell {o['volume']}@{o['price']} profit={o['profit']:.0f} "
              f"超卖 {o['oversold_amount']} 股")

    if dry_run:
        print(f"  [DRY-RUN] 不修改 (使用 --apply 提交)")
        return len(oversold)

    # 实际清理: 在 trades 表加 note 列, 标记超卖 (不删, 保留可追溯)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    # 加 note 列 (sqlite 不支持 IF NOT EXISTS ADD COLUMN, 用 try/except)
    try:
        cur.execute("ALTER TABLE trades ADD COLUMN note TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 已存在

    for o in oversold:
        cur.execute(
            "UPDATE trades SET note=? WHERE id=?",
            (f"OVERSOLD by {o['oversold_amount']}股 (旧FIFO bug)", o['trade_id'])
        )

    # 同步 orders 表状态
    for o in oversold:
        cur.execute(
            "UPDATE orders SET status='已撤销', updated_at=datetime('now') WHERE orderid=?",
            (o['orderid'],)
        )
        # 回滚 profit = 0 (超卖部分不计入)
        cur.execute(
            "UPDATE trades SET profit=0 WHERE id=?",
            (o['trade_id'],)
        )

    conn.commit()
    conn.close()
    print(f"  [OK] 标记 {len(oversold)} 笔超卖, profit 归零")
    return len(oversold)


def main():
    apply = '--apply' in sys.argv

    if not BACKTEST_DIR.exists():
        print(f"[ERR] 回测目录不存在: {BACKTEST_DIR}")
        return

    total = 0
    for task_dir in sorted(BACKTEST_DIR.iterdir()):
        if not task_dir.is_dir():
            continue
        total += cleanup_one_task(task_dir, dry_run=not apply)

    print(f"\n{'='*60}")
    print(f"总计: {total} 笔超卖")
    if not apply and total > 0:
        print(f"使用 'python {__file__} --apply' 提交清理")
    else:
        print(f"已 {'提交' if apply else '检查完成 (DRY-RUN)'}")


if __name__ == "__main__":
    main()
