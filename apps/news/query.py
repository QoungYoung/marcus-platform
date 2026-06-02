#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AKShare 新闻资讯查询命令行工具
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from akshare_engine import AKShareEngine


def cmd_stock(engine, args):
    """查询个股新闻"""
    keyword = args.keyword
    limit = getattr(args, 'limit', 20)
    
    news = engine.get_stock_news(keyword, limit=limit)
    if news:
        engine.show_news(news, limit=limit)


def cmd_finance(engine, args):
    """查询财经新闻"""
    limit = getattr(args, 'limit', 50)
    
    news = engine.get_finance_news(limit=limit)
    if news:
        engine.show_news(news, limit=limit)


def cmd_hot(engine, args):
    """查询热门新闻"""
    limit = getattr(args, 'limit', 20)
    
    news = engine.get_hot_news(limit=limit)
    if news:
        engine.show_news(news, limit=limit)


def cmd_export(engine, args):
    """导出数据"""
    data_type = args.data_type
    keyword = args.keyword
    limit = getattr(args, 'limit', 100)
    fmt = getattr(args, 'format', 'json')
    
    # 获取数据
    if data_type == 'stock':
        news = engine.get_stock_news(keyword, limit=limit)
        filename = f"news_{keyword}_{limit}.{fmt}"
    elif data_type == 'finance':
        news = engine.get_finance_news(limit=limit)
        filename = f"finance_news_{limit}.{fmt}"
    else:
        print("❌ 不支持的数据类型")
        return
    
    if news:
        if fmt == 'json':
            engine.export_to_json(news, filename)
        elif fmt == 'csv':
            engine.export_to_csv(news, filename)


def main():
    parser = argparse.ArgumentParser(description='AKShare 新闻资讯查询工具')
    parser.add_argument('--data-dir', default='./data', help='数据目录')
    
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # stock 命令
    stock_parser = subparsers.add_parser('stock', help='查询个股新闻')
    stock_parser.add_argument('keyword', help='股票名称或代码')
    stock_parser.add_argument('-l', '--limit', type=int, default=20, help='返回数量')
    
    # finance 命令
    finance_parser = subparsers.add_parser('finance', help='查询财经新闻')
    finance_parser.add_argument('-l', '--limit', type=int, default=50, help='返回数量')
    
    # hot 命令
    hot_parser = subparsers.add_parser('hot', help='查询热门新闻')
    hot_parser.add_argument('-l', '--limit', type=int, default=20, help='返回数量')
    
    # export 命令
    export_parser = subparsers.add_parser('export', help='导出数据')
    export_parser.add_argument('data_type', choices=['stock', 'finance'], help='数据类型')
    export_parser.add_argument('keyword', nargs='?', default='', help='股票名称（stock 类型需要）')
    export_parser.add_argument('-l', '--limit', type=int, default=100, help='返回数量')
    export_parser.add_argument('-f', '--format', choices=['json', 'csv'], default='json', help='导出格式')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(0)
    
    # 创建引擎
    engine = AKShareEngine(data_dir=args.data_dir)
    
    # 执行命令
    commands = {
        'stock': cmd_stock,
        'finance': cmd_finance,
        'hot': cmd_hot,
        'export': cmd_export,
    }
    
    if args.command in commands:
        commands[args.command](engine, args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
