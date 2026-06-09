# -*- coding: utf-8 -*-
"""
东方财富实时板块资金流向 API (push2.eastmoney.com)

数据源: 东方财富 push2 接口，实时更新
用途: 盘中板块资金流向监控、盘前/盘中扫描

接口说明:
- 行业板块: fs=m:90+t:3
- 概念板块: fs=m:90+t:2
- 地域板块: fs=m:90+t:1

字段映射 (经实测验证):
┌────────┬─────────────────────────────┬──────────┐
│  字段  │           含义              │   单位   │
├────────┼─────────────────────────────┼──────────┤
│ f12    │ 板块代码                     │   -      │
│ f14    │ 板块名称                     │   -      │
│ f2     │ 最新价                       │   -      │
│ f3     │ 涨跌幅                       │   %      │
│ f62    │ 主力净流入 (超大单+大单)      │   元     │
│ f184   │ 主力净流入占比               │   %      │
│ f66    │ 超大单净流入 (NET)           │   元     │
│ f69    │ 超大单净流入占比             │   %      │
│ f72    │ 大单净流入 (NET)             │   元     │
│ f75    │ 大单净流入占比               │   %      │
│ f78    │ 中单净流入 (NET)             │   元     │
│ f81    │ 中单净流入占比               │   %      │
│ f84    │ 小单净流入 (NET)             │   元     │
│ f87    │ 小单净流入占比               │   %      │
│ f104   │ 上涨家数                     │   家     │
│ f105   │ 下跌家数                     │   家     │
│ f128   │ 领涨股名称                   │   -      │
│ f140   │ 领涨股代码                   │   -      │
│ f124   │ 更新时间戳                   │  unix    │
└────────┴─────────────────────────────┴──────────┘

注意: f66/f69/f72/f75/f78/f81/f84/f87 全部是 NET 净流入值（非买入/卖出分别）
      f62 = f66 + f72 (主力 = 超大单 + 大单)
      f66 + f72 + f78 + f84 ≈ 0 (四类单净额合计归零)
"""

import json
import re
import time
import logging
import traceback
import random
import urllib.request
import urllib.error
import ssl
import http.client
from typing import Optional, Literal
from datetime import datetime

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════

EM_PUSH2_URL = "https://push2.eastmoney.com/api/qt/clist/get"

# 板块类型
SECTOR_TYPE_MAP = {
    "concept": "m:90+t:2",   # 概念板块
    "industry": "m:90+t:3",  # 行业板块
    "region": "m:90+t:1",    # 地域板块
}

# 请求字段 (经实测验证的正确映射)
DEFAULT_FIELDS = (
    "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,"
    "f104,f105,f128,f140,f124"
)

# 缓存 (避免频繁请求)
_cache: dict = {}
_CACHE_TTL = 30  # 缓存 30 秒（实时接口，不宜过长）


# ═══════════════════════════════════════════════════════
# 核心获取函数
# ═══════════════════════════════════════════════════════

def _fetch_raw(sector_type: str = "concept", sort_field: str = "f62",
               sort_order: int = 1, page: int = 1, page_size: int = 50,
               fields: str = DEFAULT_FIELDS, timeout: int = 10) -> list[dict]:
    """
    调用东财 push2 接口获取板块资金流向原始数据。

    Args:
        sector_type: 板块类型 - "concept"(概念) / "industry"(行业) / "region"(地域)
        sort_field:  排序字段，默认 f62(主力净流入)
        sort_order:  0=升序, 1=降序
        page:        页码
        page_size:   每页条数 (最大 200)
        fields:      请求字段
        timeout:     超时秒数

    Returns:
        list[dict]: 原始数据列表
    """
    fs = SECTOR_TYPE_MAP.get(sector_type, SECTOR_TYPE_MAP["concept"])

    params = {
        "fid": sort_field,
        "po": str(sort_order),
        "pz": str(min(page_size, 200)),
        "pn": str(page),
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "ut": "8dec03ba335b81bf4ebdf7b29ec27d15",
        "fs": fs,
        "fields": fields,
    }

    # 构建 URL
    query_parts = [f"{k}={v}" for k, v in params.items()]
    url = EM_PUSH2_URL + "?" + "&".join(query_parts)

    # 创建 SSL context（忽略证书，兼容代理环境）
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
            "Referer": "https://data.eastmoney.com/bkzj/hy.html",
            "Connection": "close",  # 禁止 keep-alive，避免云服务器代理断开连接
        }
    )

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, (http.client.RemoteDisconnected, ConnectionResetError)):
            logger.warning(f"[em_sector_flow] 连接被远端关闭: {reason}")
        elif isinstance(reason, TimeoutError):
            logger.warning(f"[em_sector_flow] 请求超时: {reason}")
        else:
            logger.warning(f"[em_sector_flow] 请求失败 (URLError): {e}")
        return []
    except (http.client.RemoteDisconnected, ConnectionResetError) as e:
        logger.warning(f"[em_sector_flow] 连接被远端关闭 (底层异常): {e}")
        return []
    except Exception as e:
        logger.warning(f"[em_sector_flow] 异常: {e}\n{traceback.format_exc()}")
        return []

    # 解析 JSONP 或 JSON
    data = _parse_response(raw)
    if data is None:
        return []

    items = data.get("data", {}).get("diff", [])
    if not items:
        return []

    return items


def _parse_response(raw: str) -> Optional[dict]:
    """解析东财 push2 响应（兼容 JSONP 和纯 JSON）"""
    raw = raw.strip()
    # JSONP 格式: jQueryxxx({...});
    if raw.startswith("jQuery") or raw.startswith("callback"):
        match = re.search(r'\((.*)\)', raw, re.DOTALL)
        if match:
            raw = match.group(1).rstrip(";").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[em_sector_flow] JSON 解析失败: {raw[:200]}")
        return None


# ═══════════════════════════════════════════════════════
# 数据转换
# ═══════════════════════════════════════════════════════

def _safe_float(val, default=0.0) -> float:
    """安全转换为 float，处理 None/空字符串/'-'"""
    if val is None or val == "-" or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0) -> int:
    """安全转换为 int"""
    if val is None or val == "-" or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _yuan_to_wan(yuan: float) -> float:
    """元 → 万元"""
    return yuan / 10000.0


def _format_amount_wan(wan: float) -> str:
    """万元 → 人类可读字符串（亿/万）"""
    if abs(wan) >= 10000:
        return f"{wan / 10000:+.2f}亿"
    return f"{wan:+.0f}万"


def _normalize_item(raw: dict) -> dict:
    """
    将东财原始字段转换为标准化的板块资金流数据。

    所有金额字段统一为「万元」。
    所有占比字段保留为百分比(%)。

    Returns:
        dict with fields:
        - code: 板块代码
        - name: 板块名称
        - price: 最新价
        - pct_change: 涨跌幅(%)
        - main_net: 主力净流入(万元) = 超大单 + 大单
        - main_net_rate: 主力净流入占比(%)
        - main_net_fmt: 主力净流入(格式化)
        - super_large_net: 超大单净流入(万元)
        - super_large_net_rate: 超大单净流入占比(%)
        - large_net: 大单净流入(万元)
        - large_net_rate: 大单净流入占比(%)
        - medium_net: 中单净流入(万元)
        - medium_net_rate: 中单净流入占比(%)
        - small_net: 小单净流入(万元)
        - small_net_rate: 小单净流入占比(%)
        - advancing: 上涨家数
        - declining: 下跌家数
        - lead_stock_name: 领涨股名称
        - lead_stock_code: 领涨股代码
    """
    # ── 金额字段（原始单位: 元，转换为 万元）──
    main_net_yuan = _safe_float(raw.get("f62"))          # 主力净流入(元)
    super_large_net_yuan = _safe_float(raw.get("f66"))   # 超大单净流入(元) - NET
    large_net_yuan = _safe_float(raw.get("f72"))         # 大单净流入(元) - NET
    medium_net_yuan = _safe_float(raw.get("f78"))        # 中单净流入(元) - NET
    small_net_yuan = _safe_float(raw.get("f84"))         # 小单净流入(元) - NET

    # ── 占比字段（原始单位: %）──
    main_net_rate = _safe_float(raw.get("f184"))         # 主力净流入占比(%)
    super_large_rate = _safe_float(raw.get("f69"))       # 超大单净流入占比(%)
    large_rate = _safe_float(raw.get("f75"))             # 大单净流入占比(%)
    medium_rate = _safe_float(raw.get("f81"))            # 中单净流入占比(%)
    small_rate = _safe_float(raw.get("f87"))             # 小单净流入占比(%)

    # ── 广度字段 ──
    advancing = _safe_int(raw.get("f104"))               # 上涨家数
    declining = _safe_int(raw.get("f105"))               # 下跌家数

    # ── 领涨股 ──
    lead_name = raw.get("f128", "")                      # 领涨股名称
    lead_code = raw.get("f140", "")                      # 领涨股代码

    main_net_wan = _yuan_to_wan(main_net_yuan)

    return {
        "code": raw.get("f12", ""),
        "name": raw.get("f14", ""),
        "price": _safe_float(raw.get("f2")),
        "pct_change": round(_safe_float(raw.get("f3")), 2),
        # 主力
        "main_net": round(main_net_wan, 2),
        "main_net_rate": round(main_net_rate, 2),
        "main_net_fmt": _format_amount_wan(main_net_wan),
        # 超大单
        "super_large_net": round(_yuan_to_wan(super_large_net_yuan), 2),
        "super_large_net_rate": round(super_large_rate, 2),
        # 大单
        "large_net": round(_yuan_to_wan(large_net_yuan), 2),
        "large_net_rate": round(large_rate, 2),
        # 中单
        "medium_net": round(_yuan_to_wan(medium_net_yuan), 2),
        "medium_net_rate": round(medium_rate, 2),
        # 小单
        "small_net": round(_yuan_to_wan(small_net_yuan), 2),
        "small_net_rate": round(small_rate, 2),
        # 广度
        "advancing": advancing,
        "declining": declining,
        "total_stocks": advancing + declining,
        # 领涨股
        "lead_stock_name": lead_name,
        "lead_stock_code": lead_code,
    }


# ═══════════════════════════════════════════════════════
# 公开接口
# ═══════════════════════════════════════════════════════

def get_sector_flow(
    sector_type: Literal["concept", "industry", "region"] = "concept",
    sort_by: str = "main_net",
    top_n: int = 50,
    use_cache: bool = True,
) -> list[dict]:
    """
    获取板块资金流向排名（实时）。

    Args:
        sector_type: "concept"(概念) / "industry"(行业) / "region"(地域)
        sort_by:    排序字段 - "main_net"(主力净流入) / "pct_change"(涨跌幅) / "main_net_rate"(主力占比)
        top_n:      返回前 N 条
        use_cache:  是否使用缓存（默认 30 秒 TTL）

    Returns:
        list[dict]: 标准化板块资金流数据，按 sort_by 降序排列

    Example:
        >>> flows = get_sector_flow("concept", sort_by="main_net", top_n=10)
        >>> for s in flows:
        ...     print(f"{s['name']}: 主力净流入 {s['main_net_fmt']}, 涨跌幅 {s['pct_change']:+.2f}%")
    """
    # 检查缓存
    cache_key = f"{sector_type}_{sort_by}_{top_n}"
    if use_cache and cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if time.time() - cached_time < _CACHE_TTL:
            return cached_data

    # 映射排序字段
    sort_field_map = {
        "main_net": "f62",
        "pct_change": "f3",
        "main_net_rate": "f184",
    }
    sort_field = sort_field_map.get(sort_by, "f62")

    # 请求 2 页确保有足够数据（每页最多 200）
    all_items = []
    for page in range(1, 3):
        items = _fetch_raw(
            sector_type=sector_type,
            sort_field=sort_field,
            sort_order=1,  # 降序
            page=page,
            page_size=min(top_n * 2, 200),
        )
        if not items and page == 1:
            # 首页失败 → 重试一次
            time.sleep(1)
            logger.info(f"[em_sector_flow] 首页失败，重试...")
            items = _fetch_raw(
                sector_type=sector_type,
                sort_field=sort_field,
                sort_order=1,
                page=page,
                page_size=min(top_n * 2, 200),
            )
        if not items:
            break
        all_items.extend(items)
        if len(items) < min(top_n * 2, 200):
            break

    # 去重 + 标准化
    seen = set()
    result = []
    for raw in all_items:
        item = _normalize_item(raw)
        code = item["code"]
        if code in seen:
            continue
        seen.add(code)
        result.append(item)

    # 按指定字段排序（多页合并后需重排）
    result.sort(key=lambda x: x.get(sort_by, 0), reverse=True)
    result = result[:top_n]

    # 更新缓存
    _cache[cache_key] = (time.time(), result)

    return result


def get_top_inflow_sectors(
    sector_type: str = "concept",
    top_n: int = 10,
    use_cache: bool = True,
) -> list[dict]:
    """
    获取主力资金净流入最多的板块（简化接口）。

    按主力净流入降序排列，通常用于发现当日最强板块。
    """
    return get_sector_flow(
        sector_type=sector_type,
        sort_by="main_net",
        top_n=top_n,
        use_cache=use_cache,
    )


def get_top_change_sectors(
    sector_type: str = "concept",
    top_n: int = 10,
    use_cache: bool = True,
) -> list[dict]:
    """
    获取涨幅最大的板块（与现有 dc_daily 逻辑对齐）。

    按涨跌幅降序排列，返回包含资金流数据的完整信息。
    """
    return get_sector_flow(
        sector_type=sector_type,
        sort_by="pct_change",
        top_n=top_n,
        use_cache=use_cache,
    )


def get_sector_flow_by_name(
    name: str,
    sector_type: str = "concept",
    use_cache: bool = True,
) -> Optional[dict]:
    """
    按板块名称查询单个板块的资金流向。

    Args:
        name: 板块名称（模糊匹配，如 "半导体" 可匹配 "半导体概念"）
        sector_type: 板块类型

    Returns:
        dict or None: 板块资金流数据
    """
    all_sectors = get_sector_flow(
        sector_type=sector_type,
        top_n=200,
        use_cache=use_cache,
    )
    for s in all_sectors:
        if name in s["name"] or s["name"] in name:
            return s
    return None


def get_market_sector_summary(top_n: int = 5) -> dict:
    """
    获取市场板块资金流综合摘要（概念 + 行业双维度）。

    Returns:
        {
            "top_inflow_concepts": [...],   # 主力净流入 Top N 概念
            "top_inflow_industries": [...],  # 主力净流入 Top N 行业
            "top_change_concepts": [...],    # 涨幅 Top N 概念
            "top_change_industries": [...],  # 涨幅 Top N 行业
            "updated_at": "2026-06-09T10:00:00",
        }
    """
    inflow_concepts = get_top_inflow_sectors("concept", top_n)
    inflow_industries = get_top_inflow_sectors("industry", top_n)
    change_concepts = get_top_change_sectors("concept", top_n)
    change_industries = get_top_change_sectors("industry", top_n)

    return {
        "top_inflow_concepts": inflow_concepts,
        "top_inflow_industries": inflow_industries,
        "top_change_concepts": change_concepts,
        "top_change_industries": change_industries,
        "updated_at": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════
# 大盘实时资金流向（东财 push2 ulist.np）
# ═══════════════════════════════════════════════════════

EM_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"

# 市场指数 secid 映射
MARKET_SECIDS = {
    "sh": "1.000001",   # 上证指数
    "sz": "0.399001",   # 深证成指
}

# ulist.np 字段映射
MARKET_FIELDS = (
    "f6,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,"
    "f64,f65,f70,f71,f76,f77,f82,f83"  # buy/sell breakdown
)


def _fetch_market_raw(secids: str, timeout: int = 10) -> list[dict]:
    """调用东财 ulist.np 接口获取大盘资金流原始数据"""
    params = {
        "fltt": "2",
        "secids": secids,
        "fields": MARKET_FIELDS,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    query_parts = [f"{k}={v}" for k, v in params.items()]
    url = EM_ULIST_URL + "?" + "&".join(query_parts)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
            "Referer": "https://data.eastmoney.com/zjlx/dpzjlx.html",
            "Connection": "close",  # 禁止 keep-alive，避免云服务器代理断开连接
        }
    )

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        # 区分超时 vs 连接重置
        reason = e.reason
        if isinstance(reason, (http.client.RemoteDisconnected, ConnectionResetError)):
            logger.warning(f"[em_market_flow] 连接被远端关闭: {reason}")
        elif isinstance(reason, TimeoutError):
            logger.warning(f"[em_market_flow] 请求超时: {reason}")
        else:
            logger.warning(f"[em_market_flow] 请求失败 (URLError): {e}")
        return []
    except (http.client.RemoteDisconnected, ConnectionResetError) as e:
        logger.warning(f"[em_market_flow] 连接被远端关闭 (底层异常): {e}")
        return []
    except Exception as e:
        logger.warning(f"[em_market_flow] 请求失败 (未知异常): {e}\n{traceback.format_exc()}")
        return []

    data = _parse_response(raw)
    if data is None:
        return []
    return data.get("data", {}).get("diff", [])


def _normalize_market_item(raw: dict, market: str) -> dict:
    """
    标准化单市场资金流数据。
    所有金额字段: 原始 元 → 转换为 万元
    占比字段: 保持 百分比
    """
    total_amount_yuan = _safe_float(raw.get("f6"))        # 总成交额(元)

    # 净流入 (已经 NET)
    main_net_yuan = _safe_float(raw.get("f62"))           # 主力净流入
    super_large_net_yuan = _safe_float(raw.get("f66"))    # 超大单净流入
    large_net_yuan = _safe_float(raw.get("f72"))          # 大单净流入
    medium_net_yuan = _safe_float(raw.get("f78"))         # 中单净流入
    small_net_yuan = _safe_float(raw.get("f84"))          # 小单净流入

    # 买入/卖出 分明细 (原始 元)
    super_large_buy = _safe_float(raw.get("f64"))         # 超大单买入
    super_large_sell = _safe_float(raw.get("f65"))        # 超大单卖出
    large_buy = _safe_float(raw.get("f70"))               # 大单买入
    large_sell = _safe_float(raw.get("f71"))              # 大单卖出
    medium_buy = _safe_float(raw.get("f76"))              # 中单买入
    medium_sell = _safe_float(raw.get("f77"))             # 中单卖出
    small_buy = _safe_float(raw.get("f82"))               # 小单买入
    small_sell = _safe_float(raw.get("f83"))              # 小单卖出

    # 占比
    main_net_rate = _safe_float(raw.get("f184"))          # 主力净流入占比(%)
    super_large_rate = _safe_float(raw.get("f69"))        # 超大单占比(%)
    large_rate = _safe_float(raw.get("f75"))              # 大单占比(%)
    medium_rate = _safe_float(raw.get("f81"))             # 中单占比(%)
    small_rate = _safe_float(raw.get("f87"))              # 小单占比(%)

    return {
        "market": market,
        "total_amount": round(_yuan_to_wan(total_amount_yuan), 2),
        "total_amount_fmt": _format_amount_wan(_yuan_to_wan(total_amount_yuan)),
        # 净流入
        "main_net": round(_yuan_to_wan(main_net_yuan), 2),
        "main_net_fmt": _format_amount_wan(_yuan_to_wan(main_net_yuan)),
        "main_net_rate": round(main_net_rate, 2),
        "super_large_net": round(_yuan_to_wan(super_large_net_yuan), 2),
        "super_large_net_rate": round(super_large_rate, 2),
        "large_net": round(_yuan_to_wan(large_net_yuan), 2),
        "large_net_rate": round(large_rate, 2),
        "medium_net": round(_yuan_to_wan(medium_net_yuan), 2),
        "medium_net_rate": round(medium_rate, 2),
        "small_net": round(_yuan_to_wan(small_net_yuan), 2),
        "small_net_rate": round(small_rate, 2),
        # 买/卖分明细
        "super_large_buy": round(_yuan_to_wan(super_large_buy), 2),
        "super_large_sell": round(_yuan_to_wan(super_large_sell), 2),
        "large_buy": round(_yuan_to_wan(large_buy), 2),
        "large_sell": round(_yuan_to_wan(large_sell), 2),
        "medium_buy": round(_yuan_to_wan(medium_buy), 2),
        "medium_sell": round(_yuan_to_wan(medium_sell), 2),
        "small_buy": round(_yuan_to_wan(small_buy), 2),
        "small_sell": round(_yuan_to_wan(small_sell), 2),
    }


def get_market_moneyflow_realtime() -> Optional[dict]:
    """
    获取沪深两市实时大盘资金流向（东财 push2 ulist.np）。

    返回沪深各自明细 + 两市合计，数据实时更新。
    云服务器网络不稳定时会重试 2 次。

    Returns:
        {
            "sh": { 上证明细 },
            "sz": { 深证明细 },
            "combined": { 两市合计 },
            "flow_nature": "主力建仓/温和流入/平衡/温和流出/主力出货",
            "updated_at": "2026-06-09T10:17:34",
            "source": "em_push2_ulist_realtime",
        }
        或 None（接口不可用时）
    """
    # 同时请求沪深两市，带重试
    # 云服务器环境下东财 API 可能因代理/anti-bot 导致连接重置，
    # 使用较长的退避时间 + 随机抖动，减少被拦截概率
    secids = ",".join([MARKET_SECIDS["sh"], MARKET_SECIDS["sz"]])
    items = None
    for attempt in range(3):
        items = _fetch_market_raw(secids, timeout=8)
        if items and len(items) >= 2:
            break
        if attempt < 2:
            sleep_time = (2 if attempt == 0 else 5) + random.uniform(0, 2)  # 2-4s / 5-7s 退避 + 抖动
            logger.info(f"[em_market_flow] 等待 {sleep_time:.1f}s 后重试 {attempt + 2}/3...")
            time.sleep(sleep_time)

    if not items or len(items) < 2:
        logger.warning("[em_market_flow] 大盘资金流不可用（网络或接口异常），将降级到 Tushare")
        return None

    # 标准化
    sh_data = _normalize_market_item(items[0], "sh")
    sz_data = _normalize_market_item(items[1], "sz")

    # 合并两市
    def _combine(key: str):
        return round(sh_data.get(key, 0) + sz_data.get(key, 0), 2)

    combined = {
        "total_amount": _combine("total_amount"),
        "total_amount_fmt": _format_amount_wan(_combine("total_amount")),
        "main_net": _combine("main_net"),
        "main_net_fmt": _format_amount_wan(_combine("main_net")),
        "main_net_rate": 0,  # 需要根据总成交重新算
        "super_large_net": _combine("super_large_net"),
        "large_net": _combine("large_net"),
        "medium_net": _combine("medium_net"),
        "small_net": _combine("small_net"),
        "super_large_buy": _combine("super_large_buy"),
        "super_large_sell": _combine("super_large_sell"),
        "large_buy": _combine("large_buy"),
        "large_sell": _combine("large_sell"),
        "medium_buy": _combine("medium_buy"),
        "medium_sell": _combine("medium_sell"),
        "small_buy": _combine("small_buy"),
        "small_sell": _combine("small_sell"),
    }
    # 计算合并后的占比
    total = combined["total_amount"]
    if total > 0:
        combined["main_net_rate"] = round(combined["main_net"] / total * 100, 2)
        combined["super_large_net_rate"] = round(combined["super_large_net"] / total * 100, 2)
        combined["large_net_rate"] = round(combined["large_net"] / total * 100, 2)
        combined["medium_net_rate"] = round(combined["medium_net"] / total * 100, 2)
        combined["small_net_rate"] = round(combined["small_net"] / total * 100, 2)

    flow_nature = classify_flow_nature(combined["main_net"], combined.get("main_net_rate", 0))

    return {
        "sh": sh_data,
        "sz": sz_data,
        "combined": combined,
        "flow_nature": flow_nature,
        "updated_at": datetime.now().isoformat(),
        "source": "em_push2_ulist_realtime",
    }


# ═══════════════════════════════════════════════════════
# 资金性质分类（与 fund_flow 对齐）
# ═══════════════════════════════════════════════════════

def classify_flow_nature(main_net: float, main_net_rate: float) -> str:
    """
    根据主力净流入和占比判断板块资金性质。

    Args:
        main_net: 主力净流入(万元)
        main_net_rate: 主力净流入占比(%)

    Returns:
        "主力建仓" / "温和流入" / "平衡" / "温和流出" / "主力出货"
    """
    if main_net > 0 and main_net_rate > 10:
        return "主力建仓"
    elif main_net > 0:
        return "温和流入"
    elif main_net < 0 and main_net_rate < -8:
        return "主力出货"
    elif main_net < 0:
        return "温和流出"
    else:
        return "平衡"


# ═══════════════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("=" * 70)
    print("测试 1: 概念板块 - 主力净流入 Top 10")
    print("=" * 70)
    sectors = get_top_inflow_sectors("concept", top_n=10)
    for i, s in enumerate(sectors, 1):
        nature = classify_flow_nature(s["main_net"], s["main_net_rate"])
        print(f"  {i:2d}. {s['name']:<16s} "
              f"主力: {s['main_net_fmt']:<12s}({s['main_net_rate']:+.1f}%)  "
              f"涨跌: {s['pct_change']:>+6.2f}%  "
              f"性质: {nature:<6s}  "
              f"\u2191{s['advancing']}/\u2193{s['declining']}  "
              f"领涨: {s.get('lead_stock_name', '')}({s.get('lead_stock_code', '')})")

    print()
    print("=" * 70)
    print("测试 2: 行业板块 - 涨幅 Top 10")
    print("=" * 70)
    sectors = get_top_change_sectors("industry", top_n=10)
    for i, s in enumerate(sectors, 1):
        nature = classify_flow_nature(s["main_net"], s["main_net_rate"])
        print(f"  {i:2d}. {s['name']:<16s} "
              f"涨跌: {s['pct_change']:>+6.2f}%  "
              f"主力: {s['main_net_fmt']:<12s}({s['main_net_rate']:+.1f}%)  "
              f"性质: {nature}  "
              f"\u2191{s['advancing']}/\u2193{s['declining']}")

    print()
    print("=" * 70)
    print("测试 3: 按名称查询 '半导体'")
    print("=" * 70)
    result = get_sector_flow_by_name("半导体", "concept")
    if result:
        print(f"  名称: {result['name']}")
        print(f"  代码: {result['code']}")
        print(f"  涨跌幅: {result['pct_change']:+.2f}%")
        print(f"  主力净流入: {result['main_net_fmt']} ({result['main_net_rate']:+.2f}%)")
        print(f"  超大单: {result['super_large_net']:+.0f}万 ({result['super_large_net_rate']:+.2f}%)")
        print(f"  大单:   {result['large_net']:+.0f}万 ({result['large_net_rate']:+.2f}%)")
        print(f"  中单:   {result['medium_net']:+.0f}万 ({result['medium_net_rate']:+.2f}%)")
        print(f"  小单:   {result['small_net']:+.0f}万 ({result['small_net_rate']:+.2f}%)")
        print(f"  上涨{result['advancing']} / 下跌{result['declining']} / 共{result['total_stocks']}")
        print(f"  领涨: {result.get('lead_stock_name', '')}({result.get('lead_stock_code', '')})")
    else:
        print("  未找到")

    print()
    print("=" * 70)
    print("Test 4: Market summary")
    print("=" * 70)
    summary = get_market_sector_summary(top_n=5)
    print(f"  updated: {summary['updated_at']}")
    inflow = [(s['name'], s['main_net_fmt']) for s in summary['top_inflow_concepts']]
    print(f"  top inflow concepts: {inflow}")
    change = [(s['name'], s['pct_change']) for s in summary['top_change_industries']]
    print(f"  top change industries: {change}")

    print()
    print("All tests passed!")
