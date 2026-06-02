# 东方财富概念板块集成

## 数据获取

```bash
# 手动获取最新概念数据
cd /root/.openclaw/workspace-marcus/skills/akshare-news
python3 fetch_em_concepts.py
```

**定时任务**（每周更新）：
```cron
0 9 * * 1 cd /root/.openclaw/workspace-marcus/skills/akshare-news && python3 fetch_em_concepts.py
```

## 数据文件

| 文件 | 说明 |
|------|------|
| `data/em_concepts.json` | 468 个概念板块列表 |
| `data/em_concept_stocks.json` | 热门概念成分股（11 个概念，3240 只股票） |

## 使用方式

### 1. 概念映射器
```python
from em_concept_mapper import ConceptMapper

mapper = ConceptMapper()

# 从新闻匹配概念
concepts = mapper.match_concepts('华为发布 AI 芯片')
# → ['人工智能', '华为概念']

# 获取概念成分股
stocks = mapper.get_concept_stocks('人工智能')
# → [{'code': '000001', 'name': '平安银行'}, ...]

# 获取股票所属概念
stock_concepts = mapper.get_stock_concepts('002415')
# → ['人工智能', '大数据', '云计算']
```

### 2. 选股模块自动使用
```python
from stock_selector import get_hot_sectors, get_stock_pool

# 分析新闻热点概念
hot_sectors = get_hot_sectors(news_list)
# → ['人工智能', '华为概念', '固态电池']

# 从概念直接选股
stocks = get_stock_pool(sector='人工智能', limit=20)
# 优先从东方财富概念获取，fallback 到细分行业
```

## 选股流程

```
新闻采集
    ↓
AI 分析/关键词匹配
    ↓
东方财富概念匹配（优先）→ 概念成分股
    ↓
细分行业匹配（fallback）→ 股票池 industry 字段
    ↓
综合评分 → 交易候选
```

## 热门概念 Top10

| 概念 | 成分股数量 |
|------|-----------|
| 华为概念 | 753 只 |
| 人工智能 | 699 只 |
| 大数据 | 296 只 |
| 低空经济 | 288 只 |
| 信创 | 245 只 |
| 人形机器人 | 234 只 |
| 云计算 | 231 只 |
| 固态电池 | 211 只 |
| 创新药 | 165 只 |
| 卫星互联网 | 83 只 |

## 优势

1. **直接对接** - 概念→个股，无需行业转换
2. **实时性** - 东方财富概念与市场热点同步
3. **覆盖广** - 468 个概念板块，3240 只成分股
4. **自动更新** - 每周定时同步最新数据

## 注意事项

- 成分股数据来自东方财富 API，部分概念可能返回空
- 建议每周更新一次概念成分股数据
- 概念成分股无市值数据，选股时会补充
