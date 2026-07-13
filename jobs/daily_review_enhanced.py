#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 每日复盘报告（增强版）
- 包含新闻影响力分级（S/A/B/C）
- 包含单条新闻详细分析
- 包含新闻 - 持仓关联度分析
- 包含综合评分计算
"""

import sys
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
from workspace_detector import WORKSPACE, get_vnpy_dir, get_xueqiu_dir, get_akshare_dir, get_core_dir

VNPY_DIR = get_vnpy_dir()
XUEQIU_DIR = get_xueqiu_dir()
AKSHARE_DIR = get_akshare_dir()
CORE_DIR = get_core_dir()

sys.path.insert(0, str(VNPY_DIR))
sys.path.insert(0, str(XUEQIU_DIR))
sys.path.insert(0, str(AKSHARE_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CORE_DIR / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "paper-trading"))

from paper_engine import PaperTradingEngine
from xueqiu_engine import XueqiuEngine
from news_analyzer import get_news_analysis


def get_market_data():
    """获取市场指数数据"""
    indices = {}
    index_map = {
        '上证指数': 'SH000001',
        '深证成指': 'SZ399001',
        '沪深 300': 'SH000300',
        '创业板指': 'SZ399006'
    }
    
    try:
        engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
        for name, code in index_map.items():
            quote = engine.get_stock_quote(code, use_cache=False)
            if quote:
                indices[name] = {
                    'close': float(quote.get('current', 0)),
                    'change': float(quote.get('percent', 0)),
                    'change_amt': float(quote.get('chg', 0)),
                    'volume': float(quote.get('volume', 0))
                }
    except Exception as e:
        print(f"[错误] 获取指数数据失败：{e}", file=sys.stderr)
    
    return indices


def get_news_analysis_enhanced():
    """获取新闻情绪和影响力分析（使用统一接口）"""
    try:
        # 调用统一新闻分析接口
        result = get_news_analysis(news_limit=30, use_ai=True)
        return result
    except Exception as e:
        print(f"[错误] 获取新闻分析失败：{e}", file=sys.stderr)
        return {
            'sentiment': {'score': 50, 'positive': 0, 'negative': 0, 'neutral': 0},
            'impact_analysis': [],
            'summary': {'s_level_count': 0, 'a_level_count': 0, 'b_level_count': 0, 'c_level_count': 0, 'top_sectors': []}
        }


def get_account_data():
    """获取账户数据（从 data/trades.db，与其他脚本统一）"""
    try:
        from paper_engine import PaperTradingEngine
        paper = PaperTradingEngine(data_dir=str(WORKSPACE / "data"))
        account = paper.get_account_info()
        positions = []
        for symbol, pos in paper.positions.items():
            positions.append({
                'symbol': symbol,
                'volume': pos.volume,
                'avg_price': pos.avg_price,
                'entry_date': pos.entry_date,
                'highest_price': pos.highest_price
            })
        return account, positions
    except Exception as e:
        print(f"[错误] 获取账户数据失败：{e}", file=sys.stderr)
        return None, []


def get_today_trades():
    """获取今日交易记录，计算每只股票的今日买入平均成本"""
    import sqlite3
    from datetime import datetime, date
    
    today = date.today().isoformat()
    db_path = WORKSPACE / "data" / "trades.db"
    
    if not db_path.exists():
        return {}
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 查询今日买入记录
    cursor.execute('''
        SELECT symbol, price, volume, created_at 
        FROM trades 
        WHERE direction = '买入' 
        AND date(created_at) = ?
        ORDER BY created_at DESC
    ''', (today,))
    
    today_buys = {}
    for row in cursor.fetchall():
        symbol = row['symbol']
        price = row['price']
        volume = row['volume']
        
        if symbol not in today_buys:
            today_buys[symbol] = {'total_cost': 0, 'total_volume': 0, 'trades': []}
        
        today_buys[symbol]['total_cost'] += price * volume
        today_buys[symbol]['total_volume'] += volume
        today_buys[symbol]['trades'].append({
            'price': price,
            'volume': volume,
            'time': row['created_at']
        })
    
    conn.close()
    
    # 计算今日买入平均成本
    today_avg_cost = {}
    for symbol, data in today_buys.items():
        if data['total_volume'] > 0:
            today_avg_cost[symbol] = data['total_cost'] / data['total_volume']
    
    return today_avg_cost


def calculate_composite_score(indices, sentiment_score, s_a_count):
    """计算综合评分（右侧交易：量价驱动，不加虚构权重）"""
    # 指数表现 (40%) - 趋势强度
    changes = [i['change'] for i in indices.values() if i['change'] != 0]
    avg_change = sum(changes) / len(changes) if changes else 0
    index_score = min(100, max(0, 50 + avg_change * 20))
    
    # 新闻情绪 (30%) - 市场温度
    sentiment_component = sentiment_score
    
    # S/A 级新闻 (30%) - 催化强度
    sa_score = min(100, s_a_count * 12)
    
    # 综合评分（无虚构维度）
    composite = (
        index_score * 0.40 +
        sentiment_component * 0.30 +
        sa_score * 0.30
    )
    
    return {
        'composite': round(composite, 1),
        'index_score': round(index_score, 1),
        'sentiment_score': round(sentiment_score, 1),
        'sa_score': round(sa_score, 1),
    }


def determine_stance(composite_score):
    """根据综合评分判断市场立场"""
    if composite_score >= 70:
        return "🟢 激进买入", "综合评分≥70，市场情绪高涨"
    elif composite_score >= 55:
        return "🟡 保守买入", "综合评分 55-70，市场震荡偏多"
    elif composite_score >= 40:
        return "🟡 持币观望", "综合评分 40-55，市场方向不明"
    else:
        return "🔴 空仓避险", "综合评分<40，市场情绪低迷"


def generate_review_report():
    """生成每日复盘报告（增强版）"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # 获取数据
    indices = get_market_data()
    # 15:00 多个脚本并发调用 DeepSeek，冷却 10s 避免限流
    import time
    print("[复盘] 冷却 10s 避免 DeepSeek 限流...", file=sys.stderr)
    time.sleep(10)
    news_analysis = get_news_analysis_enhanced()
    account, positions = get_account_data()
    
    # 提取分析结果
    sentiment = news_analysis.get('sentiment', {})
    impact = news_analysis.get('impact_analysis', [])
    impact_summary = news_analysis.get('summary', {})
    
    sentiment_score = sentiment.get('score', 50)
    s_count = impact_summary.get('s_level_count', 0)
    a_count = impact_summary.get('a_level_count', 0)
    
    # 计算综合评分
    scores = calculate_composite_score(indices, sentiment_score, s_count + a_count)
    stance, stance_reason = determine_stance(scores['composite'])
    
    # 生成报告
    report = f"""# 📈 Marcus 每日复盘报告 (增强版)

**日期**: {datetime.now().strftime('%Y-%m-%d')} ({['周一','周二','周三','周四','周五'][datetime.now().weekday()]})  
**生成时间**: {timestamp}  
**市场状态**: 已收盘

---

## 🎯 市场立场：{stance}

**理由**: {stance_reason}

### 综合评分：{scores['composite']}/100

| 维度 | 分数 | 权重 | 加权分 |
|------|------|------|--------|
| 指数表现 | {scores['index_score']} | 40% | {scores['index_score']*0.4:.1f} |
| 新闻情绪 | {scores['sentiment_score']} | 30% | {scores['sentiment_score']*0.3:.1f} |
| S/A 级新闻 | {scores['sa_score']} | 30% | {scores['sa_score']*0.3:.1f} |

---

## 📊 今日市场表现

| 指数 | 收盘 | 涨跌% | 涨跌额 | 成交量 |
|------|------|-------|--------|--------|
"""
    
    for name, data in indices.items():
        vol_str = f"{data['volume']/1e8:.2f}亿" if data['volume'] > 0 else "-"
        report += f"| {name} | {data['close']:.2f} | {data['change']:+.2f}% | {data['change_amt']:+.2f} | {vol_str} |\n"
    
    # 新闻情绪分析
    report += f"""
---

## 📰 新闻情绪分析

| 指标 | 数值 |
|------|------|
| 情绪分数 | {sentiment_score:.1f}/100 {'(正面)' if sentiment_score > 60 else '(负面)' if sentiment_score < 40 else '(中性)'} |
| 正面新闻 | {sentiment.get('positive', 0)} 条 |
| 负面新闻 | {sentiment.get('negative', 0)} 条 |
| 中性新闻 | {sentiment.get('neutral', 0)} 条 |

---

## 💥 新闻影响力分级 (S/A/B/C)

| 等级 | 数量 | 说明 |
|------|------|------|
| 🚨 S 级 (板块驱动) | {s_count} | 国家政策 + 资金/价格大幅变动 → 板块涨停潮 |
| 🔥 A 级 (强势催化) | {a_count} | 行业重大事件 → 板块 +5% 以上 |
| 📊 B 级 (个股利好) | {impact_summary.get('b_level_count', 0)} | 公司层面利好 → 个股 +3% 以上 |
| ⚠️ C 级 (噪音) | {impact_summary.get('c_level_count', 0)} | 传闻/建议 → 影响有限 |

"""
    
    # S/A 级新闻详情
    s_a_news = [n for n in impact if n.get('impact_level') in ['S', 'A']]
    if s_a_news:
        report += "**S/A 级新闻详情**:\n\n"
        for n in s_a_news[:8]:
            report += f"- [{n['impact_level']}] **{n['title']}**\n"
            report += f"  → 影响：{', '.join(n.get('affected_sectors', ['综合']))} | 可信度：{n.get('credibility_score', 'N/A')} | 预期：{n.get('expected_impact', 'N/A')[:30]}\n"
        report += "\n"
    
    # 重点板块
    top_sectors = impact_summary.get('top_sectors', [])
    if top_sectors:
        report += "**重点板块**:\n\n"
        for sector in top_sectors[:5]:
            report += f"- **{sector['sector']}**: 影响分 {sector['impact_score']}, 新闻 {sector['news_count']} 条\n"
        report += "\n"
    
    # 账户表现
    if account:
        # get_account_info() 返回中文键和格式化字符串值
        initial_capital = float(account['初始资金'].replace(',', ''))
        total_asset = float(account['总资产'].replace(',', ''))
        available_cash = float(account['可用资金'].replace(',', ''))
        # 持仓市值格式可能是 "xxx,xxx.xx (成本)" 或 "xxx,xxx.xx (市价)"
        position_value_str = account['持仓市值'].split()[0].replace(',', '')
        position_value = float(position_value_str)
        position_ratio = position_value / initial_capital * 100
        total_pnl = total_asset - initial_capital
        total_pnl_pct = (total_asset / initial_capital - 1) * 100

        report += f"""---

## 💰 账户表现

| 项目 | 数值 |
|------|------|
| 初始资金 | ¥{initial_capital:,.2f} |
| 总资产 | ¥{total_asset:,.2f} |
| 可用资金 | ¥{available_cash:,.2f} |
| 持仓市值 | ¥{position_value:,.2f} |
| 仓位 | {position_ratio:.1f}% |
| 总盈亏 | ¥{total_pnl:,.2f} ({total_pnl_pct:.2f}%) |

"""
    
    # 持仓明细
    if positions:
        # 获取今日交易记录
        today_avg_cost = get_today_trades()
        
        report += """---

## 📊 持仓股票表现

| 代码 | 名称 | 个股涨跌 | 今日涨跌% | 今日盈亏 | 累计涨跌% | 累计盈亏 |
|------|------|----------|-----------|----------|-----------|----------|
"""
        engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
        for pos in positions:
            symbol = pos['symbol']
            try:
                quote = engine.get_stock_quote(symbol, use_cache=False)
                current = float(quote.get('current', 0)) if quote else pos['avg_price']
                change_pct = float(quote.get('percent', 0)) if quote else 0  # 个股今日涨跌%
                last_close = float(quote.get('last_close', 0)) if quote else 0
                name = quote.get('name', symbol) if quote else symbol
            except:
                current = pos['avg_price']
                change_pct = 0
                name = symbol
            
            cost = pos['avg_price']
            volume = pos['volume']
            
            # 累计盈亏 = (现价 - 持仓成本) × 数量
            total_profit = (current - cost) * volume
            total_profit_pct = (current - cost) / cost * 100 if cost > 0 else 0
            
            # 今日盈亏计算逻辑：
            # 1. 如果今日有买入：使用今日买入平均成本 vs 现价
            # 2. 如果今日无买入：使用昨日收盘价 vs 现价（简化：用个股今日涨跌%推算）
            if symbol in today_avg_cost:
                # 今日有买入：计算今日持仓盈亏
                today_cost = today_avg_cost[symbol]
                today_profit = (current - today_cost) * volume
                today_profit_pct = (current - today_cost) / today_cost * 100 if today_cost > 0 else 0
            else:
                # 今日无买入：用昨收价计算持仓盈亏
                if last_close > 0:
                    yesterday_close = last_close
                else:
                    yesterday_close = current / (1 + change_pct / 100) if change_pct != 0 else current
                today_profit = (current - yesterday_close) * volume
                today_profit_pct = change_pct
            
            # 状态图标（基于累计盈亏）
            status = "🟢" if total_profit > 0 else "🔴" if total_profit < 0 else "🟡"
            
            report += f"| {symbol} | {name} | {change_pct:+.2f}% | {today_profit_pct:+.2f}% | ¥{today_profit:+,.0f} | {total_profit_pct:+.2f}% | ¥{total_profit:,.0f} {status} |\n"
    
    # 明日策略
    report += f"""
---

## 🎯 明日策略

**市场立场**: {stance}

**操作建议**:
"""
    
    if "激进买入" in stance:
        report += "- ✅ 仓位 ≤60%（Marcus 铁律），积极参与强势概念\n"
        report += "- ✅ 关注 S/A 级新闻驱动的概念板块\n"
        report += "- ✅ 止损 -8%，止盈分批 +10%/+15%/+20%\n"
    elif "保守买入" in stance:
        report += "- 🟡 仓位 ≤40%，精选右侧信号个股\n"
        report += "- 🟡 关注量价确认 + 趋势初期的概念\n"
        report += "- 🟡 快进快出，严格止损\n"
    else:
        report += "- 🔴 空仓或轻仓观望\n"
        report += "- 🔴 等待趋势确认后再入场\n"
        report += "- 🔴 保留现金，耐心等待右侧信号\n"
    
    # 关注方向
    if top_sectors:
        report += "\n**关注方向**:\n"
        for sector in top_sectors[:3]:
            report += f"- {sector['sector']} (影响分 {sector['impact_score']})\n"
    
    report += f"""
---

## 📝 复盘总结

**今日评分**: {scores['composite']:.0f}/100

**数据回顾**:
- 指数表现: {scores['index_score']:.0f}/100（{'趋势向上' if scores['index_score'] > 60 else '震荡' if scores['index_score'] > 40 else '趋势向下'}）
- 新闻情绪: {scores['sentiment_score']:.0f}/100（{'偏暖' if scores['sentiment_score'] > 60 else '中性' if scores['sentiment_score'] > 40 else '偏冷'}）
- 催化强度: {scores['sa_score']:.0f}/100（S/A级新闻 {s_count + a_count} 条）

**改进方向**:
- ⚠️ 关注明日开盘价与今日收盘价的缺口，判断趋势延续性
- ⚠️ 检查持仓是否出现缩量上涨信号
- ⚠️ 连续亏损 ≥3 笔则暂停交易

---

_冷静理性，数据驱动。不以物喜，不以己悲。_
"""
    
    return report


if __name__ == '__main__':
    report = generate_review_report()
    print(report)
    
    # 保存到 memory 目录
    date_str = datetime.now().strftime('%Y-%m-%d')
    output_path = WORKSPACE / "memory" / f"{date_str}-daily-review-enhanced.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n[复盘报告] 已保存至：{output_path}", file=sys.stderr)
