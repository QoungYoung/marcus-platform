#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雪球数据查询引擎

行情数据源已切换为腾讯 qt.gtimg.cn（免认证/无频率限制/60+字段）。

支持：
- 股票实时行情查询（腾讯 qt.gtimg.cn）
- 组合数据查询（雪球）
- 财务数据查询（雪球）
- 基金数据查询（雪球）
- 数据持久化存储
"""

import pysnowball as ball
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
import sqlite3


class XueqiuEngine:
    """
    雪球数据查询引擎
    
    功能：
    - 股票行情查询
    - 组合数据查询
    - 财务数据查询
    - 基金数据查询
    - 数据持久化
    """
    
    def __init__(self, config_file: str = "config.json", data_dir: str = "./data"):
        """
        初始化引擎
        
        Args:
            config_file: 配置文件路径
            data_dir: 数据目录
        """
        self.config = self._load_config(config_file)
        self.data_dir = os.path.expanduser(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        
        # 设置 token（优先环境变量，兼容 Docker 部署）
        self.token = os.getenv('XUEQIU_TOKEN', self.config.get('token', ''))
        if not self.token:
            self.token = self.config.get('token', '')
        if self.token:
            ball.set_token(self.token)
            print(f"[OK] Token 已配置")
        else:
            print("[WARN] 未配置 Token，部分 API 可能无法使用")

        # 完整 Cookie（优先使用 config 中的 cookie 字段，支持完整雪球认证）
        self.cookie = os.getenv('XUEQIU_COOKIE', self.config.get('cookie', ''))
        if self.cookie:
            print(f"[OK] 完整 Cookie 已配置")
        else:
            print("[INFO] 未配置完整 Cookie，将使用 token+u 拼接")
        
        # 初始化数据库
        self.db_file = os.path.join(self.data_dir, "cache.db")
        self._init_database()

    def _get_cookie(self) -> str:
        """获取 Cookie 字符串。优先使用完整 cookie，其次用 token+u 拼接。"""
        if self.cookie:
            return self.cookie
        u = self.config.get('u', '')
        return f"{self.token} u={u}" if u else self.token

    def _load_config(self, config_file: str) -> dict:
        """加载配置文件"""
        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            print(f"[WARN] 配置文件不存在：{config_file}")
            return {}
    
    def _init_database(self):
        """初始化 SQLite 缓存数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # 创建股票行情缓存表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_quotes (
                symbol TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        
        # 创建组合数据缓存表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cube_data (
                symbol TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        
        # 创建财务数据缓存表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS financial_data (
                symbol TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')

        # 创建 ETF 行情缓存表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etf_quotes (
                symbol TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')

        # 创建 ETF K线缓存表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etf_kline (
                symbol TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')

        # 创建 ETF 详情缓存表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etf_detail (
                symbol TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')

        # 创建 ETF 板块池表（从雪球API同步）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etf_pool (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                sector TEXT,
                catalyst_type TEXT,
                priority INTEGER,
                data TEXT,
                updated_at TEXT NOT NULL
            )
        ''')

        conn.commit()
        conn.close()
    
    # ========== 股票相关 ==========
    
    def _tencent_to_symbol(self, symbol: str) -> str:
        """将 SH600519 转为腾讯格式 sh600519"""
        symbol = symbol.upper().strip()
        if '.' in symbol:
            code, market = symbol.split('.')
            return f"{market.lower()}{code}"
        if symbol.startswith(('SH', 'SZ', 'BJ', 'HK')):
            market = symbol[:2].lower()
            code = symbol[2:]
            return f"{market}{code}"
        return symbol.lower()

    def _parse_tencent_quote(self, symbol: str, raw: str) -> Optional[dict]:
        """
        解析腾讯 qt.gtimg.cn 返回的行情数据，映射为雪球兼容的 dict 格式。
        
        腾讯字段索引（~分隔）：
        [0]市场 [1]名称 [2]代码 [3]当前价 [4]昨收 [5]今开 [6]涨跌量
        [7]涨停价 [8]跌停价 [9]流通股本 [10]量比
        [11-20]买卖5档 [30]日期 [31]涨跌额 [32]涨跌幅%
        [33]最高 [34]最低 [36]成交量(手) [37]成交额(万元)
        [38]换手率% [39]静态PE [40]开盘金额 [41]委比 [42]外盘
        [43]振幅% [44]流通市值 [45]总市值 [46]PB [47]涨停幅度
        [48]跌停幅度 [49]年最高 [50]年最低 [51]均价 [57]52周最高
        [58]52周最低 ...更多
        """
        if not raw or '~' not in raw:
            return None
        try:
            # 去掉 JSONP 前缀 v_sh600519="..." 
            idx = raw.find('"')
            if idx < 0:
                return None
            raw = raw[idx + 1:].rstrip('";\n\r ').strip()
            parts = raw.split('~')
            if len(parts) < 40:
                return None

            def _f(i: int) -> float:
                try:
                    return float(parts[i]) if parts[i] else 0.0
                except (ValueError, IndexError):
                    return 0.0

            current = _f(3)
            last_close = _f(4)
            open_price = _f(5)
            high = _f(33)
            low = _f(34)
            volume = _f(36)  # 手
            turnover = _f(37)  # 万元
            percent = _f(32)
            chg = _f(31)
            name = parts[1] if len(parts) > 1 else ''

            # 修复：涨跌额/涨幅为0但当前价≠昨收时反推
            if (chg == 0 or percent == 0) and last_close > 0 and current > 0 and abs(current - last_close) > 0.001:
                chg = round(current - last_close, 3)
                percent = round((current - last_close) / last_close * 100, 2)

            # 成交额：腾讯万元 → 元
            amount = round(turnover * 10000, 2) if turnover > 0 else 0.0

            # 分时均价：按市场规则计算
            avg_price = 0.0
            if volume > 0 and turnover > 0:
                market_lower = self._tencent_to_symbol(symbol).lower()
                if market_lower.startswith('hk'):
                    avg_price = round(turnover / volume, 3) if volume > 0 else 0
                elif market_lower.startswith(('sh68', 'bj')):
                    avg_price = round((turnover * 10000) / volume, 3)
                else:
                    avg_price = round((turnover * 10000) / (volume * 100), 3)

            # 构建雪球兼容 dict
            return {
                'symbol': symbol,
                'name': name,
                'current': current,
                'chg': chg,
                'percent': percent,
                'last_close': last_close,
                'open': open_price,
                'high': high,
                'low': low,
                'volume': volume,
                'amount': amount,
                'turnover_rate': _f(38),
                'amplitude': _f(43),
                'pe_ttm': _f(39),          # 腾讯是静态PE，雪球用TTM
                'pb': _f(46),
                'market_capital': _f(45),
                'float_market_capital': _f(44),
                'avg_price': avg_price if avg_price > 0 else current if current > 0 else 0,
                'high_52w': _f(57) or _f(49) or high,  # 优先52周最高，降级年最高/今日最高
                'low_52w': _f(58) or _f(50) or low,    # 优先52周最低，降级年最低/今日最低
                'data_source': 'tencent',
            }
        except Exception as e:
            print(f"[ERR] 解析腾讯行情失败: {symbol} - {e}")
            return None

    def get_stock_quote(self, symbol: str, use_cache: bool = True) -> Optional[dict]:
        """
        获取股票实时行情（腾讯 qt.gtimg.cn 接口，无认证/无频率限制）
        
        Args:
            symbol: 股票代码（如 SH600519，无前缀也能自动识别）
            use_cache: 是否使用缓存（默认 True，缓存5分钟）
            
        Returns:
            行情数据字典（与雪球格式兼容）
        """
        # 自动补全交易所前缀（无前缀时根据代码规则补全）
        if '.' not in symbol and not symbol.startswith(('SH', 'SZ', 'SH60', 'SZ00', 'BJ', 'HK')):
            if symbol.isdigit() and len(symbol) <= 5:
                symbol = 'HK' + symbol.zfill(5)
            elif symbol.isdigit() and len(symbol) == 6:
                symbol = ('SH' if symbol.startswith(('6', '9')) else 'SZ') + symbol
            else:
                symbol = 'SH' + symbol
        
        # 尝试从缓存读取
        if use_cache:
            cached = self._get_from_cache('stock_quotes', symbol)
            if cached:
                return cached
        
        try:
            import urllib.request
            import ssl

            # 转换为腾讯代码格式
            t_symbol = self._tencent_to_symbol(symbol)
            url = f'https://qt.gtimg.cn/q={t_symbol}'
            
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://finance.qq.com/',
            })
            
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                raw = resp.read().decode('gbk', errors='replace')
            
            if not raw or 'pv_none' in raw.lower():
                print(f"[WARN] 腾讯接口无数据: {symbol}")
                return None
            
            quote_data = self._parse_tencent_quote(symbol, raw)
            if quote_data is None:
                return None

            # 非交易时段 current 可能为 0，此时不缓存无效数据
            current = quote_data.get('current', 0)
            if current == 0 or current is None:
                print(f"[WARN] 跳过缓存 {symbol}：current={current}（非交易时段或API异常）")
                return quote_data

            # 保存到缓存
            self._save_to_cache('stock_quotes', symbol, quote_data)
            print(f"[OK] 腾讯行情: {symbol} @ {current}")
            return quote_data
            
        except Exception as e:
            print(f"[ERR] 腾讯行情获取失败: {symbol} - {e}")
            return None
    
    def get_stock_quotes(self, symbols: List[str]) -> Dict[str, dict]:
        """
        批量获取股票行情
        
        Args:
            symbols: 股票代码列表
            
        Returns:
            行情数据字典
        """
        results = {}
        for symbol in symbols:
            result = self.get_stock_quote(symbol)
            if result:
                results[symbol] = result
        return results
    
    def get_financial_indicator(self, symbol: str) -> Optional[dict]:
        """
        获取财务指标
        
        Args:
            symbol: 股票代码
            
        Returns:
            财务指标数据
        """
        try:
            result = ball.indicator(symbol)
            print(f"[OK] 获取财务指标：{symbol}")
            return result
        except Exception as e:
            print(f"[ERR] 获取财务指标失败：{e}")
            return None
    
    def get_cash_flow(self, symbol: str) -> Optional[dict]:
        """
        获取现金流量表
        
        Args:
            symbol: 股票代码
            
        Returns:
            现金流数据
        """
        try:
            result = ball.capital_flow(symbol)
            print(f"[OK] 获取现金流：{symbol}")
            return result
        except Exception as e:
            print(f"[ERR] 获取现金流失败：{e}")
            return None
    
    def get_capital_history(self, symbol: str) -> Optional[dict]:
        """
        获取资金流向历史
        
        Args:
            symbol: 股票代码
            
        Returns:
            资金流向数据
        """
        try:
            result = ball.capital_history(symbol)
            print(f"[OK] 获取资金流向：{symbol}")
            return result
        except Exception as e:
            print(f"[ERR] 获取资金流向失败：{e}")
            return None
    
    # ========== 组合相关 ==========
    
    def get_cube_info(self, symbol: str) -> Optional[dict]:
        """
        获取组合信息
        
        Args:
            symbol: 组合代码（如 ZH811111）
            
        Returns:
            组合信息
        """
        try:
            # 获取净值数据
            nav = ball.cube.nav_daily(symbol)
            
            # 获取行情数据
            quote = ball.cube.quote_current(symbol)
            
            result = {
                'symbol': symbol,
                'nav': nav,
                'quote': quote,
                'updated_at': datetime.now().isoformat()
            }
            
            # 保存到缓存
            self._save_to_cache('cube_data', symbol, result)
            
            print(f"[OK] 获取组合信息：{symbol}")
            return result
        except Exception as e:
            print(f"[ERR] 获取组合信息失败：{e}")
            return None
    
    def get_cube_holdings(self, symbol: str) -> Optional[List[dict]]:
        """
        获取组合当前持仓
        
        Args:
            symbol: 组合代码
            
        Returns:
            持仓列表
        """
        try:
            result = ball.cube.rebalancing_current(symbol)
            
            holdings = []
            if 'last_rb' in result and 'holdings' in result['last_rb']:
                holdings = result['last_rb']['holdings']
            
            print(f"[OK] 获取组合持仓：{symbol}, 共{len(holdings)}只股票")
            return holdings
        except Exception as e:
            print(f"[ERR] 获取组合持仓失败：{e}")
            return None
    
    def get_cube_history(self, symbol: str, count: int = 20) -> Optional[List[dict]]:
        """
        获取组合调仓历史
        
        Args:
            symbol: 组合代码
            count: 返回数量
            
        Returns:
            调仓历史记录
        """
        try:
            result = ball.cube.rebalancing_history(symbol)
            
            history = []
            if 'list' in result:
                history = result['list'][:count]
            
            print(f"[OK] 获取组合调仓历史：{symbol}, 共{len(history)}条记录")
            return history
        except Exception as e:
            print(f"[ERR] 获取调仓历史失败：{e}")
            return None
    
    # ========== 基金相关 ==========
    
    def get_fund_info(self, code: str) -> Optional[dict]:
        """
        获取基金信息
        
        Args:
            code: 基金代码
            
        Returns:
            基金信息
        """
        try:
            result = ball.fund_info(code)
            print(f"[OK] 获取基金信息：{code}")
            return result
        except Exception as e:
            print(f"[ERR] 获取基金信息失败：{e}")
            return None
    
    def get_fund_nav_history(self, code: str, page: int = 1, size: int = 100) -> Optional[dict]:
        """
        获取基金净值历史
        
        Args:
            code: 基金代码
            page: 页码
            size: 每页数量
            
        Returns:
            净值历史数据
        """
        try:
            result = ball.fund_nav_history(code, page, size)
            print(f"[OK] 获取基金净值历史：{code}")
            return result
        except Exception as e:
            print(f"[ERR] 获取基金净值失败：{e}")
            return None
    
    def get_fund_asset(self, code: str) -> Optional[dict]:
        """
        获取基金持仓

        Args:
            code: 基金代码

        Returns:
            基金持仓数据
        """
        try:
            result = ball.fund_asset(code)
            print(f"[OK] 获取基金持仓：{code}")
            return result
        except Exception as e:
            print(f"[ERR] 获取基金持仓失败：{e}")
            return None

    # ========== ETF 相关 ==========

    def get_etf_detail(self, symbol: str, use_cache: bool = True) -> Optional[dict]:
        """
        获取 ETF 详细信息

        Args:
            symbol: ETF 代码（如 SZ159530，自动识别前缀）
            use_cache: 是否使用缓存

        Returns:
            详细信息字典，包含：
            - name: 基金名称
            - nav_date: 净值日期
            - unit_nav: 单位净值
            - iopv: 实时估值
            - premium_rate: 溢价率
            - found_date: 成立日期
            - issue_date: 上市日期
            - sub_type: 交易类型
            - 规模、成交量、换手率等
        """
        # 自动补全交易所前缀
        # ETF: 5开头→SH(上交所), 1/0/3/2开头→SZ(深交所)
        if '.' not in symbol and not symbol.startswith(('SH', 'SZ')):
            symbol = ('SH' if symbol.startswith(('5',)) else 'SZ') + symbol

        # 尝试从缓存读取
        if use_cache:
            cached = self._get_from_cache('etf_detail', symbol)
            if cached:
                print(f"[OK] ETF详情从缓存读取：{symbol}")
                return cached

        try:
            import requests
            cookie = self._get_cookie()

            url = 'https://stock.xueqiu.com/v5/stock/quote.json'
            params = {'symbol': symbol, 'extend': 'detail'}
            headers = {
                'Cookie': cookie,
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code != 200:
                print(f"[ERR] ETF详情获取失败 HTTP {r.status_code}：{symbol}")
                return None

            result = r.json()
            if result.get('error_code') != 0:
                print(f"[ERR] ETF详情API错误 {result.get('error_code')}: {result.get('error_description', '')}")
                return None

            data = result.get('data', {})
            if not data:
                print(f"[WARN] 无ETF详情数据：{symbol}")
                return None

            # 提取 quote 部分
            quote = data.get('quote', {})

            # 提取 market 部分（包含市场状态）
            market = data.get('market', {})

            # 提取 others 部分（扩展数据）
            others = data.get('others', {})

            # 合并所有数据
            detail = {**market, **quote, **others}
            detail['symbol'] = symbol

            # 保存到缓存
            self._save_to_cache('etf_detail', symbol, detail)
            print(f"[OK] 获取ETF详情：{symbol} - {detail.get('name', 'N/A')}")

            return detail

        except Exception as e:
            print(f"[ERR] 获取ETF详情异常：{symbol} - {e}")
            return None

    def get_etf_quote(self, symbol: str, use_cache: bool = True) -> Optional[dict]:
        """
        获取 ETF 实时行情

        Args:
            symbol: ETF 代码（如 SH512480，无前缀也能自动识别）
            use_cache: 是否使用缓存

        Returns:
            行情数据字典，包含额外字段 turnover_rate_est（估算换手率）
        """
        # 自动补全交易所前缀
        # ETF: 5开头→SH(上交所), 1/0/3/2开头→SZ(深交所)
        if '.' not in symbol and not symbol.startswith(('SH', 'SZ')):
            symbol = ('SH' if symbol.startswith(('5',)) else 'SZ') + symbol

        # 尝试从 ETF 缓存读取
        if use_cache:
            cached = self._get_from_cache('etf_quotes', symbol)
            if cached:
                print(f"[OK] ETF 从缓存读取：{symbol}")
                return cached

        try:
            # 复用股票行情接口（雪球对 ETF 和股票用同一 API）
            quote_data = self.get_stock_quote(symbol, use_cache=False)

            if quote_data:
                # ETF 额外字段：估算换手率 = 成交额 / 市场规模 * 100
                amount = quote_data.get('amount') or 0
                market_cap = quote_data.get('market_capital') or 0
                if amount and market_cap and market_cap > 0:
                    quote_data['turnover_rate_est'] = round(amount / market_cap * 100, 2)

                # 保存到 ETF 独立缓存表
                self._save_to_cache('etf_quotes', symbol, quote_data)
                print(f"[OK] 获取 ETF 行情：{symbol}")

            return quote_data
        except Exception as e:
            print(f"[ERR] 获取 ETF 行情失败：{symbol} - {e}")
            return None

    def batch_get_etf_quotes(self, symbols: List[str]) -> Dict[str, dict]:
        """
        批量获取 ETF 行情

        Args:
            symbols: ETF 代码列表

        Returns:
            行情数据字典
        """
        results = {}
        for symbol in symbols:
            result = self.get_etf_quote(symbol)
            if result:
                results[symbol] = result
        return results

    def get_etf_kline(self, symbol: str, period: str = "day", count: int = -284,
                      begin: Optional[int] = None, use_cache: bool = False) -> Optional[List[dict]]:
        """
        获取 ETF K线数据

        Args:
            symbol: ETF 代码（如 SZ159530、SH512480，自动识别前缀）
            period: K线周期 (day/week/month/minute/5minute/15minute/30minute/60minute)
            count: 数据条数，负数表示取起点之前的历史数据，默认-284表示取约一年日线
            begin: 起始时间戳（毫秒），默认None使用当前时间
            use_cache: 是否使用缓存（默认False，K线数据通常实时性要求高）

        Returns:
            K线数据列表，每条包含 timestamp/open/high/low/close/volume/amount 等
        """
        # 自动补全交易所前缀
        # ETF: 5开头→SH(上交所), 1/0/3/2开头→SZ(深交所)
        if '.' not in symbol and not symbol.startswith(('SH', 'SZ')):
            symbol = ('SH' if symbol.startswith(('5',)) else 'SZ') + symbol

        # 构造 Cookie
        cookie = self._get_cookie()

        # 默认使用明天的时间戳（雪球需要未来的begin才能取到最近数据）
        if begin is None:
            from datetime import timedelta
            tomorrow = datetime.now() + timedelta(days=1)
            begin = int(tomorrow.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

        # 负数 count 保持原样，正数 count 转为负数（雪球API需要负数表示往前取）
        actual_count = -count if count > 0 else count

        try:
            import requests
            url = "https://stock.xueqiu.com/v5/stock/chart/kline.json"
            params = {
                "symbol": symbol,
                "begin": begin,
                "period": period,
                "type": "before",
                "count": actual_count,
                "indicator": "kline"
            }
            headers = {
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            print(f"DEBUG K线: symbol={symbol}, begin={begin}, period={period}, count={actual_count}, type=before")
            r = requests.get(url, headers=headers, params=params, timeout=15)
            print(f"DEBUG K线响应: status={r.status_code}, body={r.text[:500]}")
            if r.status_code != 200:
                print(f"[ERR] ETF K线获取失败 HTTP {r.status_code}：{symbol}")
                return None

            result = r.json()
            if result.get('error_code') != 0:
                print(f"[ERR] ETF K线API错误 {result.get('error_code')}: {result.get('error_description', '')}")
                return None

            data = result.get('data', {})
            if not data or 'item' not in data or not data['item']:
                print(f"[WARN] 无K线数据：{symbol}")
                return None

            columns = data.get('column', [])
            items = data['item']

            # 转换为列表字典
            klines = []
            for item in items:
                record = dict(zip(columns, item))
                klines.append(record)

            # 保存到缓存
            self._save_to_cache('etf_kline', symbol, {
                'period': period,
                'count': count,
                'begin': begin,
                'klines': klines
            })

            print(f"[OK] 获取ETF K线：{symbol} {period} {len(klines)}条")
            return klines

        except Exception as e:
            print(f"[ERR] 获取ETF K线异常：{symbol} - {e}")
            return None

    def sync_etf_pool_from_api(self, page: int = 1, size: int = 30, order: str = "desc",
                               order_by: str = "percent", fund_type: int = 18,
                               parent_type: int = 1) -> Optional[List[dict]]:
        """
        从雪球API同步ETF板块池数据

        Args:
            page: 页码
            size: 每页数量
            order: 排序方向 (desc/asc)
            order_by: 排序字段 (percent/amount/market_capital)
            fund_type: 基金类型 (18=ETF)
            parent_type: 母类型 (1=A股)

        Returns:
            ETF列表数据
        """
        try:
            import requests
            cookie = self._get_cookie()

            url = "https://stock.xueqiu.com/v5/stock/screener/fund/list.json"
            params = {
                "page": page,
                "size": size,
                "order": order,
                "order_by": order_by,
                "type": fund_type,
                "parent_type": parent_type
            }
            headers = {
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                print(f"[ERR] ETF板块池API失败 HTTP {r.status_code}")
                return None

            result = r.json()
            if result.get('error_code') != 0:
                print(f"[ERR] ETF板块池API错误 {result.get('error_code')}: {result.get('error_description', '')}")
                return None

            data = result.get('data', {})
            items = data.get('list', [])
            total = data.get('count', 0)

            print(f"[OK] 获取ETF板块池第{page}页，共{total}只")
            return items

        except Exception as e:
            print(f"[ERR] 获取ETF板块池异常：{e}")
            return None

    def sync_etf_pool(self, pages: int = 5, size: int = 30) -> int:
        """
        同步ETF板块池到数据库

        Args:
            pages: 同步前几页（默认5页=150只）
            size: 每页数量

        Returns:
            同步数量
        """
        total_count = 0
        for page in range(1, pages + 1):
            items = self.sync_etf_pool_from_api(page=page, size=size)
            if not items:
                continue

            for item in items:
                symbol = item.get('symbol', '')
                if not symbol:
                    continue

                # 保存到数据库
                self._save_etf_pool_item(symbol, item)
                total_count += 1

        print(f"[OK] ETF板块池同步完成，共{total_count}只")
        return total_count

    def _save_etf_pool_item(self, symbol: str, data: dict):
        """保存ETF到板块池"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()

            cursor.execute('''
                INSERT OR REPLACE INTO etf_pool (symbol, name, sector, catalyst_type, priority, data, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                data.get('name', ''),
                data.get('sector', ''),
                data.get('catalyst_type', ''),
                data.get('priority', 3),
                json.dumps(data, ensure_ascii=False),
                datetime.now().isoformat()
            ))

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[WARN] 保存ETF池失败：{symbol} - {e}")

    def get_etf_pool_from_db(self, sector: Optional[str] = None, limit: int = 100) -> List[dict]:
        """
        从数据库获取ETF板块池

        Args:
            sector: 板块筛选（可选）
            limit: 返回数量限制

        Returns:
            ETF列表
        """
        try:
            conn = sqlite3.connect(self.db_file)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if sector:
                cursor.execute('SELECT * FROM etf_pool WHERE sector = ? ORDER BY priority LIMIT ?',
                             (sector, limit))
            else:
                cursor.execute('SELECT * FROM etf_pool ORDER BY priority LIMIT ?', (limit,))

            rows = cursor.fetchall()
            conn.close()

            result = []
            for row in rows:
                item = {
                    'symbol': row['symbol'],
                    'name': row['name'],
                    'sector': row['sector'],
                    'catalyst_type': row['catalyst_type'],
                    'priority': row['priority'],
                    'data': json.loads(row['data']) if row['data'] else {}
                }
                result.append(item)

            return result

        except Exception as e:
            print(f"[ERR] 获取ETF池失败：{e}")
            return []

    # ========== 缓存管理 ==========
    
    def _get_from_cache(self, table: str, symbol: str) -> Optional[Any]:
        """从缓存读取数据"""
        try:
            conn = sqlite3.connect(self.db_file)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute(f'SELECT data, updated_at FROM {table} WHERE symbol = ?', (symbol,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                # 检查缓存是否过期（5 分钟）
                updated = datetime.fromisoformat(row['updated_at'])
                if (datetime.now() - updated).total_seconds() < 300:
                    return json.loads(row['data'])
        except Exception as e:
            pass
        
        return None
    
    def _save_to_cache(self, table: str, symbol: str, data: Any):
        """保存数据到缓存"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # 使用 REPLACE INTO 实现 upsert
            cursor.execute(f'''
                INSERT OR REPLACE INTO {table} (symbol, data, updated_at)
                VALUES (?, ?, ?)
            ''', (symbol, json.dumps(data, ensure_ascii=False), datetime.now().isoformat()))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[WARN] 保存缓存失败：{e}")
    
    def clear_cache(self):
        """清空缓存"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM stock_quotes')
            cursor.execute('DELETE FROM cube_data')
            cursor.execute('DELETE FROM financial_data')
            conn.commit()
            conn.close()
            print("[OK] 缓存已清空")
        except Exception as e:
            print(f"[ERR] 清空缓存失败：{e}")
    
    # ========== 数据导出 ==========
    
    def export_to_csv(self, data: List[dict], filename: str):
        """
        导出数据到 CSV
        
        Args:
            data: 数据列表
            filename: 文件名
        """
        import csv
        
        if not data:
            print("[WARN] 没有数据可导出")
            return
        
        filepath = os.path.join(self.data_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        
        print(f"[OK] 数据已导出：{filepath}")
    
    def export_to_json(self, data: Any, filename: str):
        """
        导出数据到 JSON
        
        Args:
            data: 数据
            filename: 文件名
        """
        filepath = os.path.join(self.data_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"[OK] 数据已导出：{filepath}")


def main():
    """测试引擎"""
    print("=" * 60)
    print("雪球数据查询引擎测试")
    print("=" * 60)
    
    # 创建引擎
    engine = XueqiuEngine()
    
    # 测试股票行情
    print("\n[1] 测试股票行情...")
    quote = engine.get_stock_quote("SH600519")
    if quote:
        print(f"贵州茅台当前价：{quote.get('current', 'N/A')}")
    
    # 测试组合数据
    print("\n[2] 测试组合数据...")
    cube = engine.get_cube_info("ZH811111")
    if cube and cube.get('quote'):
        q = cube['quote']
        print(f"组合 ZH811111 净值：{q.get('net_value', 'N/A')}")
    
    # 测试组合持仓
    print("\n[3] 测试组合持仓...")
    holdings = engine.get_cube_holdings("ZH811111")
    if holdings:
        print(f"持仓数量：{len(holdings)}")
        for h in holdings[:5]:
            print(f"  - {h.get('stock_name', 'N/A')}: {h.get('weight', 0)}%")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
