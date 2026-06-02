# 数据持久化说明

## ✅ 是的，数据可以永久保存！

VN.PY 模拟交易 Skill 支持完整的数据持久化功能。

---

## 📁 数据存储位置

```
data/
├── account.json      # 账户状态（JSON 格式）
└── trades.db         # 交易记录（SQLite 数据库）
```

---

## 🗄️ 存储内容

### 1. account.json - 账户状态

```json
{
  "initial_capital": 1000000.0,
  "available_cash": 807153.62,
  "frozen_cash": 96.38,
  "positions": {
    "SH600519": {
      "symbol": "SH600519",
      "volume": 100,
      "avg_price": 1700.50
    },
    "SZ000858": {
      "symbol": "SZ000858",
      "volume": 500,
      "avg_price": 45.48
    }
  },
  "updated_at": "2026-03-04T16:03:06"
}
```

**包含：**
- ✅ 初始资金
- ✅ 可用资金
- ✅ 冻结资金
- ✅ 当前持仓（代码、数量、成本价）
- ✅ 最后更新时间

### 2. trades.db - 交易记录（SQLite）

**orders 表 - 订单记录：**
| 字段 | 说明 |
|------|------|
| orderid | 订单号 |
| symbol | 标的代码 |
| direction | 方向（买入/卖出） |
| price | 委托价格 |
| volume | 委托数量 |
| status | 订单状态 |
| created_at | 创建时间 |

**trades 表 - 成交记录：**
| 字段 | 说明 |
|------|------|
| orderid | 关联订单号 |
| symbol | 标的代码 |
| direction | 方向 |
| price | 成交价格 |
| volume | 成交数量 |
| amount | 成交金额 |
| profit | 盈亏（卖出时） |
| created_at | 成交时间 |

---

## 🔍 查询数据

### 命令行查询

```bash
# 查询账户
python3 query.py account

# 查询持仓
python3 query.py positions

# 查询订单
python3 query.py orders -l 50

# 查询成交
python3 query.py trades -l 50

# 查询某只股票的成交
python3 query.py trades -s SH600519

# 查询盈亏
python3 query.py profit
```

### Python API 查询

```python
from paper_engine import PaperTradingEngine

# 创建引擎（会自动加载已有数据）
engine = PaperTradingEngine(data_dir="./data")

# 查询账户
info = engine.get_account_info()
print(info)

# 查询持仓
positions = engine.get_positions()
for pos in positions:
    print(f"{pos['symbol']}: {pos['volume']}股 @ {pos['avg_price']}")

# 查询订单
orders = engine.get_orders(limit=10)
for o in orders:
    print(f"{o['orderid']}: {o['symbol']} {o['direction']} @ {o['price']}")

# 查询成交
trades = engine.get_trades(limit=10)
for t in trades:
    print(f"{t['symbol']}: {t['volume']}股 @ {t['price']}, 盈亏：{t['profit']}")

# 查询盈亏汇总
summary = engine.get_profit_summary()
print(f"总盈亏：{summary['总盈亏']}")
```

---

## 📊 数据示例

### 买入后查询

```bash
# 执行买入
python3 query.py buy SH600519 1700 100

# 查询持仓
python3 query.py positions
```

输出：
```
📊 当前持仓:
--------------------------------------------------------------------------------
代码                   数量          成本价            成本市值
--------------------------------------------------------------------------------
SH600519            100      1700.00       170000.00
--------------------------------------------------------------------------------
```

### 卖出后查询盈亏

```bash
# 执行卖出
python3 query.py sell SH600519 1720 50

# 查询盈亏
python3 query.py profit
```

输出：
```
============================================================
💰 盈亏汇总
============================================================
  总盈亏：+1000.00
  总交易次数：2

  按标的汇总:
  代码                        盈亏       交易次数
  ----------------------------------------
  SH600519     ++++++++++1000.00          2
============================================================
```

---

## 💾 数据备份

```bash
# 备份数据
cp -r data/ data_backup_20260304/

# 恢复数据
cp -r data_backup_20260304/ data/
```

---

## 🔄 程序重启后

**数据会自动恢复！**

```python
# 第一次运行 - 创建账户
engine = PaperTradingEngine(data_dir="./data", initial_capital=1000000)
# ✓ 已创建新账户

# 执行交易
engine.buy("SH600519", 1700, 100)
engine.match_order("ORD000001", 1700.50)

# 程序退出...

# 第二次运行 - 自动加载已有数据
engine = PaperTradingEngine(data_dir="./data")
# ✓ 已加载账户数据，可用资金：807,153.62

# 持仓和交易记录都还在！
engine.show_positions()
```

---

## ⚠️ 注意事项

1. **数据目录**: 默认在 `./data/`，可通过 `data_dir` 参数指定
2. **数据安全**: 定期备份 `account.json` 和 `trades.db`
3. **并发访问**: 避免多个进程同时写入同一数据库
4. **数据迁移**: 直接复制 `data/` 目录即可迁移账户

---

## 📈 统计数据

- **订单表**: 支持无限订单记录
- **成交表**: 支持无限成交记录
- **查询性能**: 索引优化，支持快速查询
- **数据格式**: JSON + SQLite，易于导出和分析

---

## 🎯 总结

| 功能 | 支持 | 说明 |
|------|------|------|
| 买入记录保存 | ✅ | 永久保存 |
| 卖出记录保存 | ✅ | 永久保存 |
| 持仓查询 | ✅ | 实时查询 |
| 历史订单 | ✅ | 全部记录 |
| 历史成交 | ✅ | 全部记录 |
| 盈亏统计 | ✅ | 按标的汇总 |
| 程序重启 | ✅ | 自动恢复 |
| 数据导出 | ✅ | SQLite + JSON |

**是的，你的每一笔交易都会永久保存，随时可以查询！** 🎉
