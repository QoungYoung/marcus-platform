# -*- coding: utf-8 -*-
"""Validate sub-decision logic against backtest data."""
import io

CATEGORIES = {
    '价值防御': ['酿酒','白酒','茅指数','大盘价值','红利股','红利破净','中特估','银行','保险','破净股','高股息','价值股','超级品牌','上证50','权重股','中字头','宁组合'],
    '科技成长': ['半导体','AI','芯片','算力','数字经济','IPv6','VPN','云计算','区块链','信创','机器人','AI语料','Kimi','DeepSeek','AI智能体','华为欧拉','EDA','腾讯云','百度','阿里','互联网服务','WiFi','空间计算','数字阅读','财税数字化'],
    '医药': ['CRO','创新药','肝炎','减肥药','中药','独家药品','特色药','肝素','SPD','流感','维生素','精准诊断','医药医疗','创新医疗'],
    '资源': ['贵金属','黄金','稀有金属','有色金属','煤炭','油气','资源开采','煤化工','钢铁'],
    '电力/能源': ['绿色电力','超超临界发电','生物质能发电','抽水蓄能','虚拟电厂','光伏','风电','储能','麒麟电池','新能源'],
    '消费': ['食品饮料','家电','旅游','免税','社区团购','味蕾经济','影视','旅游酒店','水产','预制菜','猪肉','鸡肉','供销社','托育服务','退税商店'],
    '金融': ['券商','跨境支付','参股保险','参股新三板','蚂蚁概念','数字货币','供应链金融'],
    '基建/交通': ['铁路','港口','水泥','一带一路','工程建设','大基建','东北振兴','水利','地下管网','土壤修复'],
}

def classify(name):
    for cat, kws in CATEGORIES.items():
        for kw in kws:
            if kw in name:
                return cat
    return '其他'

days = {
    '7/13': ['特色药','独家药品','肝素概念','华为欧拉','资源开采概念'],
    '7/14': ['资源开采概念','肝素概念','特色药','独家药品','SPD概念'],
    '7/15': ['CRO','肝炎概念','创新药','减肥药','医药医疗风格'],
    '7/16': ['肝素概念','特色药','SPD概念','流感','维生素'],
    '7/17': ['超超临界发电','数字货币','生物质能发电','托育服务','土壤修复'],
    '7/20': ['酿酒概念','茅指数','大盘价值','红利破净股','SPD概念'],
}

overlaps = {'7/14': 7, '7/15': 5, '7/16': 3, '7/17': 0, '7/20': 0}

out = io.StringIO()
out.write(f"{'日期':<8} {'重叠':<6} {'判定':<14} {'TOP5归类':<50} {'子判定':<20} {'AI行为'}\n")
out.write('-' * 120 + '\n')

for date, top5 in days.items():
    cats = [classify(n) for n in top5]
    cat_counts = {}
    for c in cats:
        cat_counts[c] = cat_counts.get(c, 0) + 1
    max_cat = max(cat_counts, key=cat_counts.get)
    max_count = cat_counts[max_cat]

    if date == '7/13':
        overlap = '-'
        status = 'baseline'
        sub = '-'
        action = '正常做多医药'
    else:
        overlap = overlaps[date]
        if overlap >= 7:
            status = '主线延续'
            sub = '-'
            action = '正常仓位'
        elif overlap >= 4:
            status = '主线松动'
            sub = '-'
            action = '仓位减半'
        else:
            status = '风格切换'
            # Find max category EXCLUDING "其他"
            valid_cats = {k: v for k, v in cat_counts.items() if k != '其他'}
            max_valid = max(valid_cats.values()) if valid_cats else 0
            if max_valid >= 3:
                sub = '新主线形成·试探'
                action = f'第一等<=3% ({max(valid_cats, key=valid_cats.get)})'
            else:
                sub = '混乱期·空仓'
                action = '空仓观望'

    cat_str = ', '.join([f'{n}=>{classify(n)[:3]}' for n in top5])
    out.write(f'{date:<8} {str(overlap):<6} {status:<14} {cat_str:<50} {sub:<20} {action}\n')

out.write('\n')
out.write('=== 关键验证 ===\n')
out.write('7/17: 超超临界发电(电力)+数字货币(其他)+生物质能(电力)+托育(其他)+土壤(其他)\n')
out.write('      归类跨度=4类 -> 混乱期 -> 空仓   实际: 7/17方向一日游,7/20全线退出 -> 空仓是对的\n')
out.write('\n')
out.write('7/20: 酿酒(价值)+茅指数(价值)+大盘价值(价值)+红利破净(价值)+SPD(医药)\n')
out.write('      4个价值防御 -> 新主线形成 -> 试探  实际: 7/20方向延续中\n')
print(out.getvalue())
