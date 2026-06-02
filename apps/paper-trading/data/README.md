# 数据目录

此目录用于存储历史行情数据，供回测使用。

## 支持的数据格式

### CSV 格式

```csv
datetime,open,high,low,close,volume
2024-01-02 09:30:00,1700.00,1705.00,1698.00,1702.00,100000
2024-01-02 09:31:00,1702.00,1708.00,1701.00,1706.00,120000
...
```

### 数据来源

#### 1. AKShare（免费）

```python
import akshare as ak
import pandas as pd

# 获取 A 股历史数据
df = ak.stock_zh_a_hist(
    symbol="600519",
    period="daily",
    start_date="20240101",
    end_date="20241231",
    adjust="qfq"
)

# 保存为 CSV
df.to_csv("SH600519.csv", index=False)
```

#### 2. Tushare（需要 token）

```python
import tushare as ts

pro = ts.pro_api('your_token')
df = pro.daily(ts_code='600519.SH', start_date='20240101', end_date='20241231')
df.to_csv("SH600519.csv", index=False)
```

#### 3. 从 VN.PY 导出数据

在 VN.PY 图形界面中：
1. 打开"数据管理器"
2. 选择要导出的品种
3. 点击"导出数据"

## 数据目录结构

```
data/
├── stock/          # 股票数据
│   ├── SH600519.csv
│   └── SZ000858.csv
├── future/         # 期货数据
│   └── IF2406.csv
└── fund/           # 基金数据
    └── 510300.csv
```

## 数据要求

| 字段 | 类型 | 说明 |
|------|------|------|
| datetime | datetime | 时间戳 |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| volume | float | 成交量 |
| turnover | float | 成交额（可选） |
| open_interest | float | 持仓量（期货可选） |

## 注意事项

1. 时间格式必须能被 pandas 解析
2. 数据必须按时间升序排列
3. 不能有重复的时间戳
4. 价格不能为负数或零
