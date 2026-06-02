# 新闻采集系统使用说明

## 架构改造

### 原架构
- 每次查询新闻时实时调用 AKShare API
- 无去重机制
- 无板块标记

### 新架构
- **定时采集**：每 10 分钟自动采集（09/19/29/39/49/59 分）
- **自动去重**：基于标题 + 发布时间哈希去重
- **板块标记**：自动识别 18 个行业板块
- **情绪分析**：正面/负面/中性标记
- **本地读取**：查询时从本地数据库读取最近 1-3 天新闻

---

## 文件结构

```
skills/akshare-news/
├── news_collector.py      # 新闻采集器（新增）
├── akshare_engine.py      # 查询引擎（已修改为本地读取）
├── akshare_engine_enhanced.py  # 增强版引擎（保留）
├── query.py               # 命令行工具
├── data/
│   └── news.db            # SQLite 数据库
└── SKILL.md               # 技能文档
```

---

## 定时任务

**Cron 表达式**: `9,19,29,39,49,59 * * * *` (Asia/Shanghai)

**任务内容**:
- 采集财经新闻（20 条）
- 采集行业新闻（18 个行业，约 160 条）
- 自动去重并标记板块/情绪
- 推送采集结果到 QQ

---

## 使用方法

### 1. 手动采集新闻

```bash
cd /root/.openclaw/workspace-marcus/skills/akshare-news
python3 news_collector.py
```

### 2. 查询最近 N 天新闻（Python API）

```python
from akshare_engine import AKShareEngine

engine = AKShareEngine()

# 读取最近 3 天新闻（默认）
news = engine.get_finance_news(limit=50, days=3, from_local=True)

# 读取最近 1 天新闻
news = engine.get_finance_news(limit=30, days=1, from_local=True)

# 按板块读取
news = engine.get_cached_news_by_days(days=3, limit=20, category='半导体')

# 按情绪读取
positive_news = engine.get_news_by_sentiment('positive', days=1, limit=20)
negative_news = engine.get_news_by_sentiment('negative', days=1, limit=20)

# 获取板块统计
stats = engine.get_category_stats(days=1)
```

### 3. 命令行查询

```bash
# 查询最近 3 天财经新闻
python3 query.py finance --limit 30

# 查询个股新闻（从本地读取）
python3 query.py stock 茅台 --limit 20

# 导出数据
python3 query.py export finance --limit 100 --format json
```

---

## 数据库结构

```sql
CREATE TABLE news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT,
    source TEXT,
    publish_time TEXT,
    url TEXT,
    category TEXT,           -- 板块：半导体/新能源/医药生物等
    sentiment TEXT,          -- 情绪：positive/negative/neutral
    keyword TEXT,            -- 关键词
    hash TEXT UNIQUE,        -- 去重哈希
    created_at TEXT NOT NULL
);
```

---

## 板块分类（18 个行业）

| 板块 | 关键词示例 |
|------|-----------|
| 半导体 | 芯片、集成电路、晶圆、光刻 |
| 人工智能 | AI、大模型、算力、深度学习 |
| 新能源 | 光伏、风电、锂电、储能 |
| 消费电子 | 手机、苹果、华为、智能穿戴 |
| 医药生物 | 医药、疫苗、创新药、CXO |
| 金融科技 | 数字货币、区块链、券商、保险 |
| 汽车 | 新能源车、自动驾驶、特斯拉 |
| 房地产 | 地产、楼市、物业、REITs |
| 通信 | 5G、基站、光纤、卫星 |
| 化工 | 塑料、化肥、有机硅、石化 |
| 有色金属 | 锂、钴、稀土、黄金 |
| 食品饮料 | 白酒、啤酒、乳制品、调味品 |
| 电力 | 火电、水电、核电、绿电 |
| 机械设备 | 机器人、自动化、工业母机 |
| 软件服务 | 云计算、SaaS、信创、鸿蒙 |
| 军工 | 航天、航空、船舶、兵器 |
| 传媒 | 游戏、影视、广告、出版 |
| 纺织 | 服装、家纺、棉花 |

---

## 情绪分析

**正面关键词**: 增长、利好、突破、超预期、业绩、中标、合作、创新、上涨、盈利...

**负面关键词**: 下跌、亏损、风险、违规、处罚、下滑、诉讼、调查、暴跌、衰退...

**情绪分数计算**: `score = 50 + (positive - negative) / total * 50`
- score > 60: 🟢 正面
- score < 40: 🔴 负面
- 40-60: 🟡 中性

---

## 日志位置

- **采集日志**: `/root/.openclaw/workspace-marcus/memory/news-collection-logs/`
- **数据库**: `/root/.openclaw/workspace-marcus/skills/akshare-news/data/news.db`

---

## 注意事项

1. **首次运行**: 会自动创建数据库和表结构
2. **去重机制**: 基于标题 + 发布时间哈希，避免重复
3. **数据保留**: 建议定期清理 30 天前的旧数据
4. **网络要求**: 采集时需要访问国内财经网站
5. **采集频率**: 每 10 分钟一次，避免过于频繁

---

## 清理旧数据（可选）

```python
import sqlite3
from datetime import datetime, timedelta

db_file = '/root/.openclaw/workspace-marcus/skills/akshare-news/data/news.db'
conn = sqlite3.connect(db_file)
cursor = conn.cursor()

# 删除 30 天前的数据
cutoff = (datetime.now() - timedelta(days=30)).isoformat()
cursor.execute('DELETE FROM news WHERE publish_time < ?', (cutoff,))
conn.commit()
conn.close()

print(f"已删除 30 天前的新闻")
```

---

_保持饥饿，保持锋利。📈_
