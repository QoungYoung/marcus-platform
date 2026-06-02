#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取东方财富概念板块列表及成分股

功能：
1. 获取东方财富所有概念板块
2. 获取热门概念的成分股
3. 保存为本地 JSON 文件
4. 供新闻分析直接使用

使用方法：
    python3 fetch_em_concepts.py
"""

import akshare as ak
import json
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings('ignore')

# 输出文件
OUTPUT_DIR = Path(__file__).parent / "data"
CONCEPTS_FILE = OUTPUT_DIR / "em_concepts.json"
CONCEPT_STOCKS_FILE = OUTPUT_DIR / "em_concept_stocks.json"

# 热门概念（优先获取成分股）
HOT_CONCEPTS = [
    '人工智能', 'AI 芯片', '半导体', '芯片', '集成电路',
    '新能源汽车', '锂电池', '固态电池', '光伏', '储能',
    '机器人', '人形机器人', '低空经济', '飞行汽车',
    '创新药', '医药', '医疗器械', '减肥药',
    '5G', '6G', 'CPO', '光模块', '卫星互联网',
    '大数据', '云计算', '信创', '鸿蒙', '华为概念',
    '消费电子', 'AI 手机', 'AI PC', '元宇宙',
    '券商', '互联网金融', '白酒', '食品饮料',
    '军工', '商业航天', '碳中和', '虚拟电厂',
]


def get_all_concepts():
    """获取东方财富所有概念板块"""
    print("[东方财富] 获取概念板块列表...")
    
    try:
        df = ak.stock_board_concept_name_em()
        
        concepts = []
        for _, row in df.iterrows():
            concepts.append({
                'name': row.get('板块名称', ''),
                'code': row.get('板块代码', ''),
                'price': float(row.get('最新价', 0)),
                'change_pct': float(row.get('涨跌幅', 0)),
                'market_cap': float(row.get('总市值', 0)),
                'turnover': float(row.get('换手率', 0)),
                'up_count': int(row.get('上涨家数', 0)),
                'down_count': int(row.get('下跌家数', 0)),
                'leader': row.get('领涨股票', ''),
            })
        
        print(f"[东方财富] 获取到 {len(concepts)} 个概念板块")
        return concepts
    
    except Exception as e:
        print(f"[东方财富] ✗ 失败：{e}")
        return []


def get_concept_stocks(concept_name: str):
    """
    获取概念板块成分股
    
    Args:
        concept_name: 概念板块名称
    
    Returns:
        [(code, name), ...]
    """
    try:
        df = ak.stock_board_concept_cons_em(symbol=concept_name)
        
        if df is None or len(df) == 0:
            return []
        
        stocks = []
        for _, row in df.iterrows():
            code = row.get('代码', '')
            name = row.get('名称', '')
            if code and name:
                stocks.append((code, name))
        
        return stocks
    
    except Exception as e:
        return []


def fetch_hot_concept_stocks(concepts: list, max_concepts: int = 30):
    """
    获取热门概念的成分股
    
    Args:
        concepts: 概念名称列表
        max_concepts: 最多获取多少个概念
    
    Returns:
        {concept_name: [(code, name), ...], ...}
    """
    print(f"[东方财富] 获取 {len(concepts[:max_concepts])} 个热门概念的成分股...")
    
    concept_stocks = {}
    
    for i, concept in enumerate(concepts[:max_concepts], 1):
        print(f"[{i}/{len(concepts[:max_concepts])}] {concept}...", end=' ')
        
        stocks = get_concept_stocks(concept)
        
        if stocks:
            concept_stocks[concept] = stocks
            print(f"{len(stocks)}只")
        else:
            print("无数据")
    
    return concept_stocks


def save_to_file(data: dict, filepath: Path):
    """保存数据到 JSON 文件"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"[保存] ✓ {filepath}")


def load_concepts():
    """加载本地概念数据"""
    if not CONCEPTS_FILE.exists():
        return None
    
    with open(CONCEPTS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_concept_stocks():
    """加载本地概念成分股数据"""
    if not CONCEPT_STOCKS_FILE.exists():
        return None
    
    with open(CONCEPT_STOCKS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    """主函数"""
    print("=" * 60)
    print("东方财富概念板块数据获取")
    print("=" * 60)
    
    # 1. 获取所有概念板块
    concepts = get_all_concepts()
    if not concepts:
        print("[错误] 获取概念板块失败")
        return
    
    # 2. 保存概念列表
    concept_data = {
        'updated_at': datetime.now().isoformat(),
        'total': len(concepts),
        'concepts': concepts
    }
    save_to_file(concept_data, CONCEPTS_FILE)
    
    # 3. 获取热门概念成分股
    concept_stocks = fetch_hot_concept_stocks(HOT_CONCEPTS, max_concepts=30)
    
    # 4. 保存成分股数据
    stocks_data = {
        'updated_at': datetime.now().isoformat(),
        'concepts': {k: [{'code': c, 'name': n} for c, n in v] 
                     for k, v in concept_stocks.items()}
    }
    save_to_file(stocks_data, CONCEPT_STOCKS_FILE)
    
    # 5. 统计
    print("\n=== 统计 ===")
    print(f"概念板块总数：{len(concepts)}")
    print(f"已获取成分股的概念：{len(concept_stocks)}")
    print(f"总成分股记录：{sum(len(v) for v in concept_stocks.values())}")
    
    # 6. 显示热门概念 Top10
    print("\n=== 热门概念成分股 Top10 ===")
    sorted_concepts = sorted(concept_stocks.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    for concept, stocks in sorted_concepts:
        print(f"  {concept}: {len(stocks)}只")


if __name__ == '__main__':
    main()
