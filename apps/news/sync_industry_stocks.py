#!/usr/bin/env python3

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _api_config import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL, TUSHARE_TOKEN
# -*- coding: utf-8 -*-
"""
从 Tushare 同步行业成分股，自动生成行业关键词映射

功能：
1. 从 Tushare 获取行业分类（申万一级/二级/三级）
2. 获取每个行业的成分股列表
3. 从成分股名称中提取关键词（公司名简称）
4. 自动生成/更新 industry_keywords.py

使用方法：
    python3 sync_industry_stocks.py
"""

import sys
import os
from datetime import datetime
from pathlib import Path

# Use workspace_detector for cross-platform path resolution
sys.path.insert(0, str(Path(__file__).parent))
from workspace_detector import WORKSPACE, get_akshare_dir

AKSHARE_DIR = get_akshare_dir()

# Tushare 配置
TUSHARE_URL = "https://api.tushare.pro"

# 输出文件
OUTPUT_FILE = AKSHARE_DIR / "industry_keywords_auto.py"
STOCK_POOL_DB = WORKSPACE / "data" / "stock_pool.db"


def get_stock_basic_by_industry():
    """
    从 Tushare 获取股票基本信息，按 industry 字段分组
    
    Returns:
        {industry_name: [(ts_code, name), ...], ...}
    """
    import urllib.request
    import urllib.error
    import json
    import ssl
    
    try:
        # 获取全部 A 股基本信息
        payload = {
            'api_name': 'stock_basic',
            'token': TUSHARE_TOKEN,
            'params': {
                'exchange': '',
                'list_status': 'L',
                'fields': 'ts_code,symbol,name,area,industry,market'
            }
        }
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            TUSHARE_URL,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        context = ssl.create_default_context()
        with urllib.request.urlopen(req, context=context, timeout=30) as response:
            response_data = response.read().decode('utf-8')
        
        api_response = json.loads(response_data)
        
        if api_response.get('code') == 0:
            fields = api_response['data']['fields']
            items = api_response['data']['items']
            
            # 按 industry 分组
            industry_stocks = {}
            for item in items:
                row = dict(zip(fields, item))
                industry = row.get('industry', '')
                ts_code = row.get('ts_code', '')
                name = row.get('name', '')
                
                if industry and ts_code and name:
                    if industry not in industry_stocks:
                        industry_stocks[industry] = []
                    industry_stocks[industry].append((ts_code, name))
            
            print(f"[Tushare] 获取到 {len(items)} 只股票，{len(industry_stocks)} 个行业")
            return industry_stocks
        else:
            print(f"[Tushare] API 错误：{api_response.get('msg', '')}")
            return {}
    
    except Exception as e:
        print(f"[Tushare] 异常：{e}")
        return {}


def extract_keywords_from_names(stock_names: list) -> list:
    """
    从股票名称中提取关键词
    
    规则：
    1. 取前 2-4 个字作为公司名简称
    2. 去除常见后缀（股份、集团、科技等）
    3. 保留行业特征词
    
    Args:
        stock_names: 股票名称列表
    
    Returns:
        关键词列表
    """
    # 常见后缀（用于简化公司名）
    suffixes = [
        '股份有限公司', '股份有限公司', '集团有限公司', '集团',
        '股份有限公司', '股份公司', '有限公司', '有限责任公司',
        '科技', '技术', '发展', '实业', '国际', '中国',
    ]
    
    keywords = set()
    
    for name in stock_names[:50]:  # 每个行业取前 50 只股票
        # 去除 ST、*等标记
        clean_name = name.replace('*ST', '').replace('ST', '').replace('*', '')
        
        # 提取 2-4 字简称
        if len(clean_name) >= 4:
            # 尝试取前 2-4 字
            for length in [2, 3, 4]:
                keyword = clean_name[:length]
                # 避免太常见的词
                if keyword not in ['中国', '国际', '实业', '发展', '集团']:
                    keywords.add(keyword)
                    break
        elif len(clean_name) >= 2:
            keywords.add(clean_name[:2])
    
    return sorted(list(keywords))[:20]  # 每个行业最多 20 个关键词


def build_industry_keywords():
    """
    构建行业关键词映射
    
    Returns:
        {industry_name: [keywords], ...}
    """
    print("[同步] 开始从 Tushare 同步行业成分股...")
    
    # 1. 获取股票基本信息（按 industry 分组）
    industry_stocks = get_stock_basic_by_industry()
    if not industry_stocks:
        print("[同步] ✗ 获取股票数据失败")
        return {}
    
    # 2. 为每个行业提取关键词
    industry_keywords = {}
    
    for i, (industry_name, stocks) in enumerate(industry_stocks.items(), 1):
        print(f"[同步] {i}/{len(industry_stocks)} {industry_name}...", end=' ')
        
        stock_names = [name for _, name in stocks]
        keywords = extract_keywords_from_names(stock_names)
        
        # 添加行业名称本身作为关键词
        if industry_name not in keywords:
            keywords.insert(0, industry_name)
        
        industry_keywords[industry_name] = keywords
        print(f"{len(stocks)}只股票，{len(keywords)}个关键词")
    
    return industry_keywords


def generate_python_file(industry_keywords: dict):
    """
    生成 industry_keywords_auto.py 文件
    
    Args:
        industry_keywords: {industry_name: [keywords], ...}
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    total_keywords = sum(len(v) for v in industry_keywords.values())
    num_industries = len(industry_keywords)
    
    content = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行业关键词映射（自动生成）

来源：Tushare API 行业成分股
生成时间：''' + now + '''
成分股数量：''' + str(total_keywords) + ''' 个关键词
行业数量：''' + str(num_industries) + ''' 个

注意：此文件由 sync_industry_stocks.py 自动生成，手动修改会被覆盖。
如需自定义关键词，请编辑 industry_keywords_manual.py
"""

# 行业关键词映射（从 Tushare 成分股自动生成）
INDUSTRY_KEYWORDS_AUTO = {
'''
    
    # 生成映射字典
    for industry, keywords in sorted(industry_keywords.items()):
        keywords_str = ', '.join(f"'{kw}'" for kw in keywords[:15])  # 最多 15 个关键词
        content += f"    '{industry}': [{keywords_str}],\n"
    
    content += '''
}


def get_auto_keywords(industry: str) -> list:
    """获取自动生成的行业关键词"""
    return INDUSTRY_KEYWORDS_AUTO.get(industry, [])


def get_all_auto_industries() -> list:
    """获取所有自动同步的行业名称"""
    return list(INDUSTRY_KEYWORDS_AUTO.keys())


# 合并手动和自动关键词
if __name__ == '__main__':
    print(f"自动生成 {len(INDUSTRY_KEYWORDS_AUTO)} 个行业的关键词映射")
    print(f"总关键词数：{sum(len(v) for v in INDUSTRY_KEYWORDS_AUTO.values())}")
'''
    
    # 写入文件
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"\n[同步] ✓ 已生成：{OUTPUT_FILE}")
    print(f"[同步] 行业数：{len(industry_keywords)}")
    print(f"[同步] 总关键词数：{sum(len(v) for v in industry_keywords.values())}")


def main():
    """主函数"""
    # 1. 从 Tushare 同步行业成分股
    industry_keywords = build_industry_keywords()
    
    if not industry_keywords:
        print("[同步] ✗ 同步失败")
        return
    
    # 2. 生成 Python 文件
    generate_python_file(industry_keywords)
    
    # 3. 测试
    print("\n=== 测试自动关键词 ===\n")
    test_industries = ['半导体', '白酒', '软件开发', '电池', '光伏设备']
    
    # 从生成的文件导入
    import importlib.util
    spec = importlib.util.spec_from_file_location("industry_keywords_auto", OUTPUT_FILE)
    auto_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auto_module)
    
    for industry in test_industries:
        keywords = auto_module.INDUSTRY_KEYWORDS_AUTO.get(industry, [])
        print(f"{industry}: {keywords[:5]}...")


if __name__ == '__main__':
    main()
