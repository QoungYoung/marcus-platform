---
name: akshare-news
description: AKShare 财经新闻资讯查询，支持个股新闻、实时财经新闻、多新闻源聚合
read_when:
  - 查询个股相关新闻
  - 获取实时财经新闻
  - 聚合多新闻源资讯
  - 导出新闻数据
metadata: {"clawdbot":{"emoji":"📰","requires":{"bins":["python3"]}}}
allowed-tools: Bash(akshare:*)
---

# AKShare 新闻资讯查询 Skill

> 基于 AKShare 的免费财经新闻资讯查询工具，支持同花顺、东方财富等新闻源

## 功能

- ✅ 个股相关新闻查询
- ✅ 财经新闻实时获取
- ✅ 多新闻源支持（同花顺、东方财富等）
- ✅ 数据持久化存储
- ✅ 批量数据导出
- ✅ 无需 API Token

## 安装

```bash
cd /root/.openclaw/workspace/skills/akshare-news
pip install -r requirements.txt
```

## 快速开始

### 1. 查询个股新闻

```bash
# 查询贵州茅台相关新闻
python3 query.py stock 茅台

# 查询多只股票
python3 query.py stock 茅台 五粮液 宁德时代

# 指定返回数量
python3 query.py stock 茅台 --limit 20
```

### 2. 查询财经新闻

```bash
# 查询最新财经新闻
python3 query.py finance

# 查询指定数量
python3 query.py finance --limit 50
```

### 3. 导出数据

```bash
# 导出股票新闻到 JSON
python3 query.py export stock 茅台 --limit 100

# 导出到 CSV
python3 query.py export stock 茅台 --format csv
```

## 命令参考

| 命令 | 说明 | 示例 |
|------|------|------|
| `stock` | 查询个股新闻 | `query.py stock 茅台` |
| `finance` | 查询财经新闻 | `query.py finance` |
| `export` | 导出数据 | `query.py export stock 茅台` |
| `hot` | 查询热门新闻 | `query.py hot` |

## 新闻源

| 新闻源 | 代码 | 说明 |
|--------|------|------|
| 东方财富 | em | 东方财富网财经新闻 |
| 同花顺 | ths | 同花顺财经新闻 |
| 新浪财经 | sina | 新浪财经 |
| 腾讯财经 | qq | 腾讯财经 |

## 输出示例

```
================================================================================
📰 个股新闻：茅台
================================================================================

[2026-03-04 15:30] 贵州茅台发布 2025 年业绩预告
  来源：东方财富网
  链接：http://finance.eastmoney.com/a/...

[2026-03-04 14:45] 茅台集团召开年度工作会议
  来源：同花顺财经
  链接：http://news.10jqka.com.cn/...

================================================================================
```

## Python API

```python
from akshare_engine import AKShareEngine

# 创建引擎
engine = AKShareEngine()

# 查询个股新闻
news = engine.get_stock_news('茅台', limit=20)
for n in news:
    print(f"{n['发布时间']}: {n['标题']}")

# 查询财经新闻
finance_news = engine.get_finance_news(limit=50)

# 导出数据
engine.export_to_json(news, "maotai_news.json")
```

## 数据持久化

- 查询的新闻自动保存到 `data/news.db` (SQLite)
- 导出的数据保存在 `data/` 目录
- 支持缓存查询（减少重复请求）

## 优势

| 特性 | AKShare | Tushare | 雪球 |
|------|---------|---------|------|
| 免费 | ✅ | ❌ (积分制) | ⚠️ (需登录) |
| 无需 Token | ✅ | ❌ | ❌ |
| 新闻源丰富 | ✅ | ✅ | ❌ |
| 数据全面 | ✅ | ✅ | ⚠️ |
| 更新频率 | 实时 | 实时 | 实时 |

## 注意事项

1. **网络要求**: 需要访问国内财经网站
2. **数据准确性**: 以官方公告为准
3. **访问频率**: 建议适当控制请求频率

## 相关资源

- [AKShare 官网](https://akshare.akfamily.xyz/)
- [AKShare 文档](https://akshare.akfamily.xyz/data.html)
- [GitHub](https://github.com/akfamily/akshare)

## 更新日志

- **2026-03-06**: 重构为定时采集系统
  - 新增 `news_collector.py` 定时采集器
  - 每 10 分钟自动采集（09/19/29/39/49/59 分）
  - 自动去重 + 板块标记 + 情绪分析
  - 查询改为从本地读取最近 1-3 天新闻
  - 支持 18 个行业板块分类

- 2026-03-04: 初始版本，支持个股新闻和财经新闻查询

## 定时任务

**Cron**: `9,19,29,39,49,59 * * * *` (Asia/Shanghai)

每次采集自动推送结果到 QQ，包含：
- 新增新闻条数
- 活跃板块 Top10
- 情绪分布统计
