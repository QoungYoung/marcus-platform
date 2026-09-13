# -*- coding: utf-8 -*-
"""Tushare 数据中继客户端 —— 统一替代已失效的 gzcloud 代理（ts.gyzcloud.top）。

背景（2026-09-13）
------------------
原各路径统一走 `.env` 的 `TUSHARE_API_URL=https://ts.gyzcloud.top/api`（Tushare 风格 POST
代理）。该镜像 token 已过期（HTTP 401「Token无效或已过期，请联系客服续费」），导致
`get_tushare_pro()` 派生的所有取数路径（选股 / regime / 日线回填 / 分钟线）全部拿不到数据。

新数据源（两套，均 GET + Header `X-API-Key`，返回 Tushare 风格 `data.fields/items`）：

1. **datahubco**（`DATAHUBCO_API_KEY`，默认 `http://datahubco.com/app-api/openapi/v1/tushare`）
   —— 「15000 积分基础功能」，本地 RDS 为主，**速度飞快**，手册列 80 个基础接口
   （daily / daily_basic / trade_cal / index_daily / fund_daily / moneyflow / stk_limit ...），
   另有实测可用且字段兼容的扩展接口（adj_factor / stock_basic / moneyflow_dc / dc_* / ths_* /
   etf_basic / fund_share / sw_daily ... 见 DATAHUBCO_APIS）。
   实测限制：**必须带 `limit`（≤5000）**，否则大结果集直接 HTTP 413；分页用 `offset`。
   不支持 `pro_bar`（HTTP 400「请指定正确的接口名」）。
2. **promax**（`PROMAX_API_KEY`，默认 `https://pcd.mobcvb.cn/tushare/pro`）
   —— 298 接口聚合（含 stk_mins / rt_* / moneyflow_ind_dc / dc_* / ths_* / sw_daily / pro_bar ...）。
   实测抖动明显：`502/503/504 upstream_pool_exhausted` 与超时是常态 → 必须退避重试。
   只带 `limit/offset/fields` 的请求会被判为「探测」，正式请求需带业务参数（或显式 `__probe=0`）。

设计要点
--------
- **单一入口**：`get_relay()` 返回与 `tushare.pro.client.DataApi` 兼容的对象
  （`pro.daily(...)` / `pro.query('daily', ...)` / `ts.pro_bar(api=pro, ...)` 均可直接使用），
  返回 pandas DataFrame。`backend/app/core/trading/_api_config.get_tushare_pro()` 与
  `core/_api_config.get_tushare_pro()` 都委托到这里。
- **自动路由**：datahubco 覆盖的接口优先走 datahubco（快），其余走 promax；
  某源报错（4xx/5xx/上游 code!=0/连接失败）自动降级到另一源。
- **分页**：datahubco 不带 limit 的大结果集直接 413 → 自动补 `limit=5000`（stk_factor_pro 类超宽表
  上限 1000）并按 `offset` 翻页，对外仍是「一次查询返回全部行」。
- **分段**：两家网关都限制单次查询的日期跨度（>~1 年直接 400 date range too large）→
  捕获该错误后按 `TUSHARE_RELAY_MAX_RANGE_DAYS`（默认 360 天）分段取数并合并。
- **参数归一**：日期 `YYYY-MM-DD` → `YYYYMMDD`；空串视作「未指定」；列表 → 逗号串；
  `freq` 小写；只给 `end_date`+`limit` 的「取最近 N 根」自动补 start_date（否则 datahubco 400）。
- **`pro_bar` 本地合成**：用 tushare 自带实现（内部拆成 daily/index_daily + adj_factor，走
  datahubco 快通道），不走 promax 抖动的 `/pro_bar`；并记录 daily 的窗口供紧随其后的
  adj_factor 复用（pro_bar(limit=N) 实测 8s → 1.5s）。
- **重试**：429/5xx/断连按 1s→2s 退避重试（次数可配）。
- **降级记忆**：某源对某接口返回「不支持」类错误（404/405、400 含 unknown_api）时，
  进程内 30 分钟不再尝试该源；**参数类 400（如日期跨度超限）不降级**，避免误伤整个接口。
- **不缓存行情**：仅对 `trade_cal`/`stock_basic` 这类参考数据做短 TTL 缓存（默认 300s）。
- **已知契约差异**：`fund_portfolio` 在 datahubco 要求 start_date/end_date 成对（只给 end_date
  会 400 → 自动降级 promax）；`limit_list_d` 在 datahubco 缺 `limit_type` 字段 → 仍走 promax。

环境变量
--------
| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DATAHUBCO_API_KEY` | 空 | datahubco 客户密钥（必需，否则该源不可用） |
| `DATAHUBCO_API_URL` | `http://datahubco.com/app-api/openapi/v1/tushare` | datahubco 入口 |
| `PROMAX_API_KEY` | 空 | promax 客户密钥（必需，否则该源不可用） |
| `PROMAX_URL` | `https://pcd.mobcvb.cn/tushare/pro` | promax 入口 |
| `TUSHARE_SOURCE` | `auto` | `auto`/`datahubco`/`promax`/`legacy` 强制指定源 |
| `TUSHARE_RELAY_TIMEOUT` | `30` | 单次 HTTP 超时（秒） |
| `TUSHARE_RELAY_ATTEMPTS` | `3` | 单源重试次数 |
| `TUSHARE_RELAY_PAGE_SIZE` | `5000` | datahubco 分页大小 |
| `TUSHARE_RELAY_MAX_PAGES` | `6` | datahubco 单次调用最多翻页数（防跑飞） |
| `TUSHARE_RELAY_MAX_RANGE_DAYS` | `360` | 单次查询日期跨度上限；超限自动分段取数再合并（网关实测 >~1 年直接 400） |
| `TUSHARE_RELAY_CACHE_TTL` | `300` | 参考数据缓存秒数（0=关闭） |
| `TUSHARE_RELAY_DEMOTE_TTL` | `1800` | 某源对某接口返回 4xx（请求形态不支持）后的跳过时长（0=不跳过） |
| `TUSHARE_API_URL` + `TUSHARE_TOKEN` | 空 | 仅 `TUSHARE_SOURCE=legacy` 时使用（兼容老代理） |

用法
----
    from tushare_relay import get_relay, relay_query, relay_items

    pro = get_relay()
    df = pro.daily(ts_code="000001.SZ", start_date="20260901", end_date="20260911")
    df = relay_query("trade_cal", exchange="SSE", start_date="20260901", end_date="20260911")
    fields, items = relay_items("stock_basic", list_status="L", limit=3)
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

logger = logging.getLogger("tushare_relay")

# ── .env 载入（与 core/_api_config.py 相同的「向上找 .env」策略） ──────────────
def _load_env() -> None:
    """从项目根目录加载 .env 到 os.environ（不覆盖已存在的环境变量）。"""
    current = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = current / ".env"
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key and key not in os.environ:
                            os.environ[key] = value
            except OSError:  # pragma: no cover - 权限/编码异常不阻断取数
                pass
            return
        current = current.parent


_load_env()

DEFAULT_DATAHUBCO_URL = "http://datahubco.com/app-api/openapi/v1/tushare"
DEFAULT_PROMAX_URL = "https://pcd.mobcvb.cn/tushare/pro"

# ── datahubco 覆盖的接口（手册 §4「80 个接口总览」，去掉 HTTP 不支持的 pro_bar） ──
_DOC_DATAHUBCO_APIS = {
    # 股票
    "daily", "weekly", "monthly", "daily_basic", "new_share", "top_list", "top_inst",
    "pledge_detail", "pledge_stat", "margin", "margin_detail", "repurchase", "share_float",
    "block_trade", "stk_holdernumber", "moneyflow", "stk_holdertrade", "stk_limit", "hk_hold",
    # 财务
    "income", "balancesheet", "cashflow", "forecast", "express", "dividend",
    "fina_indicator", "fina_audit", "fina_mainbz", "disclosure_date",
    # 基金
    "fund_basic", "fund_company", "fund_nav", "fund_daily", "fund_div", "fund_portfolio", "fund_adj",
    # 期货
    "fut_basic", "trade_cal", "fut_daily", "fut_holding", "fut_wsr", "fut_settle",
    # 指数
    "index_daily", "index_basic", "index_weekly", "index_monthly", "index_weight",
    "index_dailybasic", "index_classify", "index_member_all",
    # 期权 / 债券 / 外汇 / 港股
    "opt_basic", "opt_daily", "cb_basic", "cb_issue", "cb_daily",
    "fx_obasic", "fx_daily", "hk_basic",
    # 宏观
    "shibor", "shibor_quote", "shibor_lpr", "libor", "hibor", "wz_index", "gz_index",
    # 行业特色 / 特色
    "tmt_twincome", "tmt_twincomedetail", "bo_monthly", "bo_weekly", "bo_daily", "bo_cinema",
    "film_record", "teleplay_record", "report_rc", "cyq_perf", "cyq_chips", "stk_rewards",
    "stk_factor_pro", "stk_nineturn",
}

# 手册总览未列、但实测可用且字段与 promax 一致/兼容的接口
# - stock_basic : 全 A 基础资料（promax 只带 limit 时会被判为探测，datahubco 直接返回）
# - adj_factor  : 复权因子（pro_bar 前/后复权的必需输入，走 datahubco 快通道）
# - moneyflow_* / dc_* / ths_* / etf_basic / fund_share / sw_daily / suspend_d / stock_company /
#   bak_daily   : 实测字段兼容且 datahubco 命中本地 RDS（毫秒~秒级），同请求 promax 要 10~23s，
#                 个别接口还会返回超范围数据（fund_share 指定 9 天却回 2212 行）→ 统一优先 datahubco
_EXTRA_DATAHUBCO_APIS = {
    "stock_basic", "adj_factor",
    "moneyflow_dc", "moneyflow_ind_dc", "moneyflow_mkt_dc",
    "dc_index", "dc_member", "dc_daily",
    "ths_index", "ths_daily", "ths_member",
    "etf_basic", "fund_share", "sw_daily", "suspend_d", "stock_company", "bak_daily",
}

DATAHUBCO_APIS = frozenset(_DOC_DATAHUBCO_APIS | _EXTRA_DATAHUBCO_APIS)

# 参考数据（变动极慢）→ 允许短 TTL 缓存，缓解 trade_cal/stock_basic 高频重复调用
CACHEABLE_APIS = frozenset({"trade_cal", "stock_basic"})

# pro_bar 配对：daily/index_daily 的窗口被记下，供紧随其后的 adj_factor 复用（见 _apply_window_hint）
_WINDOW_HINT_SOURCE_APIS = frozenset({"daily", "index_daily", "fund_daily"})
_WINDOW_HINT_TARGET_APIS = frozenset({"adj_factor"})

# datahubco 单页上限（实测）：超宽表（stk_factor_pro 数百列）行数上限更小，超了直接 HTTP 400/413
DATAHUBCO_PAGE_CAP = {"stk_factor_pro": 1000}

# 需要退避重试的 HTTP 状态码（promax 抖动 + 限流）
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

_PLAIN_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MIN_FREQ_RE = re.compile(r"^\d+MIN$", re.IGNORECASE)
# 非业务参数：只有这些参数时请求会被 promax 识别为「探测」
_NON_BUSINESS_PARAMS = frozenset({"limit", "offset", "fields", "__probe", "ts_type_name"})


class TushareRelayError(RuntimeError):
    """中继取数失败（所有可用源都失败时抛出）。"""


@dataclass(frozen=True)
class _Source:
    """一个数据源：名称 + 入口 + 认证方式。"""

    name: str
    base_url: str
    api_key: str
    style: str  # "get" = GET + X-API-Key（datahubco / promax）；"legacy" = Tushare 风格 POST


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(float(raw))
    except ValueError:
        return default
    return value


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _build_sources() -> List[_Source]:
    """按优先级构造可用源列表（datahubco → promax → [可选] legacy）。"""
    sources: List[_Source] = []
    forced = (os.getenv("TUSHARE_SOURCE") or "auto").strip().lower()

    dh_key = (os.getenv("DATAHUBCO_API_KEY") or os.getenv("DATAHUBCO_TOKEN") or "").strip()
    dh_url = (os.getenv("DATAHUBCO_API_URL") or DEFAULT_DATAHUBCO_URL).strip().rstrip("/")
    pm_key = (os.getenv("PROMAX_API_KEY") or "").strip()
    pm_url = (os.getenv("PROMAX_URL") or DEFAULT_PROMAX_URL).strip().rstrip("/")

    if forced in ("datahubco", "dh"):
        if dh_key:
            sources.append(_Source("datahubco", dh_url, dh_key, "get"))
    elif forced in ("promax", "pm"):
        if pm_key:
            sources.append(_Source("promax", pm_url, pm_key, "get"))
    elif forced in ("legacy", "tushare", "gzcloud"):
        pass  # 只走 legacy，见下
    else:  # auto：两个新源都放进来，按顺序路由
        if dh_key:
            sources.append(_Source("datahubco", dh_url, dh_key, "get"))
        if pm_key:
            sources.append(_Source("promax", pm_url, pm_key, "get"))

    if forced in ("legacy", "tushare", "gzcloud") or not sources:
        legacy_url = (os.getenv("TUSHARE_API_URL") or "").strip().rstrip("/")
        legacy_token = (os.getenv("TUSHARE_TOKEN") or "").strip()
        if legacy_url and legacy_token:
            sources.append(_Source("legacy", legacy_url, legacy_token, "legacy"))

    return sources


class TushareRelay:
    """Tushare 兼容的中继客户端（可直接替代 `ts.pro_api()` 的返回值）。

    属性访问（`relay.daily(...)`）等价于 `relay.query("daily", ...)`，返回 DataFrame。
    """

    def __init__(self, timeout: Optional[float] = None, attempts: Optional[int] = None,
                 sources: Optional[Sequence[_Source]] = None) -> None:
        self._timeout = timeout
        self._attempts = attempts
        self._sources = list(sources) if sources is not None else _build_sources()
        self._local = threading.local()
        self._cache: Dict[Any, Tuple[float, List[str], List[list]]] = {}
        self._demoted: Dict[Tuple[str, str], float] = {}
        self._window_hints: Dict[str, Tuple[str, str, float]] = {}
        self._cache_lock = threading.Lock()

    # ── 基础属性 ────────────────────────────────────────────────
    @property
    def timeout(self) -> float:
        if self._timeout is not None:
            return float(self._timeout)
        return _env_float("TUSHARE_RELAY_TIMEOUT", 30.0)

    @property
    def attempts(self) -> int:
        if self._attempts is not None:
            return max(1, int(self._attempts))
        return max(1, _env_int("TUSHARE_RELAY_ATTEMPTS", 3))

    @property
    def sources(self) -> List[_Source]:
        return list(self._sources)

    def available_sources(self) -> List[str]:
        return [s.name for s in self._sources]

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"Accept": "application/json"})
            self._local.session = session
        return session

    # ── 对外查询接口 ────────────────────────────────────────────
    def query(self, api_name: str, fields: str = "", **params: Any):
        """执行一次查询并返回 pandas.DataFrame（与 tushare DataApi.query 行为一致）。

        特例：`pro_bar`（复权行情）优先用 tushare 自带实现（内部拆成 daily/index_daily +
        adj_factor，走 **datahubco 快通道**），失败再退网关 `/pro_bar`（promax 聚合）。
        """
        api = str(api_name).strip()
        if api == "pro_bar":
            local = self._pro_bar_local(fields=fields, **params)
            if local is not None:
                return local
        response_fields, items = self.query_items(api, fields=fields, **params)
        import pandas as pd

        return pd.DataFrame(items, columns=response_fields)

    def _pro_bar_local(self, fields: str = "", **params: Any):
        """用 tushare 官方 pro_bar 实现拼接复权行情（api=self → 内部走中继的 daily/adj_factor）。

        promax 网关虽也提供 /pro_bar，但实测 `503 upstream_pool_exhausted` 抖动明显；
        本地实现走 datahubco 的 daily/adj_factor（RDS 快、稳定）。失败返回 None（交回网关）。
        """
        params.pop("api", None)
        params.pop("ts_type_name", None)
        try:
            from tushare.pro.data_pro import pro_bar as _ts_pro_bar
        except Exception:  # tushare 未安装 → 网关兜底
            return None
        kwargs: Dict[str, Any] = {k: v for k, v in params.items() if v is not None}
        if fields:
            kwargs["fields"] = fields
        try:
            df = _ts_pro_bar(api=self, **kwargs)
        except Exception as exc:  # 参数不兼容 / 上游失败 → 网关兜底
            logger.info("[tushare_relay] 本地 pro_bar 失败，改走网关 /pro_bar: %s", str(exc)[:100])
            return None
        import pandas as pd

        if df is None:
            return None
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame(df)

    def query_items(self, api_name: str, fields: str = "", **params: Any) -> Tuple[List[str], List[list]]:
        """执行一次查询并返回原始 `(fields, items)`（不依赖 pandas）。"""
        api = str(api_name).strip()
        if not api:
            raise TushareRelayError("api_name 不能为空")
        clean = _clean_params(params)
        wants = _clean_fields(fields)
        # tushare 的 pro_bar 先查 daily、紧接着查 adj_factor（同一 ts_code、同一窗口），
        # 但 adj_factor 那次不带 limit 也没有 start_date → 记下 daily 的窗口复用，避免走慢源
        clean = self._apply_window_hint(api, clean)
        clean = _infer_start_date(clean)
        self._remember_window(api, clean)

        ordered = self._ordered_sources(api)
        if not ordered:
            raise EnvironmentError(
                "没有可用的数据源：请在 .env 配置 DATAHUBCO_API_KEY / PROMAX_API_KEY"
            )

        errors: List[str] = []
        for source in ordered:
            if self._is_demoted(source, api):
                # 该源此前对本接口返回过「请求形态不可用」（如 datahubco 413/400 行数上限），
                # 进程内不再重复撞墙（TTL 后自动恢复尝试）
                errors.append(f"{source.name}: skipped(demoted)")
                continue
            try:
                out_fields, items = self._fetch(source, api, clean, wants)
            except TushareRelayError as exc:
                errors.append(f"{source.name}: {exc}")
                if _is_request_shape_error(exc):
                    self._demote(source, api, str(exc))
                if len(ordered) > 1:
                    logger.warning("[tushare_relay] %s 走 %s 失败，尝试下一源: %s", api, source.name, exc)
                continue
            return out_fields, items

        raise TushareRelayError(f"{api} 全部数据源失败: " + " | ".join(errors))

    def __getattr__(self, name: str):
        # 与 tushare DataApi.__getattr__ 一致：pro.daily(...) → query('daily', ...)
        if name.startswith("_"):
            raise AttributeError(name)
        return partial(self.query, name)

    # ── 内部：源选择 ────────────────────────────────────────────
    def _ordered_sources(self, api: str) -> List[_Source]:
        """按接口所在源优先排序：datahubco 覆盖的接口优先 datahubco，其余优先 promax。"""
        if not self._sources:
            return []
        prefer = "datahubco" if api in DATAHUBCO_APIS else "promax"
        primary = [s for s in self._sources if s.name == prefer]
        others = [s for s in self._sources if s.name != prefer]
        return primary + others

    # ── 内部：接口级降级记忆（避免反复撞同一面墙） ───────────────
    def _is_demoted(self, source: _Source, api: str) -> bool:
        ttl = _env_int("TUSHARE_RELAY_DEMOTE_TTL", 1800)
        if ttl <= 0:
            return False
        with self._cache_lock:
            ts = self._demoted.get((source.name, api))
        if ts is None:
            return False
        if (time.time() - ts) > ttl:
            with self._cache_lock:
                self._demoted.pop((source.name, api), None)
            return False
        return True

    def _demote(self, source: _Source, api: str, reason: str) -> None:
        with self._cache_lock:
            self._demoted[(source.name, api)] = time.time()
        logger.info("[tushare_relay] %s 对 %s 暂标记不可用（%s）", source.name, api, reason[:80])

    def clear_demotions(self) -> None:
        with self._cache_lock:
            self._demoted.clear()

    # ── 内部：pro_bar 配对窗口提示（daily → adj_factor） ─────────
    def _remember_window(self, api: str, params: Dict[str, Any]) -> None:
        if api not in _WINDOW_HINT_SOURCE_APIS:
            return
        ts_code = params.get("ts_code")
        start, end = _as_date8(params.get("start_date")), _as_date8(params.get("end_date"))
        if not ts_code or not start or not end:
            return
        with self._cache_lock:
            if len(self._window_hints) > 512:
                self._window_hints.clear()
            self._window_hints[str(ts_code)] = (start, end, time.time())

    def _apply_window_hint(self, api: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if api not in _WINDOW_HINT_TARGET_APIS:
            return params
        if "start_date" in params or "end_date" not in params:
            return params
        ts_code = params.get("ts_code")
        if not ts_code:
            return params
        with self._cache_lock:
            hit = self._window_hints.get(str(ts_code))
        if not hit:
            return params
        start, end, ts = hit
        if (time.time() - ts) > _env_int("TUSHARE_RELAY_WINDOW_HINT_TTL", 15):
            return params
        merged = dict(params)
        merged["start_date"] = start
        return merged

    # ── 内部：单源取数 ──────────────────────────────────────────
    def _fetch(self, source: _Source, api: str, params: Dict[str, Any],
               fields: str) -> Tuple[List[str], List[list]]:
        cache_key = None
        if api in CACHEABLE_APIS:
            ttl = _env_int("TUSHARE_RELAY_CACHE_TTL", 300)
            if ttl > 0:
                cache_key = (source.name, api, tuple(sorted(params.items())), fields)
                cached = self._cache_get(cache_key)
                if cached is not None:
                    return cached

        if source.style == "legacy":
            out = self._fetch_legacy(source, api, params, fields)
        else:
            out = self._fetch_get(source, api, params, fields)

        if cache_key is not None and out[1]:
            self._cache_put(cache_key, out)
        return out

    def _fetch_get(self, source: _Source, api: str, params: Dict[str, Any],
                   fields: str) -> Tuple[List[str], List[list]]:
        try:
            return self._fetch_pages(source, api, params, fields)
        except TushareRelayError as exc:
            # 两家网关都对「单次查询的日期跨度」有硬上限（实测 >~1 年直接 400
            # requested date range is too large / date_range_too_large）→ 自动按窗口切分后合并
            if _is_date_range_too_large(exc):
                chunked = self._fetch_chunked(source, api, params, fields)
                if chunked is not None:
                    return chunked
            raise

    def _fetch_pages(self, source: _Source, api: str, params: Dict[str, Any],
                     fields: str) -> Tuple[List[str], List[list]]:
        page_size = max(1, _env_int("TUSHARE_RELAY_PAGE_SIZE", 5000))
        max_pages = max(1, _env_int("TUSHARE_RELAY_MAX_PAGES", 6))

        request_params = dict(params)
        if fields:
            request_params["fields"] = fields

        out_fields: Optional[List[str]] = None
        items: List[list] = []

        if source.name == "datahubco":
            # datahubco：不带 limit 的大结果集会直接 413，必须自带 limit（≤5000）并按需翻页
            requested_limit = _as_int(params.get("limit"))
            explicit_offset = params.get("offset") is not None
            cap = min(page_size, DATAHUBCO_PAGE_CAP.get(api, page_size))
            page = min(requested_limit or cap, cap)
            request_params["limit"] = page
            paginate = not explicit_offset
            max_pages = 1 if explicit_offset else max_pages
        else:
            page = 0
            paginate = False
            max_pages = 1
            if not _has_business_params(request_params):
                # promax 无业务参数会被当作「探测」，显式声明正式请求
                request_params["__probe"] = 0

        for index in range(max_pages):
            body = self._http_get_json(source, api, request_params)
            data = body.get("data") or {}
            page_fields = list(data.get("fields") or [])
            page_items = list(data.get("items") or [])
            if out_fields is None:
                out_fields = page_fields
            elif page_fields and page_fields != out_fields:
                # 不同页字段布局漂移：以首屏字段顺序为准重排（对不齐就丢弃该页）
                page_items = _realign_items(page_fields, page_items, out_fields)
            items.extend(page_items)

            if not paginate or len(page_items) < page:
                break
            if requested_limit is not None and len(items) >= requested_limit:
                break
            if index + 1 >= max_pages:
                if requested_limit is None:
                    logger.warning("[tushare_relay] %s 翻页达上限 %s 页（已取 %s 行）", api, max_pages, len(items))
                break
            request_params["offset"] = int(request_params.get("offset") or 0) + page

        return (out_fields or []), items

    def _fetch_chunked(self, source: _Source, api: str, params: Dict[str, Any],
                       fields: str) -> Optional[Tuple[List[str], List[list]]]:
        """日期跨度超上限时按窗口切分（新→旧），逐窗取数后合并。"""
        start, end = _as_date8(params.get("start_date")), _as_date8(params.get("end_date"))
        if not start or not end or start > end:
            return None
        window = max(30, _env_int("TUSHARE_RELAY_MAX_RANGE_DAYS", 360))
        out_fields: Optional[List[str]] = None
        merged: List[list] = []
        limit = _as_int(params.get("limit"))
        cursor = end
        guard = 0
        while cursor >= start and guard < 40:
            guard += 1
            chunk_start = _shift_date8(cursor, -window)
            if chunk_start < start or chunk_start == cursor:
                chunk_start = start
            chunk = dict(params)
            chunk["start_date"], chunk["end_date"] = chunk_start, cursor
            chunk.pop("offset", None)
            if limit is not None:
                remaining = limit - len(merged)
                if remaining <= 0:
                    break
                chunk["limit"] = remaining
            try:
                c_fields, c_items = self._fetch_pages(source, api, chunk, fields)
            except TushareRelayError as exc:
                logger.warning("[tushare_relay] %s 分段取数失败(%s~%s): %s", api, chunk_start, cursor, str(exc)[:80])
                return None
            if out_fields is None and c_fields:
                out_fields = c_fields
            merged.extend(c_items)
            if chunk_start == start:
                break
            cursor = _shift_date8(chunk_start, -1)
        logger.info("[tushare_relay] %s 日期跨度超限 → 分 %d 段取数（%s~%s，共 %d 行）",
                    api, guard, start, end, len(merged))
        return (out_fields or []), merged

    def _fetch_legacy(self, source: _Source, api: str, params: Dict[str, Any],
                      fields: str) -> Tuple[List[str], List[list]]:
        """兼容老式 Tushare 代理：POST {api_name, token, params, fields}。"""
        url = f"{source.base_url}/{api}"
        payload = {"api_name": api, "token": source.api_key, "params": params, "fields": fields}
        last_err: Optional[Exception] = None
        for attempt in range(1, self.attempts + 1):
            try:
                resp = self._session().post(
                    url, json=payload, timeout=self.timeout,
                    headers={"Accept-Encoding": "identity"},
                )
                body = _json_or_raise(resp)
                data = body.get("data") or {}
                return list(data.get("fields") or []), list(data.get("items") or [])
            except TushareRelayError as exc:
                last_err = exc
                if attempt < self.attempts:
                    time.sleep(min(2.0 ** (attempt - 1), 4.0))
        raise TushareRelayError(str(last_err))

    def _http_get_json(self, source: _Source, api: str, params: Dict[str, Any]) -> dict:
        url = f"{source.base_url}/{api}"
        headers = {"X-API-Key": source.api_key}
        last_err: Optional[Exception] = None
        for attempt in range(1, self.attempts + 1):
            try:
                resp = self._session().get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_err = TushareRelayError(f"连接失败 {type(exc).__name__}: {str(exc)[:120]}")
                if attempt < self.attempts:
                    time.sleep(min(2.0 ** (attempt - 1), 4.0))
                    continue
                raise last_err
            if resp.status_code in _RETRY_STATUS and attempt < self.attempts:
                time.sleep(min(2.0 ** (attempt - 1), 4.0))
                last_err = TushareRelayError(f"HTTP {resp.status_code}")
                continue
            return _json_or_raise(resp)
        raise TushareRelayError(str(last_err))

    # ── 内部：缓存 ──────────────────────────────────────────────
    def _cache_get(self, key) -> Optional[Tuple[List[str], List[list]]]:
        with self._cache_lock:
            hit = self._cache.get(key)
        if not hit:
            return None
        ts, fields, items = hit
        ttl = _env_int("TUSHARE_RELAY_CACHE_TTL", 300)
        if ttl <= 0 or (time.time() - ts) > ttl:
            with self._cache_lock:
                self._cache.pop(key, None)
            return None
        return list(fields), [list(row) for row in items]

    def _cache_put(self, key, value: Tuple[List[str], List[list]]) -> None:
        fields, items = value
        with self._cache_lock:
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[key] = (time.time(), list(fields), [list(r) for r in items])

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()


# ── 模块级便捷函数 ──────────────────────────────────────────────
_relay_singleton: Optional[TushareRelay] = None
_singleton_lock = threading.Lock()


def get_relay() -> TushareRelay:
    """进程内单例中继客户端（复用连接与参考数据缓存）。"""
    global _relay_singleton
    if _relay_singleton is None:
        with _singleton_lock:
            if _relay_singleton is None:
                _relay_singleton = TushareRelay()
    return _relay_singleton


def reset_relay() -> None:
    """丢弃单例（测试或运行时切换 .env 后调用）。"""
    global _relay_singleton
    with _singleton_lock:
        _relay_singleton = None


def relay_query(api_name: str, fields: str = "", **params: Any):
    """便捷函数：直接查一次并返回 DataFrame。"""
    return get_relay().query(api_name, fields=fields, **params)


def relay_items(api_name: str, fields: str = "", **params: Any) -> Tuple[List[str], List[list]]:
    """便捷函数：直接查一次并返回 `(fields, items)` 原始结构。"""
    return get_relay().query_items(api_name, fields=fields, **params)


def available_sources() -> List[str]:
    """当前可用源名称（排障用）。"""
    return get_relay().available_sources()


# ── 参数/字段处理 ───────────────────────────────────────────────
def _clean_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """丢弃 None / tushare 内部参数，归一日期与 freq，列表转逗号串。"""
    clean: Dict[str, Any] = {}
    for key, value in params.items():
        if value is None or key in ("api", "self", "ts_type_name"):
            continue
        if isinstance(value, (list, tuple, set)):
            value = ",".join(str(v) for v in value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue  # 空串按 tushare 语义视作「未指定」（网关对空串会报参数缺失）
            if _PLAIN_DATE_RE.match(value):
                value = value.replace("-", "")
            elif key == "freq" and _MIN_FREQ_RE.match(value):
                value = value.lower()
        elif isinstance(value, bool):
            value = int(value)
        clean[key] = value
    return clean


def _clean_fields(fields: Any) -> str:
    if not fields:
        return ""
    if isinstance(fields, (list, tuple, set)):
        return ",".join(str(f).strip() for f in fields if str(f).strip())
    return str(fields).strip()


def _has_business_params(params: Dict[str, Any]) -> bool:
    return any(k not in _NON_BUSINESS_PARAMS for k in params)


def _infer_start_date(params: Dict[str, Any]) -> Dict[str, Any]:
    """只给 end_date + limit 的「取最近 N 根」请求补一个安全的 start_date。

    两家网关都要求 start_date/end_date 成对（datahubco 缺失直接 400），而 tushare 的
    `pro_bar(ts_code=..., limit=N)` 会先把 start_date 置空 → 白白走到慢源。
    有 limit 时结果集本来就有界，补一个足够宽的窗口不改变返回内容（仍是最新 N 根）。
    """
    if "end_date" not in params or "start_date" in params:
        return params
    limit = _as_int(params.get("limit"))
    if not limit or limit > 1000:
        return params
    end8 = _as_date8(params["end_date"])
    if not end8:
        return params
    window = min(max(730, limit * 5), 3650)
    merged = dict(params)
    merged["start_date"] = _shift_date8(end8, -window)
    return merged


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_request_shape_error(exc: Exception) -> bool:
    """判断是否为「该源根本不支持这个接口/方法」类错误（重试无用，可进程内降级跳过）。

    注意：**不能**把一般的 400/413 都算进来 —— 例如「日期跨度超限」是参数问题（中继会分段重试）、
    「payload too large」是结果集体积问题（换个接口参数可能就好了），把它们当成接口不可用会误伤
    同一接口的其它调用。
    """
    text = str(exc)
    if "HTTP 404" in text or "HTTP 405" in text:
        return True
    unknown_api_markers = ("请指定正确的接口名", "unknown_api", "未登记的接口", "not registered")
    return "HTTP 400" in text and any(m in text for m in unknown_api_markers)


def _is_date_range_too_large(exc: Exception) -> bool:
    """两家网关对单次查询日期跨度都有上限（>~1 年直接 400），需要分段重试。"""
    text = str(exc).lower()
    return ("date range is too large" in text) or ("date_range_too_large" in text)


def _as_date8(value: Any) -> Optional[str]:
    """把 YYYYMMDD / YYYY-MM-DD 规整为 YYYYMMDD，其它返回 None。"""
    if value is None:
        return None
    text = str(value).strip().replace("-", "")
    return text if len(text) == 8 and text.isdigit() else None


def _shift_date8(date8: str, days: int) -> str:
    from datetime import date, timedelta

    d = date(int(date8[:4]), int(date8[4:6]), int(date8[6:8])) + timedelta(days=days)
    return d.strftime("%Y%m%d")


def _realign_items(page_fields: List[str], page_items: List[list],
                   target_fields: List[str]) -> List[list]:
    """把某页行按字段名对齐到首屏字段顺序；缺字段的行丢弃。"""
    index = {name: i for i, name in enumerate(page_fields)}
    if not all(name in index for name in target_fields):
        return []
    order = [index[name] for name in target_fields]
    out: List[list] = []
    for row in page_items:
        if len(row) < len(page_fields):
            continue
        out.append([row[i] for i in order])
    return out


def _json_or_raise(resp: requests.Response) -> dict:
    """解析响应并按新网关的错误约定判定成功/失败。"""
    try:
        body = resp.json()
    except ValueError:
        raise TushareRelayError(f"HTTP {resp.status_code}: 非 JSON 响应 {resp.text[:120]!r}")
    if not isinstance(body, dict):
        raise TushareRelayError(f"HTTP {resp.status_code}: 响应格式异常")
    if resp.status_code >= 400:
        detail = body.get("error") or body.get("message") or body.get("msg") or body.get("code")
        raise TushareRelayError(f"HTTP {resp.status_code}: {detail}")
    if body.get("ok") is False:
        raise TushareRelayError(f"上游错误: {body.get('error') or body.get('message')}")
    code = body.get("code")
    if code not in (0, None):
        raise TushareRelayError(f"上游 code={code}: {body.get('msg') or body.get('message') or ''}")
    return body


__all__ = [
    "DATAHUBCO_APIS",
    "CACHEABLE_APIS",
    "TushareRelay",
    "TushareRelayError",
    "available_sources",
    "get_relay",
    "relay_items",
    "relay_query",
    "reset_relay",
]
