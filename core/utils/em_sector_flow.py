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
import random
import subprocess
from typing import Optional, Literal
from datetime import datetime

logger = logging.getLogger(__name__)


# ── 云服务器东财开关 ──
# curl 在云服务器上可以正常访问东财 push2 接口（HTTP 200, 0.28s），
# 说明不是 IP 封锁。如果 Python 仍然报 RemoteDisconnected，大概率是
# Python SSL 库的 TLS 指纹与系统 OpenSSL 不同导致，可临时设置此变量跳过。
# 设置环境变量 SKIP_EASTMONEY=true 可跳过东财，直接走 Tushare 降级源。
import os as _os
_SKIP_EASTMONEY = _os.getenv("SKIP_EASTMONEY", "").strip().lower() in ("1", "true", "yes", "on")
if _SKIP_EASTMONEY:
    logger.info("[em_sector_flow] SKIP_EASTMONEY=true，东财接口已禁用，将直接返回 None 走降级源")

# ── HTTP 客户端选择 ──
# 优先 requests（urllib3 的 TLS 行为更接近系统 OpenSSL），不可用时降级 urllib。
# 不使用 verify=False 和自定义 cipher——这些会导致 TLS 指纹异常，反被东财拒绝。
try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    _HAS_REQUESTS = False
    logger.info("[em_sector_flow] requests 不可用，使用 urllib")

# ── 精简请求头（去除 Connection: close，与 curl 行为一致） ──
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cookie": os.environ.get("EASTMONEY_COOKIE", 
        "qgqp_b_id=1cc3c89ff09003f14504d6ce2704f978; "
        "st_nvi=W6lpD9Ad7PhFwtvK87DTf930b; "
        "nid18=0669c78d6e75a0345b1571c451cbd4b4; "
        "nid18_create_time=1777289270410; "
        "gviem=K3qwW0bI41sVLDrtqtPBQ2d3c; "
        "gviem_create_time=1777289270410"),
}


# ═══════════════════════════════════════════════════════
# HTTP 统一请求（三重降级：requests → urllib → curl）
# ═══════════════════════════════════════════════════════

def _http_get(url: str, timeout: int = 10, referer: str = "") -> Optional[str]:
    """HTTP GET，依次尝试 curl_cffi / requests / urllib / curl。"""
    headers = dict(_BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer

    # ── 第一重：curl_cffi（TLS 指纹 + Cookie，绕过反爬）──
    try:
        from curl_cffi import requests as cffi_req
        resp = cffi_req.get(url, headers=headers, impersonate="chrome124", timeout=timeout)
        if resp.status_code == 200:
            logger.debug(f"[em] curl_cffi 成功 ({len(resp.text)}B)")
            return resp.text
    except Exception:
        pass

    # ── 第二重：requests（urllib3）──
    if _HAS_REQUESTS:
        try:
            resp = requests.get(url, timeout=timeout, headers=headers)
            resp.raise_for_status()
            logger.debug(f"[em] ✅ requests 成功 ({len(resp.text)}B)")
            return resp.text
        except requests.exceptions.RequestException:
            pass  # 静默失败，不打印日志，继续下一个

    # ── 第二重：urllib（stdlib）──
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            logger.debug(f"[em] ✅ urllib 成功 ({len(raw)}B)")
            return raw
    except Exception:
        pass

    # ── 第三重：curl（系统 OpenSSL，与手动 curl 测试一致）──
    try:
        timeout_int = int(timeout)
        cmd = [
            "curl", "-s", "--max-time", str(timeout_int),
            "--retry", "1", "--retry-delay", "2",
            "--noproxy", "*",  # 忽略父进程继承的代理环境变量
            "-H", f"User-Agent: {headers['User-Agent']}",
            "-H", f"Cookie: {headers['Cookie']}",
        ]
        if referer:
            cmd.extend(["-H", f"Referer: {referer}"])
        cmd.append(url)
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_int + 5,
        )
        if result.returncode == 0 and result.stdout.strip():
            snippet = result.stdout[:300].replace("\n", " ")
            logger.info(f"[em] ✅ curl 成功, {len(result.stdout)}B → {snippet}...")
            return result.stdout
        else:
            logger.warning(f"[em] curl 失败 (exit={result.returncode}): {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        logger.warning(f"[em] curl 超时 (>{timeout_int}s)")
    except FileNotFoundError:
        logger.error("[em] curl 未安装，所有 HTTP 通道均失败")
    except Exception as e:
        logger.warning(f"[em] curl 异常: {e}")

    # 全部失败
    logger.warning(f"[em] 所有 HTTP 通道均失败: requests/urllib/curl 都无法获取数据")
    return None

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

    raw = _http_get(url, timeout=timeout, referer="https://data.eastmoney.com/bkzj/hy.html")
    if raw is None:
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

def _try_sqlite_cache(sector_type: str, top_n: int, sort_by: str) -> Optional[list]:
    """从 PostgreSQL fund_flow_cache 读取概念板块资金流缓存（≤5分钟视为有效）"""
    try:
        import json, os
        from datetime import datetime
        db_url = os.environ.get("DATABASE_URL", os.environ["DATABASE_URL"])
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(
            "SELECT data_json, updated_at FROM fund_flow_cache WHERE data_type='concept' AND symbol='__index__'"
        )
        idx_row = cur.fetchone()
        if not idx_row:
            conn.close()
            return None
        age = (datetime.now() - idx_row[1]).total_seconds()
        if age > 300:
            conn.close()
            return None
        cur.execute(
            "SELECT symbol, data_json FROM fund_flow_cache WHERE data_type='concept' AND symbol!='__index__'"
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None
        items = [json.loads(r[1]) for r in rows]
        items.sort(key=lambda x: x.get(sort_by, 0), reverse=True)
        return items[:top_n]
    except Exception:
        return None


def _try_sqlite_market_cache() -> Optional[dict]:
    """从 PostgreSQL fund_flow_cache 读取大盘资金流缓存（≤5分钟视为有效）"""
    try:
        import json, os
        from datetime import datetime
        db_url = os.environ.get("DATABASE_URL", os.environ["DATABASE_URL"])
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(
            "SELECT data_json, updated_at FROM fund_flow_cache WHERE data_type='market' AND symbol=''"
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        age = (datetime.now() - row[1]).total_seconds()
        if age > 300:
            return None
        return json.loads(row[0])
    except Exception:
        return None


def _build_market_response(d: dict) -> dict:
    """将扁平缓存 dict 还原为 get_market_moneyflow_realtime() 格式"""
    combined = {
        "main_net": d.get("main_net", 0),
        "main_net_fmt": d.get("main_net_fmt", ""),
        "main_net_rate": d.get("main_net_rate", 0),
        "super_large_net": d.get("super_large_net", 0),
        "large_net": d.get("large_net", 0),
        "medium_net": d.get("medium_net", 0),
        "small_net": d.get("small_net", 0),
        "total_amount": d.get("total_amount", 0),
        "total_amount_fmt": d.get("total_amount_fmt", ""),
    }
    return {
        "sh": {}, "sz": {},
        "combined": combined,
        "flow_nature": d.get("flow_nature", "平衡"),
        "updated_at": datetime.now().isoformat(),
        "source": "cache(fund_flow_cache.db)",
    }


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
    if _SKIP_EASTMONEY:
        raise RuntimeError("SKIP_EASTMONEY: 东财接口已禁用，请走 Tushare 降级源")
    # ── 优先：SQLite 缓存（fund_flow_cache 定时任务落库） ──
    dbc = _try_sqlite_cache(sector_type, top_n, sort_by)
    if dbc:
        return dbc
    # 检查内存缓存
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
            # 首页失败 → 冷却后重试一次（避免触发东财频率限制）
            cooldown = 20 + random.uniform(0, 5)
            logger.info(f"[em_sector_flow] 首页失败，冷却 {cooldown:.0f}s 后重试...")
            time.sleep(cooldown)
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

    # ── 东财缓存回退：所有渠道（requests/urllib/curl）均失败 → 返回今日上一次缓存 ──
    if not all_items:
        try:
            from core.utils.eastmoney_cache import get_em_cache
            cache = get_em_cache()
            cached_val, meta = cache.load_with_fallback("sector_flow", subtype=sector_type)
            if meta["from_cache"] and cached_val:
                aged = cache.get_aged_minutes("sector_flow", subtype=sector_type)
                age_str = f"{aged:.0f}分钟前" if aged is not None else "未知时点"
                logger.warning(
                    f"[em_sector_flow] ⚠️ 东财 {sector_type} 接口不可达，"
                    f"返回今日缓存数据 ({age_str}，{meta['cached_at']})"
                )
                return cached_val
        except Exception:
            pass
        return []

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

    # 更新内存缓存
    _cache[cache_key] = (time.time(), result)
    
    # ── 东财缓存 → EMCache + PostgreSQL ──
    try:
        from core.utils.eastmoney_cache import get_em_cache
        get_em_cache().save("sector_flow", result, subtype=sector_type)
    except Exception:
        pass
    _save_sector_to_pg(result, sector_type)

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

    raw = _http_get(url, timeout=timeout, referer="https://data.eastmoney.com/zjlx/dpzjlx.html")
    if raw is None:
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
    if _SKIP_EASTMONEY:
        return None
    # ── 优先：SQLite 缓存 ──
    dbc = _try_sqlite_market_cache()
    if dbc:
        return _build_market_response(dbc)
    # 请求沪深两市，带冷却式重试（避免触发东财频率限制）
    secids = ",".join([MARKET_SECIDS["sh"], MARKET_SECIDS["sz"]])
    items = None
    for attempt in range(2):
        items = _fetch_market_raw(secids, timeout=8)
        if items and len(items) >= 2:
            break
        if attempt < 1:
            cooldown = 30 + random.uniform(0, 10)  # 失败后冷却 30-40s，避免触发限流
            logger.info(f"[em_market_flow] 冷却 {cooldown:.0f}s 后重试（避免触发东财频率限制）...")
            time.sleep(cooldown)

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

    result = {
        "sh": sh_data,
        "sz": sz_data,
        "combined": combined,
        "flow_nature": flow_nature,
        "updated_at": datetime.now().isoformat(),
        "source": "em_push2_ulist_realtime",
    }
    _save_market_to_pg(result)
    return result


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
# PostgreSQL 持久化（market_scan 调用时同步落库）
# ═══════════════════════════════════════════════════════

def _save_sector_to_pg(items: list, sector_type: str):
    """将板块资金流写入 fund_flow_cache 表"""
    try:
        import json, os
        db_url = os.environ.get("DATABASE_URL", os.environ["DATABASE_URL"])
        import psycopg2
        from datetime import datetime
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        for item in items:
            name = item.get("name", "")
            data = {
                "name": name, "code": item.get("code", ""),
                "pct_change": item.get("pct_change", 0),
                "main_net": item.get("main_net", 0),
                "main_net_rate": item.get("main_net_rate", 0),
                "super_large_net": item.get("super_large_net", 0),
                "large_net": item.get("large_net", 0),
                "medium_net": item.get("medium_net", 0),
                "small_net": item.get("small_net", 0),
                "advancing": item.get("advancing", 0),
                "declining": item.get("declining", 0),
                "total_stocks": item.get("total_stocks", 0),
                "lead_stock_name": item.get("lead_stock_name", ""),
                "lead_stock_code": item.get("lead_stock_code", ""),
            }
            cur.execute(
                "INSERT INTO fund_flow_cache (data_type, symbol, data_json, updated_at) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (data_type, symbol) DO UPDATE SET data_json=EXCLUDED.data_json, updated_at=EXCLUDED.updated_at",
                ("concept", name, json.dumps(data, ensure_ascii=False), datetime.now()),
            )
        cur.execute(
            "INSERT INTO fund_flow_cache (data_type, symbol, data_json, updated_at) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (data_type, symbol) DO UPDATE SET data_json=EXCLUDED.data_json, updated_at=EXCLUDED.updated_at",
            ("concept", "__index__", json.dumps({"count": len(items)}), datetime.now()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _save_market_to_pg(data: dict):
    """将大盘资金流写入 fund_flow_cache 表"""
    try:
        import json, os
        db_url = os.environ.get("DATABASE_URL", os.environ["DATABASE_URL"])
        import psycopg2
        from datetime import datetime
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        combined = data.get("combined", data)
        flat = {
            "main_net": combined.get("main_net", 0),
            "main_net_fmt": combined.get("main_net_fmt", ""),
            "main_net_rate": combined.get("main_net_rate", 0),
            "super_large_net": combined.get("super_large_net", 0),
            "large_net": combined.get("large_net", 0),
            "medium_net": combined.get("medium_net", 0),
            "small_net": combined.get("small_net", 0),
            "total_amount": combined.get("total_amount", 0),
            "total_amount_fmt": combined.get("total_amount_fmt", ""),
            "flow_nature": data.get("flow_nature", "平衡"),
            "source": data.get("source", ""),
        }
        cur.execute(
            "INSERT INTO fund_flow_cache (data_type, symbol, data_json, updated_at) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (data_type, symbol) DO UPDATE SET data_json=EXCLUDED.data_json, updated_at=EXCLUDED.updated_at",
            ("market", "", json.dumps(flat, ensure_ascii=False), datetime.now()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


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
