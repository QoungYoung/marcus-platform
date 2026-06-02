#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
THS 板块轮动动态检测模块

功能：
1. 从同花顺（THS）资金流接口获取所有概念板块排行
2. 结合「即时」「3日排行」「10日排行」多维度计算板块动量分
3. 自动识别资金流入板块（板块轮动信号）
4. 支持板块名称 → Tushare 行业/概念 映射，返回成分股
5. 缓存 10 分钟，避免重复请求

数据源：
- akshare: stock_fund_flow_concept (THS 概念资金流)
- akshare: stock_board_concept_name_ths (THS 概念列表)
- akshare: stock_board_concept_info_ths (THS 板块快讯)
- tushare: stock_basic (个股行业分类)

使用示例：
    from fetch_ths_sector_rotation import THSSectorRotation
    rotator = THSSectorRotation()
    hot = rotator.get_hot_sectors(top_n=20)
    print(hot)
"""

import sys
import json
import time
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from functools import lru_cache

# Use workspace_detector for cross-platform path resolution
sys.path.insert(0, str(Path(__file__).parent))
try:
    from workspace_detector import WORKSPACE
    HAS_WORKSPACE_DETECTOR = True
except ImportError:
    HAS_WORKSPACE_DETECTOR = False

# 弱依赖：所有失败都有 fallback
try:
    import akshare as ak
    AKSHARE_OK = True
except ImportError:
    AKSHARE_OK = False
    print("[THS轮动] ⚠ akshare 未安装", file=sys.stderr)

try:
    import tushare as ts
    TUSHARE_OK = True
except ImportError:
    TUSHARE_OK = False

# 缓存路径
CACHE_DIR = Path(__file__).parent / "data"
CACHE_DIR.mkdir(exist_ok=True)
SECTOR_CACHE_FILE = CACHE_DIR / "ths_hot_sectors.json"
STOCKS_CACHE_FILE = CACHE_DIR / "ths_concept_stocks.json"

# 缓存有效期（分钟）
CACHE_TTL_MINUTES = 10


# ============================================================
# 板块名称 → 申万行业/关键词 映射
# ============================================================
# 当 THS 概念名称无法直接映射时，使用关键词 fallback
SECTOR_KEYWORD_MAP = {
    # 商业航天（核心新增）
    '商业航天': {
        'ths_concepts': ['商业航天', '军工', '卫星导航', '成飞概念'],
        'tushare_industry': ['航空', '船舶', '运输设备'],
        'keywords': ['航天', '火箭', '卫星', '商业航天', '天龙', '蓝箭', '星际荣耀',
                     '东方空间', '中航', '航天科技', '航天电器', '火箭股份', '龙溪股份'],
    },
    '军工': {
        'ths_concepts': ['军工', '军工信息化', '国产航母', '军民融合', '成飞概念'],
        'tushare_industry': ['航空', '船舶', '运输设备'],
        'keywords': ['军工', '中航', '航发', '兵装', '核工业', '航天科工', '航天科技'],
    },
    '军工信息化': {
        'ths_concepts': ['军工信息化', '军工', '卫星导航'],
        'tushare_industry': ['航空', '通信设备'],
        'keywords': ['军工信息化', '雷达', '电子对抗', '指挥系统', '军用通信'],
    },
    '卫星导航': {
        'ths_concepts': ['卫星导航', '商业航天', '卫星互联网'],
        'tushare_industry': ['通信设备', '航空'],
        'keywords': ['卫星', '导航', '北斗', 'GPS', '遥感', '测控'],
    },
    '卫星互联网': {
        'ths_concepts': ['卫星互联网', '卫星导航', '商业航天'],
        'tushare_industry': ['通信设备', '航空'],
        'keywords': ['卫星互联网', '低轨卫星', '星链', '6G'],
    },
}


class THSSectorRotation:
    """THS 板块轮动检测器"""

    def __init__(self, cache_dir: Path = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(exist_ok=True)
        self.tushare_pro = None
        self._init_tushare()

    def _init_tushare(self):
        """初始化 Tushare（Token 统一从 .env 的 TUSHARE_TOKEN 读取）"""
        if not TUSHARE_OK:
            return
        try:
            from _api_config import get_tushare_pro
            self.tushare_pro = get_tushare_pro()
            print("[THS轮动] ✓ Tushare 已连接", file=sys.stderr)
        except Exception as e:
            print(f"[THS轮动] ⚠ Tushare 初始化失败: {e}", file=sys.stderr)

    # --------------------------------------------------------
    # 核心 API：获取热点板块列表
    # --------------------------------------------------------
    def get_hot_sectors(self, top_n: int = 20, force_refresh: bool = False) -> List[Dict]:
        """
        获取当前最热门的板块（多维度动量评分）

        Args:
            top_n: 返回前 N 个热点板块
            force_refresh: 强制刷新缓存

        Returns:
            [{name, score, rank, change_pct, net_flow, stock_count, leader_stock, leader_change}, ...]
        """
        # 1. 检查缓存
        if not force_refresh:
            cached = self._read_cache(SECTOR_CACHE_FILE)
            if cached:
                age = (datetime.now() - datetime.fromisoformat(cached['updated_at'])).total_seconds() / 60
                if age < CACHE_TTL_MINUTES:
                    print(f"[THS轮动] ✓ 命中缓存（{age:.0f}分钟前）", file=sys.stderr)
                    return cached['sectors'][:top_n]

        # 2. 获取多维度数据
        sectors = self._fetch_all_dimensions()
        if not sectors:
            print("[THS轮动] ⚠ 获取板块数据失败，返回空列表", file=sys.stderr)
            return []

        # 3. 计算综合动量分并排序
        sectors = self._score_sectors(sectors)
        sectors.sort(key=lambda x: x['score'], reverse=True)

        # 4. 缓存
        self._write_cache(SECTOR_CACHE_FILE, {
            'updated_at': datetime.now().isoformat(),
            'sectors': sectors,
        })

        print(f"[THS轮动] ✓ 获取到 {len(sectors)} 个板块，前5：", file=sys.stderr)
        for s in sectors[:5]:
            print(f"    {s['name']} | 动量分={s['score']:.1f} | 涨跌={s.get('change_pct', 'N/A')} | 净额={s.get('net_flow', 'N/A')}亿", file=sys.stderr)

        return sectors[:top_n]

    def _fetch_all_dimensions(self) -> List[Dict]:
        """从 THS 获取多维度资金流数据"""
        sectors = {}  # {name: sector_dict}
        got_instant = False

        # 即时数据（含涨跌幅、领涨股）— 优先获取，给3次机会
        for attempt in range(3):
            try:
                df = ak.stock_fund_flow_concept(symbol='即时')
                if df is not None and len(df) > 0:
                    for _, row in df.iterrows():
                        name = str(row.get('行业', '')).strip()
                        if not name:
                            continue
                        sectors[name] = {
                            'name': name,
                            'instant_rank': int(row.get('序号', 999)),
                            'instant_change': float(row.get('行业-涨跌幅', 0) or 0),
                            'instant_net_flow': float(row.get('净额', 0) or 0),
                            'instant_flow_in': float(row.get('流入资金', 0) or 0),
                            'instant_flow_out': float(row.get('流出资金', 0) or 0),
                            'stock_count': int(row.get('公司家数', 0) or 0),
                            'leader_stock': str(row.get('领涨股', '')),
                            'leader_change': float(row.get('领涨股-涨跌幅', 0) or 0),
                            '_has_instant': True,
                        }
                    got_instant = True
                    print(f"[THS轮动] ✓ 即时数据：{len(sectors)} 个板块", file=sys.stderr)
                    break
            except Exception as e:
                print(f"[THS轮动] ⚠ 即时数据获取失败（尝试{attempt+1}）: {e}", file=sys.stderr)
                time.sleep(2)

        # 3日排行（补充中期趋势）— 不覆盖已有的即时数据
        try:
            df = ak.stock_fund_flow_concept(symbol='3日排行')
            if df is not None:
                for _, row in df.iterrows():
                    name = str(row.get('行业', '')).strip()
                    if name in sectors and sectors[name].get('_has_instant'):
                        # 保留即时数据，补充3日数据
                        sectors[name]['flow_3d_change'] = float(row.get('阶段涨跌幅', 0) or 0)
                        sectors[name]['flow_3d_net'] = float(row.get('净额', 0) or 0)
                    else:
                        # 没有即时数据，用3日排行降级
                        sectors[name] = {
                            'name': name,
                            'instant_rank': int(row.get('序号', 999)),
                            'instant_change': 0.0,  # 降级标记
                            'instant_net_flow': float(row.get('净额', 0) or 0),
                            'instant_flow_in': float(row.get('流入资金', 0) or 0),
                            'instant_flow_out': float(row.get('流出资金', 0) or 0),
                            'stock_count': int(row.get('公司家数', 0) or 0),
                            'leader_stock': '',
                            'leader_change': 0.0,
                            'flow_3d_change': float(row.get('阶段涨跌幅', 0) or 0),
                            'flow_3d_net': float(row.get('净额', 0) or 0),
                            '_has_instant': False,
                            '_is_fallback': True,
                        }
        except Exception as e:
            print(f"[THS轮动] ⚠ 3日排行获取失败: {e}", file=sys.stderr)

        # 10日排行（补充长期趋势）
        try:
            df = ak.stock_fund_flow_concept(symbol='10日排行')
            if df is not None:
                for _, row in df.iterrows():
                    name = str(row.get('行业', '')).strip()
                    if name in sectors:
                        sectors[name]['flow_10d_change'] = float(row.get('阶段涨跌幅', 0) or 0)
                        sectors[name]['flow_10d_net'] = float(row.get('净额', 0) or 0)
        except Exception as e:
            print(f"[THS轮动] ⚠ 10日排行获取失败: {e}", file=sys.stderr)

        if got_instant:
            print(f"[THS轮动] ✓ 最终板块数：{len(sectors)}（即时模式）", file=sys.stderr)
        else:
            print(f"[THS轮动] ⚠ 最终板块数：{len(sectors)}（3日排行降级模式）", file=sys.stderr)

        return list(sectors.values())

    def _score_sectors(self, sectors: List[Dict]) -> List[Dict]:
        """计算板块综合动量分（0-100）"""
        for s in sectors:
            score = 0.0
            is_fallback = s.get('_is_fallback', False)

            # 即时维度（40%）：涨跌幅 + 资金净额
            if not is_fallback:
                instant_change = s.get('instant_change', 0)
                instant_net = s.get('instant_net_flow', 0)
                # 涨跌贡献：±3% 对应 ±30分（±10分/1%）
                score += max(-30, min(30, instant_change * 10))
                # 资金净额贡献：流入越多分越高（每50亿=+10分，上限±20分）
                score += max(-20, min(20, instant_net / 5))
                # 排名贡献：序号越小分越高（10分封顶）
                rank = s.get('instant_rank', 999)
                score += max(0, 10 - rank * 0.05)

            # 3日维度（35%）
            flow_3d = s.get('flow_3d_change', 0)
            score += max(-20, min(20, flow_3d * 4))
            flow_3d_net = s.get('flow_3d_net', 0)
            score += max(-15, min(15, flow_3d_net / 10))

            # 10日维度（25%）
            flow_10d = s.get('flow_10d_change', 0)
            score += max(-15, min(15, flow_10d * 2))
            flow_10d_net = s.get('flow_10d_net', 0)
            score += max(-10, min(10, flow_10d_net / 20))

            # 资金强度加成：公司家数越多、成交额越大，分数越高（最多+10分）
            stock_count = s.get('stock_count', 0)
            score += min(5, stock_count / 50)

            # 领涨股加成：领涨股涨停（+10%）+5分
            leader_change = s.get('leader_change', 0)
            if leader_change > 5:
                score += 5
            elif leader_change > 0:
                score += leader_change

            # 归一化到 0-100，以50为基准
            s['score'] = max(0, min(100, 50 + score))

        return sectors

    # --------------------------------------------------------
    # 核心 API：获取板块成分股
    # --------------------------------------------------------
    def get_sector_stocks(self, sector_name: str, force_refresh: bool = False) -> List[Dict]:
        """
        获取指定板块的成分股

        策略（优先级递减）：
        1. THS 板块快讯数据（涨跌幅、成交量 → 判断是否值得交易）
        2. Tushare SW 行业分类（申万行业 → 个股）
        3. 关键词新闻匹配（新闻中提到的个股）

        Args:
            sector_name: THS 板块名称，如"商业航天"
            force_refresh: 强制刷新

        Returns:
            [{code, name, ts_code, industry, reason}, ...]
        """
        cache_key = f"stocks_{sector_name}"
        cached = self._read_cache(STOCKS_CACHE_FILE)
        if cached and not force_refresh:
            entry = cached.get(cache_key)
            if entry:
                age = (datetime.now() - datetime.fromisoformat(entry.get('updated_at', '2000-01-01'))).total_seconds() / 60
                if age < CACHE_TTL_MINUTES:
                    return entry.get('stocks', [])

        stocks = []

        # 方法1：Tushare 申万行业（最可靠的个股列表）
        stocks += self._get_stocks_by_tushare_industry(sector_name)

        # 方法2：新闻关键词匹配
        stocks += self._get_stocks_by_news_keywords(sector_name)

        # 去重（保留最早出现的，即优先级最高的）
        seen = set()
        unique = []
        for s in stocks:
            if s['code'] not in seen:
                seen.add(s['code'])
                unique.append(s)

        # 缓存
        if cached is None:
            cached = {}
        cached[cache_key] = {
            'updated_at': datetime.now().isoformat(),
            'stocks': unique,
        }
        self._write_cache(STOCKS_CACHE_FILE, cached)

        print(f"[THS轮动] ✓ 板块 '{sector_name}' 获取 {len(unique)} 只成分股", file=sys.stderr)
        return unique

    def _get_stocks_by_tushare_industry(self, sector_name: str) -> List[Dict]:
        """通过 Tushare 申万行业获取个股"""
        if not self.tushare_pro:
            return []

        # 查映射表
        mapping = SECTOR_KEYWORD_MAP.get(sector_name, {})
        tushare_industries = mapping.get('tushare_industry', [])

        if not tushare_industries:
            # 智能推断：板块名 → 申万行业
            tushare_industries = self._infer_industry(sector_name)

        if not tushare_industries:
            return []

        stocks = []
        for industry in tushare_industries:
            try:
                df = self.tushare_pro.stock_basic(
                    exchange='', list_status='L',
                    fields='ts_code,symbol,name,industry,market'
                )
                if 'industry' in df.columns:
                    matched = df[df['industry'] == industry]
                    for _, row in matched.iterrows():
                        code = str(row['symbol'])
                        stocks.append({
                            'code': code,
                            'name': str(row['name']),
                            'ts_code': str(row['ts_code']),
                            'industry': industry,
                            'reason': f'Tushare行业:{industry}',
                        })
            except Exception as e:
                print(f"[THS轮动] ⚠ Tushare行业查询失败 ({industry}): {e}", file=sys.stderr)

        return stocks

    def _get_stocks_by_news_keywords(self, sector_name: str) -> List[Dict]:
        """通过新闻关键词匹配获取个股"""
        mapping = SECTOR_KEYWORD_MAP.get(sector_name, {})
        keywords = mapping.get('keywords', [])

        if not keywords:
            keywords = [sector_name]  # 用板块名本身

        stocks = []

        # 尝试从本地新闻缓存匹配
        try:
            news_cache = CACHE_DIR / "news_cache.json"
            if news_cache.exists():
                with open(news_cache) as f:
                    news_list = json.load(f)
                for news in news_list[-200:]:  # 最近200条
                    text = news.get('title', '') + ' ' + news.get('content', '')
                    for kw in keywords:
                        if kw in text:
                            # 尝试提取股票代码/名称
                            import re
                            codes = re.findall(r'(?:SH|SZ)?([0-6][0-9]{5})', text)
                            names = re.findall('[\u4e00-\u9fa5]{2,6}(?:股份|集团|科技|电子|航空|机电|动力|控制|系统)', text)
                            for code in codes[:3]:
                                if len(code) == 6:
                                    stocks.append({
                                        'code': code,
                                        'name': '',
                                        'ts_code': f"{code}.SH" if code.startswith(('5', '6', '9')) else f"{code}.SZ",
                                        'industry': sector_name,
                                        'reason': f'新闻关键词:{kw}',
                                    })
                            for name in names[:3]:
                                stocks.append({
                                    'code': '',
                                    'name': name,
                                    'ts_code': '',
                                    'industry': sector_name,
                                    'reason': f'新闻关键词:{kw}',
                                })
                            break  # 一个关键词匹配即可
        except Exception as e:
            print(f"[THS轮动] ⚠ 新闻匹配失败: {e}", file=sys.stderr)

        return stocks

    def _infer_industry(self, sector_name: str) -> List[str]:
        """根据板块名称智能推断申万行业"""
        infer_map = {
            '商业航天': ['航空', '船舶', '运输设备'],
            '军工': ['航空', '船舶', '运输设备'],
            '军工信息化': ['航空', '通信设备'],
            '卫星导航': ['通信设备', '航空'],
            '卫星互联网': ['通信设备'],
            '新能源汽车': ['汽车整车', '汽车配件'],
            '固态电池': ['电气设备'],
            '人形机器人': ['机械基件', '电器仪表'],
            '低空经济': ['航空', '运输设备'],
            '创新药': ['化学制药', '生物制药'],
            'AI': ['软件服务', 'IT设备'],
        }
        return infer_map.get(sector_name, [])

    # --------------------------------------------------------
    # 辅助方法
    # --------------------------------------------------------
    def _read_cache(self, path: Path) -> Optional[dict]:
        """读取缓存"""
        try:
            if path.exists():
                with open(path) as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    def _write_cache(self, path: Path, data: dict):
        """写入缓存"""
        try:
            with open(path, 'w') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[THS轮动] ⚠ 缓存写入失败: {e}", file=sys.stderr)

    def get_sector_spot(self, sector_name: str) -> Optional[Dict]:
        """获取板块快讯（涨跌幅/资金流/成分股数）"""
        try:
            df = ak.stock_board_concept_info_ths(symbol=sector_name)
            if df is not None and len(df) > 0:
                result = {}
                for _, row in df.iterrows():
                    item = str(row.get('项目', ''))
                    value = str(row.get('值', ''))
                    result[item] = value
                return result
        except Exception as e:
            print(f"[THS轮动] ⚠ 板块快讯获取失败 ({sector_name}): {e}", file=sys.stderr)
        return None


# ============================================================
# 便捷函数
# ============================================================
_instance = None

def get_hot_sectors(top_n: int = 20, force_refresh: bool = False) -> List[Dict]:
    """获取热点板块（模块级便捷函数）"""
    global _instance
    if _instance is None:
        _instance = THSSectorRotation()
    return _instance.get_hot_sectors(top_n=top_n, force_refresh=force_refresh)


def get_sector_stocks(sector_name: str, force_refresh: bool = False) -> List[Dict]:
    """获取板块成分股（模块级便捷函数）"""
    global _instance
    if _instance is None:
        _instance = THSSectorRotation()
    return _instance.get_sector_stocks(sector_name=sector_name, force_refresh=force_refresh)


# ============================================================
# 测试
# ============================================================
if __name__ == '__main__':
    print("=== THS 板块轮动检测 ===\n")

    rotator = THSSectorRotation()

    print("--- 热点板块 Top10 ---")
    hot = rotator.get_hot_sectors(top_n=10, force_refresh=True)
    for i, s in enumerate(hot, 1):
        change = s.get('instant_change', s.get('flow_3d_change', 0))
        net = s.get('instant_net_flow', s.get('flow_3d_net', 0))
        mode = "(即时)" if not s.get('_is_fallback') else "(3日)"
        print(f"  {i:2d}. {s['name']:<15} | 动量分={s['score']:5.1f}{mode} | "
              f"涨跌={change:>7.2f}% | "
              f"净额={net:>8.1f}亿 | "
              f"领涨={s.get('leader_stock', 'N/A') or 'N/A':<8} "
              f"(±{s.get('leader_change', 0):.1f}%)")

    print()
    print("--- 商业航天 板块详情 ---")
    spot = rotator.get_sector_spot('商业航天')
    if spot:
        for k, v in spot.items():
            print(f"  {k}: {v}")

    print()
    print("--- 商业航天 成分股 ---")
    stocks = rotator.get_sector_stocks('商业航天')
    print(f"  共 {len(stocks)} 只（申万行业匹配）")
    for s in stocks[:10]:
        print(f"  {s['code']} {s['name']:<10} | {s['industry']} | {s['reason']}")
