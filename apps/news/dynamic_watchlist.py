#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 动态观察列表管理模块
P0 改进核心模块

功能:
- 管理带时效标签的观察列表
- 标签: 🟢 催化中 / 🟡 观察中 / 🔴 已过期
- 自动过期: 添加超过2周自动移除
- 自动升级: 有催化剂的股票自动打🟢
- 自动降级: 2周无催化自动打🔴并移除

数据存储: data/dynamic_watchlist.json
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# ===== 路径适配（从 workspace-marcus 迁移到 marcus-platform） =====
# 此文件位于 apps/news/，往上两级是项目根目录 marcus-platform/
WORKSPACE = Path(__file__).resolve().parents[2]
DATA_DIR = WORKSPACE / "data"
WATCHLIST_FILE = DATA_DIR / "dynamic_watchlist.json"

# 观察列表配置
WATCHLIST_CONFIG = {
    'max_stocks': 30,           # 最多保留30只
    'expiry_days': 14,          # 无催化超时天数
    'auto_upgrade_days': 3,     # 有催化自动升级天数
    'min_score_for_green': 40,  # 🟢 最低新闻分数
    'strong_catalyst_score': 70, # 🟢 强催化剂分数
}


def _load_watchlist() -> Dict:
    """加载动态观察列表"""
    if not WATCHLIST_FILE.exists():
        return {'stocks': {}}
    try:
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'stocks': {}}


def _save_watchlist(data: Dict):
    """保存动态观察列表"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _code_to_symbol(code: str) -> str:
    """代码转标准symbol"""
    code = code.strip()
    if code.startswith('SH') or code.startswith('SZ'):
        return code
    if code.startswith('6'):
        return f"SH{code}"
    return f"SZ{code}"


def _symbol_to_code(symbol: str) -> str:
    """symbol转纯代码"""
    if len(symbol) == 8:
        return symbol[2:]
    return symbol


def _get_held_symbols() -> set:
    """
    从正确持仓数据库读取当前净头寸 > 0 的标的代码集合。
    使用 data/trades.db（真实持仓）。
    """
    import sqlite3
    held = set()
    try:
        db_path = DATA_DIR / "trades.db"
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute("""
            SELECT symbol FROM (
                SELECT symbol,
                       SUM(CASE WHEN direction IN ('买入','BUY') THEN volume
                                WHEN direction IN ('卖出','SELL') THEN -volume
                                ELSE 0 END) as net_vol
                FROM trades GROUP BY symbol
            ) WHERE net_vol > 0
        """)
        for row in cur.fetchall():
            sym = row[0]
            held.add(sym)
            held.add(_symbol_to_code(sym))  # 同时加纯代码格式
        conn.close()
    except Exception as e:
        print(f"[持仓过滤] ⚠️ 读取失败: {e}", file=sys.stderr)
    return held


def add_to_watchlist(
    code: str,
    name: str = "",
    reason: str = "",
    source: str = "manual",
    news_score: int = 0,
    catalyst_keywords: List[str] = None
) -> Dict:
    """
    添加股票到动态观察列表
    
    Args:
        code: 股票代码（6位）
        name: 股票名称
        reason: 添加理由
        source: 来源 (manual/monthly/scan/trade)
        news_score: 当前新闻分数
        catalyst_keywords: 催化剂关键词
    
    Returns:
        更新后的记录
    """
    data = _load_watchlist()
    now = datetime.now()
    code = _symbol_to_code(code)
    
    # 判断标签
    tag = '🟡'  # 默认观察中
    if catalyst_keywords:
        tag = '🟢'  # 有关键词=催化中
    elif news_score >= WATCHLIST_CONFIG['strong_catalyst_score']:
        tag = '🟢'
    elif news_score >= WATCHLIST_CONFIG['min_score_for_green']:
        tag = '🟢'
    
    if code in data['stocks']:
        record = data['stocks'][code]
        record['update_time'] = now.isoformat()
        record['hit_count'] = record.get('hit_count', 0) + 1
        if name:
            record['name'] = name
        if reason:
            record['reason'] = reason
        if catalyst_keywords:
            record['catalyst_keywords'] = catalyst_keywords
        # 更新标签（只升不降，除非过期）
        if tag == '🟢' and record.get('tag') != '🔴':
            record['tag'] = '🟢'
    else:
        record = {
            'code': code,
            'symbol': _code_to_symbol(code),
            'name': name or code,
            'added_time': now.isoformat(),
            'update_time': now.isoformat(),
            'reason': reason,
            'source': source,
            'tag': tag,
            'news_score': news_score,
            'catalyst_keywords': catalyst_keywords or [],
            'hit_count': 1,
            'days_since_catalyst': 0,
            'last_catalyst_time': now.isoformat() if tag == '🟢' else None,
            'expired': False,
        }
        
        # 限制数量：超过时删除最老的🔴
        if len(data['stocks']) >= WATCHLIST_CONFIG['max_stocks']:
            _evict_expired(data)
        
        data['stocks'][code] = record
    
    _save_watchlist(data)
    return record


def _evict_expired(data: Dict):
    """驱逐过期的股票"""
    now = datetime.now()
    expired = []
    
    for code, record in data['stocks'].items():
        # 明确标记为expired的
        if record.get('expired'):
            expired.append((code, record.get('update_time', '')))
            continue
        
        # 检查是否超时未更新
        try:
            update_time = datetime.fromisoformat(record.get('update_time', ''))
            days_old = (now - update_time).days
            if days_old >= WATCHLIST_CONFIG['expiry_days']:
                record['expired'] = True
                record['tag'] = '🔴'
                expired.append((code, record.get('update_time', '')))
        except:
            pass
    
    # 删除最老的expired记录
    expired.sort(key=lambda x: x[1])
    for code, _ in expired[:5]:
        if code in data['stocks']:
            del data['stocks'][code]
            print(f"[动态观察] 🔴 移除过期股票: {code}", file=sys.stderr)


def update_watchlist_tags(catalyst_data: Dict[str, Dict] = None):
    """
    根据催化剂数据更新所有观察列表股票的标签
    
    Args:
        catalyst_data: {code: catalyst_record} from news_catalyst_tracker
    """
    data = _load_watchlist()
    now = datetime.now()

    # 剔除当前持仓标的
    held = _get_held_symbols()
    removed_held = [code for code in data['stocks'] if code in held]
    for code in removed_held:
        del data['stocks'][code]
        print(f"[动态观察] ⛔ 剔除已持仓标的: {code}", file=sys.stderr)

    for code, record in data['stocks'].items():
        if record.get('expired'):
            continue
        
        cat = None
        if catalyst_data and code in catalyst_data:
            cat = catalyst_data[code]
        
        if cat:
            score = cat.get('news_score', 0)
            keywords = cat.get('catalyst_keywords', [])
            
            if keywords or score >= WATCHLIST_CONFIG['strong_catalyst_score']:
                record['tag'] = '🟢'
                record['news_score'] = score
                record['catalyst_keywords'] = keywords
                record['last_catalyst_time'] = cat.get('update_time', now.isoformat())
                record['days_since_catalyst'] = 0
                record['expired'] = False
            elif score >= WATCHLIST_CONFIG['min_score_for_green']:
                record['tag'] = '🟡'
                record['news_score'] = score
            else:
                # 无明显催化剂
                last_cat = record.get('last_catalyst_time')
                if last_cat:
                    try:
                        days = (now - datetime.fromisoformat(last_cat)).days
                        record['days_since_catalyst'] = days
                        if days >= WATCHLIST_CONFIG['expiry_days']:
                            record['tag'] = '🔴'
                            record['expired'] = True
                            print(f"[动态观察] 🔴 {code} 超过{WATCHLIST_CONFIG['expiry_days']}天无催化，降级", file=sys.stderr)
                        elif days >= 7:
                            record['tag'] = '🟡'
                    except:
                        pass
        else:
            # 无催化剂数据，过期检查
            last_cat = record.get('last_catalyst_time')
            if last_cat:
                try:
                    days = (now - datetime.fromisoformat(last_cat)).days
                    record['days_since_catalyst'] = days
                    if days >= WATCHLIST_CONFIG['expiry_days']:
                        record['tag'] = '🔴'
                        record['expired'] = True
                except:
                    pass
    
    _save_watchlist(data)


def remove_from_watchlist(code: str) -> bool:
    """从观察列表移除股票"""
    data = _load_watchlist()
    code = _symbol_to_code(code)
    if code in data['stocks']:
        del data['stocks'][code]
        _save_watchlist(data)
        return True
    return False


def get_watchlist(tag: str = None, exclude_expired: bool = True) -> List[Dict]:
    """
    获取观察列表
    
    Args:
        tag: 过滤标签 (🟢/🟡/🔴)，None表示全部
        exclude_expired: 是否排除已过期
    
    Returns:
        [{code, name, tag, reason, ...}, ...]
    """
    data = _load_watchlist()
    results = []
    
    for code, record in data['stocks'].items():
        if exclude_expired and record.get('expired'):
            continue
        
        if tag and record.get('tag') != tag:
            continue
        
        results.append(record)
    
    # 按标签优先级排序: 🟢 > 🟡 > 🔴, 然后按更新时间
    tag_order = {'🟢': 0, '🟡': 1, '🔴': 2}
    results.sort(key=lambda x: (tag_order.get(x.get('tag', '🔴'), 2), -x.get('hit_count', 0)))
    
    return results


def get_active_watchlist() -> List[str]:
    """
    获取活跃观察列表（🟢+🟡），用于选股过滤
    
    Returns:
        ['600570', '688981', ...]
    """
    active = get_watchlist(exclude_expired=True)
    return [r['code'] for r in active if r.get('tag') != '🔴']


def get_watchlist_for_selection() -> List[str]:
    """
    获取用于选股的观察列表代码
    只返回🟢（有催化剂）的股票，排除🔴
    
    Returns:
        股票代码列表，按优先级排序
    """
    data = _load_watchlist()
    results = []
    
    for code, record in data['stocks'].items():
        if record.get('expired'):
            continue
        if record.get('tag') == '🔴':
            continue
        results.append(code)
    
    # 按新闻分数和hit_count排序
    results.sort(key=lambda c: (
        0 if data['stocks'].get(c, {}).get('tag') == '🟢' else 1,  # 🟢优先
        -(data['stocks'].get(c, {}).get('news_score', 0)),           # 高分优先
        -data['stocks'].get(c, {}).get('hit_count', 0)               # 多被命中优先
    ))
    
    return results


def merge_scan_candidates(
    watchlist_codes: List[str],
    scan_candidates: List[Dict],
    catalyst_data: Dict[str, Dict] = None
) -> List[Dict]:
    """
    合并观察列表和扫描候选股
    
    规则:
    1. 观察列表🟢股票优先（绝对入选）
    2. 扫描候选股中有催化剂的次优先
    3. 其他候选股按分数排序
    
    Args:
        watchlist_codes: 观察列表代码
        scan_candidates: 扫描候选股 [{symbol, name, price, score, reason}, ...]
        catalyst_data: 催化剂数据
    
    Returns:
        合并后的候选列表
    """
    watchlist_set = set(watchlist_codes)
    
    # 分类
    watchlist_green = []    # 观察列表🟢
    watchlist_yellow = []   # 观察列表🟡
    candidate_with_cat = [] # 候选股有催化剂
    candidate_without = []  # 候选股无催化剂
    
    for cand in scan_candidates:
        code = _symbol_to_code(cand.get('symbol', ''))
        
        if code in watchlist_set:
            record = _load_watchlist()['stocks'].get(code, {})
            tag = record.get('tag', '🟡')
            if tag == '🟢':
                watchlist_green.append({**cand, '_source': 'watchlist_green', '_tag': '🟢'})
            else:
                watchlist_yellow.append({**cand, '_source': 'watchlist_yellow', '_tag': '🟡'})
        else:
            # 检查是否有催化剂
            has_cat = False
            if catalyst_data and code in catalyst_data:
                cat = catalyst_data[code]
                if cat.get('catalyst_keywords') or cat.get('news_score', 0) >= 40:
                    has_cat = True
            
            if has_cat:
                candidate_with_cat.append({**cand, '_source': 'candidate_cat', '_tag': '🟢'})
            else:
                candidate_without.append({**cand, '_source': 'candidate_nocat', '_tag': '🟡'})
    
    # 按分数排序
    for lst in [watchlist_yellow, candidate_with_cat, candidate_without]:
        lst.sort(key=lambda x: x.get('score', 0), reverse=True)
    
    # 合并：观察列表🟢 → 候选有催化 → 观察列表🟡 → 候选无催化
    merged = (
        watchlist_green +
        candidate_with_cat +
        watchlist_yellow +
        candidate_without
    )
    
    return merged


def format_watchlist_summary() -> str:
    """格式化观察列表摘要"""
    all_stocks = get_watchlist(exclude_expired=False)
    green = [s for s in all_stocks if s.get('tag') == '🟢']
    yellow = [s for s in all_stocks if s.get('tag') == '🟡']
    red = [s for s in all_stocks if s.get('tag') == '🔴']
    
    lines = [
        "📋 动态观察列表摘要",
        f"  🟢 催化中: {len(green)} 只",
        f"  🟡 观察中: {len(yellow)} 只",
        f"  🔴 已过期: {len(red)} 只",
        f"  合计: {len(all_stocks)} 只",
        ""
    ]
    
    if green:
        lines.append("🟢 催化中:")
        for s in green[:5]:
            score = s.get('news_score', 0)
            cats = ','.join(s.get('catalyst_keywords', [])[:3])
            lines.append(f"  • {s['code']} {s.get('name','')} (新闻{score:.0f}分) {cats}")
    
    if yellow:
        lines.append("🟡 观察中:")
        for s in yellow[:5]:
            lines.append(f"  • {s['code']} {s.get('name','')} (无明显催化)")
    
    return '\n'.join(lines)


def add_batch_from_scan(candidates: List[Dict], source: str = "scan"):
    """
    从扫描候选股批量添加到观察列表
    
    Args:
        candidates: [{symbol, name, score, catalyst_keywords, news_score, reason}, ...]
        source: 来源
    """
    added = 0
    for cand in candidates:
        code = _symbol_to_code(cand.get('symbol', ''))
        if not code:
            continue
        
        # 检查是否已在列表
        data = _load_watchlist()
        if code in data['stocks']:
            continue

        # 排除当前持仓标的
        held = _get_held_symbols()
        if code in held:
            print(f"[动态观察] ⛔ 跳过已持仓标的: {code}", file=sys.stderr)
            continue

        add_to_watchlist(
            code=code,
            name=cand.get('name', ''),
            reason=cand.get('reason', ''),
            source=source,
            news_score=cand.get('news_score', cand.get('score', 0)),
            catalyst_keywords=cand.get('catalyst_keywords', [])
        )
        added += 1
    
    if added > 0:
        print(f"[动态观察] ✅ 从{source}新增 {added} 只到观察列表", file=sys.stderr)


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 dynamic_watchlist.py --summary")
        print("  python3 dynamic_watchlist.py --active")
        print("  python3 dynamic_watchlist.py --add 600570 [name] [reason]")
        print("  python3 dynamic_watchlist.py --remove 600570")
        print("  python3 dynamic_watchlist.py --update_tags")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == '--summary':
        print(format_watchlist_summary())
    elif cmd == '--active':
        stocks = get_watchlist(exclude_expired=True)
        print(f"活跃观察列表: {len(stocks)} 只\n")
        for s in stocks:
            cats = ','.join(s.get('catalyst_keywords', [])[:3])
            print(f"{s['tag']} {s['code']} {s.get('name','')} | 新闻{s.get('news_score',0):.0f}分 | {cats} | 来源:{s.get('source','')}")
    elif cmd == '--add':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        name = sys.argv[3] if len(sys.argv) > 3 else ''
        reason = sys.argv[4] if len(sys.argv) > 4 else ''
        if code:
            r = add_to_watchlist(code, name, reason, source='manual')
            print(f"✅ 添加 {r['code']} {r['name']} {r['tag']}")
        else:
            print("⚠️ 需要股票代码")
    elif cmd == '--remove':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        if code:
            ok = remove_from_watchlist(code)
            print(f"{'✅ 移除' if ok else '⚠️ 未找到'} {code}")
    elif cmd == '--update_tags':
        update_watchlist_tags()
        print("✅ 标签更新完成")
        print(format_watchlist_summary())
