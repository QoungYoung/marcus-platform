# -*- coding: utf-8 -*-
"""tushare 中继单元测试（core/tushare_relay.py）。

2026-09-13：gzcloud 代理 token 失效 → 取数统一改走 **datahubco（基础接口，RDS 快）+
promax（聚合接口）** 中继。本文件覆盖路由/分页/参数归一/双源降级/缓存等关键行为，
全部离线（网络请求用假 session 替换），不消耗真实配额。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "core") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "core"))

import tushare_relay as R  # noqa: E402


# ── 测试替身 ────────────────────────────────────────────────
class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """记录所有请求；按队列顺序返回预设响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        if not self.responses:
            return FakeResponse({"code": 0, "data": {"fields": [], "items": []}})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": dict(headers or {})})
        item = self.responses.pop(0) if self.responses else FakeResponse({"code": 0, "data": {}})
        if isinstance(item, Exception):
            raise item
        return item


def _body(fields, items, code=0, **extra):
    payload = {"code": code, "data": {"fields": fields, "items": items}}
    payload.update(extra)
    return FakeResponse(payload)


def _relay(sources, responses):
    relay = R.TushareRelay(timeout=5, attempts=1, sources=sources)
    session = FakeSession(responses)
    relay._session = lambda: session  # type: ignore[assignment]
    return relay, session


DH = R._Source("datahubco", "http://dh.test/api", "dh-key", "get")
PM = R._Source("promax", "https://pm.test/pro", "pm-key", "get")


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_CACHE_TTL", "0")


# ── 路由 ────────────────────────────────────────────────────
def test_datahubco_apis_prefer_datahubco():
    relay = R.TushareRelay(sources=[DH, PM])
    assert [s.name for s in relay._ordered_sources("daily")] == ["datahubco", "promax"]
    assert [s.name for s in relay._ordered_sources("trade_cal")] == ["datahubco", "promax"]


def test_promax_only_apis_prefer_promax():
    relay = R.TushareRelay(sources=[DH, PM])
    for api in ("stk_mins", "research_report", "dc_index_prev", "pro_bar", "limit_list_d"):
        assert [s.name for s in relay._ordered_sources(api)] == ["promax", "datahubco"], api
    # pro_bar 属 promax（datahubco HTTP 不支持）
    assert "pro_bar" not in R.DATAHUBCO_APIS
    # limit_list_d：datahubco 返回的字段缺 limit_type（main_wave 依赖它）→ 仍走 promax
    assert "limit_list_d" not in R.DATAHUBCO_APIS


def test_datahubco_extras_cover_hot_paths():
    """实测 datahubco 命中本地 RDS（毫秒~秒级），这些热路径必须优先 datahubco。"""
    for api in ("adj_factor", "stock_basic", "moneyflow_dc", "moneyflow_ind_dc",
                "etf_basic", "fund_share", "sw_daily"):
        assert api in R.DATAHUBCO_APIS, api


def test_build_sources_auto_order(monkeypatch):
    monkeypatch.setenv("DATAHUBCO_API_KEY", "k1")
    monkeypatch.setenv("PROMAX_API_KEY", "k2")
    monkeypatch.delenv("TUSHARE_SOURCE", raising=False)
    assert [s.name for s in R._build_sources()] == ["datahubco", "promax"]


def test_build_sources_forced_and_missing(monkeypatch):
    monkeypatch.setenv("DATAHUBCO_API_KEY", "k1")
    monkeypatch.setenv("PROMAX_API_KEY", "k2")
    monkeypatch.setenv("TUSHARE_SOURCE", "promax")
    assert [s.name for s in R._build_sources()] == ["promax"]

    monkeypatch.setenv("TUSHARE_SOURCE", "auto")
    monkeypatch.delenv("DATAHUBCO_API_KEY")
    monkeypatch.delenv("PROMAX_API_KEY")
    monkeypatch.delenv("TUSHARE_API_URL", raising=False)
    assert R._build_sources() == []


def test_no_source_raises_environment_error(monkeypatch):
    monkeypatch.delenv("DATAHUBCO_API_KEY", raising=False)
    monkeypatch.delenv("PROMAX_API_KEY", raising=False)
    relay = R.TushareRelay(sources=[])
    with pytest.raises(EnvironmentError):
        relay.query("daily", ts_code="000001.SZ")


# ── datahubco：limit / 分页 / 参数归一 ───────────────────────
def test_datahubco_always_sends_limit_and_paginates():
    page1 = _body(["ts_code"], [["000001.SZ"]] * 5000)
    page2 = _body(["ts_code"], [["000002.SZ"]] * 550)
    relay, session = _relay([DH], [page1, page2])
    fields, items = relay.query_items("daily", trade_date="20260911")
    assert len(items) == 5550
    assert session.calls[0]["params"]["limit"] == 5000
    assert "offset" not in session.calls[0]["params"]
    assert session.calls[1]["params"]["offset"] == 5000
    assert session.calls[0]["headers"]["X-API-Key"] == "dh-key"


def test_datahubco_stops_when_page_not_full():
    relay, session = _relay([DH], [_body(["ts_code"], [["000001.SZ"]] * 10)])
    fields, items = relay.query_items("daily", trade_date="20260911")
    assert len(items) == 10
    assert len(session.calls) == 1


def test_datahubco_explicit_limit_not_paginated():
    relay, session = _relay([DH], [_body(["ts_code"], [["000001.SZ"]] * 5000)])
    relay.query_items("daily", trade_date="20260911", limit=5000)
    assert len(session.calls) == 1
    assert session.calls[0]["params"]["limit"] == 5000


def test_param_normalization():
    relay, session = _relay([DH], [_body(["ts_code"], [])])
    relay.query_items("daily", ts_code="000001.SZ", start_date="2026-09-01",
                      end_date="2026-09-11", freq="5MIN", dummy=None,
                      codes=["000001.SZ", "600000.SH"])
    params = session.calls[0]["params"]
    assert params["start_date"] == "20260901"
    assert params["end_date"] == "20260911"
    assert params["freq"] == "5min"
    assert params["codes"] == "000001.SZ,600000.SH"
    assert "dummy" not in params
    assert "api" not in params


def test_fields_passed_through():
    relay, session = _relay([DH], [_body(["ts_code", "close"], [])])
    df = relay.query("daily", ts_code="000001.SZ", fields="ts_code,close")
    assert session.calls[0]["params"]["fields"] == "ts_code,close"
    assert list(df.columns) == ["ts_code", "close"]


# ── promax：探测规避 ────────────────────────────────────────
def test_promax_probe_guard_without_business_params():
    relay, session = _relay([PM], [_body(["ts_code"], [])])
    relay.query_items("stock_basic", limit=3)
    assert session.calls[0]["params"]["__probe"] == 0
    assert session.calls[0]["params"]["limit"] == 3


def test_promax_no_probe_guard_with_business_params():
    relay, session = _relay([PM], [_body(["ts_code"], [])])
    relay.query_items("stock_basic", list_status="L")
    assert "__probe" not in session.calls[0]["params"]


def test_promax_does_not_inject_limit():
    relay, session = _relay([PM], [_body(["ts_code"], [])])
    relay.query_items("daily", trade_date="20260911")
    assert "limit" not in session.calls[0]["params"]


# ── 双源降级 / 错误处理 ─────────────────────────────────────
def test_fallback_to_promax_on_datahubco_http_error():
    err = FakeResponse({"error": "payload_too_large"}, status_code=413)
    ok = _body(["ts_code", "close"], [["000001.SZ", 11.7]])
    relay, session = _relay([DH, PM], [err, ok])
    fields, items = relay.query_items("daily", trade_date="20260911")
    assert items == [["000001.SZ", 11.7]]
    assert session.calls[0]["url"].startswith("http://dh.test")
    assert session.calls[1]["url"].startswith("https://pm.test")


def test_fallback_on_upstream_code_error():
    bad = _body([], [], code=-1, msg="Token无效或已过期")
    ok = _body(["ts_code"], [["000001.SZ"]])
    relay, session = _relay([DH, PM], [bad, ok])
    fields, items = relay.query_items("daily", trade_date="20260911")
    assert items == [["000001.SZ"]]


def test_all_sources_failed_raises():
    relay, session = _relay([DH, PM], [
        FakeResponse({"error": "x"}, status_code=500),
        FakeResponse({"ok": False, "error": "upstream_pool_exhausted"}),
    ])
    with pytest.raises(R.TushareRelayError) as exc:
        relay.query_items("daily", trade_date="20260911")
    assert "全部数据源失败" in str(exc.value)


def test_retry_on_flaky_5xx(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_ATTEMPTS", "3")
    flaky = FakeResponse({"error": "upstream_pool_exhausted"}, status_code=503)
    ok = _body(["ts_code"], [["000001.SZ"]])
    relay = R.TushareRelay(timeout=5, sources=[PM])
    session = FakeSession([flaky, ok])
    relay._session = lambda: session  # type: ignore[assignment]
    monkeypatch.setattr(R.time, "sleep", lambda _s: None)
    fields, items = relay.query_items("daily", trade_date="20260911")
    assert items == [["000001.SZ"]]
    assert len(session.calls) == 2


def test_empty_result_is_success_not_fallback():
    empty = _body(["ts_code"], [])
    relay, session = _relay([DH, PM], [empty])
    fields, items = relay.query_items("daily", trade_date="20260912")
    assert items == []
    assert len(session.calls) == 1  # 空结果不算失败，不触发降级


# ── 缓存（仅参考数据） ──────────────────────────────────────
def test_reference_api_cached(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_CACHE_TTL", "300")
    body = _body(["cal_date", "is_open"], [["20260911", 1]])
    relay, session = _relay([DH], [body])
    for _ in range(3):
        relay.query_items("trade_cal", exchange="SSE", start_date="20260901", end_date="20260911")
    assert len(session.calls) == 1


def test_market_data_not_cached(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_CACHE_TTL", "300")
    body = _body(["ts_code"], [["000001.SZ"]])
    relay, session = _relay([DH], [body, body])
    relay.query_items("daily", trade_date="20260911")
    relay.query_items("daily", trade_date="20260911")
    assert len(session.calls) == 2


# ── DataFrame 语义 & 旧接口兼容 ─────────────────────────────
def test_query_returns_dataframe_like_tushare():
    pd = pytest.importorskip("pandas")
    body = _body(["ts_code", "trade_date", "close"],
                 [["000001.SZ", "20260911", 11.74], ["000001.SZ", "20260910", 11.85]])
    relay, session = _relay([DH], [body])
    df = relay.daily(ts_code="000001.SZ", start_date="20260910", end_date="20260911")
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["ts_code", "trade_date", "close"]
    assert df.iloc[0]["close"] == 11.74
    assert session.calls[0]["params"]["start_date"] == "20260910"


def test_pro_bar_style_kwargs_are_dropped():
    """ts.pro_bar(api=pro, ...) 会把 relay 自身作为 api kwarg 传进来，必须丢弃。"""
    body = _body(["ts_code"], [["000001.SZ"]])
    relay, session = _relay([DH], [body])
    relay.query("daily", api=relay, ts_code="000001.SZ")
    assert "api" not in session.calls[0]["params"]


def test_pro_bar_uses_local_tushare_impl_over_datahubco():
    """pro_bar 本地实现（daily + adj_factor，走 datahubco），不再依赖 promax /pro_bar。"""
    pytest.importorskip("tushare")
    daily = _body(
        ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"],
        [["000001.SZ", "20260911", 11.82, 11.86, 11.71, 11.74, 11.85, -0.11, -0.93, 832460.84, 979990.55],
         ["000001.SZ", "20260910", 11.68, 11.86, 11.66, 11.85, 11.70, 0.15, 1.28, 867632.22, 1022543.91]],
    )
    adj = _body(["ts_code", "trade_date", "adj_factor"],
                [["000001.SZ", "20260911", 139.008], ["000001.SZ", "20260910", 139.008]])
    relay, session = _relay([DH, PM], [daily, adj])
    df = relay.pro_bar(api=relay, ts_code="000001.SZ", adj="qfq", asset="E", freq="D",
                       start_date="20260910", end_date="20260911")
    assert len(df) == 2
    assert {c["url"].rsplit("/", 1)[-1] for c in session.calls} == {"daily", "adj_factor"}
    assert all(c["url"].startswith("http://dh.test") for c in session.calls)


# ── legacy 兜底通道 ────────────────────────────────────────
def test_legacy_source_posts_tushare_style(monkeypatch):
    legacy = R._Source("legacy", "https://legacy.test/api", "tok", "legacy")
    body = _body(["ts_code"], [["000001.SZ"]])
    relay, session = _relay([legacy], [body])
    relay.query_items("daily", ts_code="000001.SZ")
    call = session.calls[0]
    assert call["url"] == "https://legacy.test/api/daily"
    assert call["json"]["api_name"] == "daily"
    assert call["json"]["token"] == "tok"


# ── 请求形态不支持时的接口级降级记忆 ────────────────────────
def test_unknown_api_demotes_source(monkeypatch):
    """某源根本没登记该接口（404 / 400「请指定正确的接口名」）→ 进程内不再重复撞墙。"""
    monkeypatch.setenv("TUSHARE_RELAY_DEMOTE_TTL", "600")
    unknown = FakeResponse({"ok": False, "error": "unknown_api", "message": "未登记的接口: x"}, status_code=404)
    ok = _body(["ts_code"], [["000001.SZ"]])
    relay, session = _relay([PM, DH], [unknown, ok, ok])
    relay.query_items("concept_detail", ts_code="000001.SZ")
    relay.query_items("concept_detail", ts_code="000001.SZ")
    urls = [c["url"] for c in session.calls]
    assert urls[0].startswith("https://pm.test")   # 首次尝试 promax
    assert urls[1].startswith("http://dh.test")    # 降级 datahubco
    assert urls[2].startswith("http://dh.test")    # 第二次直接 datahubco，不再撞 promax


def test_demotion_expires(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_DEMOTE_TTL", "0")
    unknown = FakeResponse({"ok": False, "error": "unknown_api"}, status_code=404)
    ok = _body(["ts_code"], [["000001.SZ"]])
    relay, session = _relay([PM, DH], [unknown, ok, unknown, ok])
    relay.query_items("concept_detail", ts_code="000001.SZ")
    relay.query_items("concept_detail", ts_code="000001.SZ")
    assert session.calls[2]["url"].startswith("https://pm.test")


def test_transient_5xx_does_not_demote(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_DEMOTE_TTL", "600")
    err503 = FakeResponse({"error": "upstream_pool_exhausted"}, status_code=503)
    ok = _body(["ts_code"], [["000001.SZ"]])
    relay, session = _relay([DH, PM], [err503, ok, ok])
    relay.query_items("daily", trade_date="20260911")
    relay.query_items("daily", trade_date="20260912")
    assert session.calls[2]["url"].startswith("http://dh.test")  # 抖动不降级，仍优先 datahubco


def test_param_error_does_not_demote(monkeypatch):
    """参数类 400（如日期跨度超限）不是「接口不可用」，不能降级跳过（否则会误伤整个接口）。"""
    monkeypatch.setenv("TUSHARE_RELAY_DEMOTE_TTL", "600")
    err = FakeResponse({"error": "requested date range is too large"}, status_code=400)
    ok = _body(["ts_code"], [["000001.SZ"]])
    relay, session = _relay([DH, PM], [err, ok, ok])
    relay.query_items("daily", ts_code="000001.SZ", start_date="20250819", end_date="20260911")
    relay.query_items("daily", trade_date="20260911")
    assert relay._demoted == {}
    assert session.calls[2]["url"].startswith("http://dh.test")


# ── 日期跨度超限自动分段 ────────────────────────────────────
def test_long_date_range_is_chunked(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_MAX_RANGE_DAYS", "100")
    too_large = FakeResponse({"error": "requested date range is too large"}, status_code=400)
    seg1 = _body(["ts_code", "trade_date"], [["000001.SZ", "20260911"]])
    seg2 = _body(["ts_code", "trade_date"], [["000001.SZ", "20260601"]])
    seg3 = _body(["ts_code", "trade_date"], [["000001.SZ", "20260101"]])
    relay, session = _relay([DH], [too_large, seg1, seg2, seg3])
    fields, items = relay.query_items("daily", ts_code="000001.SZ",
                                      start_date="20260101", end_date="20260911")
    assert len(session.calls) == 4                       # 1 次全量失败 + 3 段
    assert [r[1] for r in items] == ["20260911", "20260601", "20260101"]
    assert session.calls[1]["params"]["end_date"] == "20260911"
    assert session.calls[2]["params"]["end_date"] < session.calls[1]["params"]["end_date"]
    assert session.calls[-1]["params"]["start_date"] == "20260101"


def test_infer_start_date_for_end_date_plus_limit():
    """只给 end_date + limit 的「取最近 N 根」请求补 start_date（网关要求成对），
    这样 pro_bar(limit=N) 也能走 datahubco 快通道，而不是被 400 打到慢源。"""
    relay, session = _relay([DH], [_body(["ts_code"], [])])
    relay.query_items("daily", ts_code="000001.SZ", end_date="20260911", limit=15)
    params = session.calls[0]["params"]
    assert params["end_date"] == "20260911"
    assert params["start_date"] <= "20250801"      # 730 天窗口
    assert params["limit"] == 15


def test_infer_start_date_skipped_without_limit():
    relay, session = _relay([DH], [_body(["ts_code"], [])])
    relay.query_items("daily", ts_code="000001.SZ", end_date="20260911")
    assert "start_date" not in session.calls[0]["params"]


def test_pro_bar_adj_factor_reuses_daily_window():
    """tushare pro_bar 先查 daily 再查 adj_factor（后者不带 limit/start_date）→
    adj_factor 复用 daily 的窗口，避免落到慢源（真实链路 8s → 1.5s）。"""
    pytest.importorskip("tushare")
    daily = _body(
        ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"],
        [["000001.SZ", "20260911", 11.82, 11.86, 11.71, 11.74, 11.85, -0.11, -0.93, 832460.84, 979990.55]],
    )
    adj = _body(["ts_code", "trade_date", "adj_factor"], [["000001.SZ", "20260911", 139.008]])
    relay, session = _relay([DH, PM], [daily, adj])
    relay.pro_bar(api=relay, ts_code="000001.SZ", adj="qfq", limit=5)
    adj_calls = [c for c in session.calls if c["url"].endswith("/adj_factor")]
    assert adj_calls, "未发起 adj_factor 查询"
    assert "start_date" in adj_calls[0]["params"]
    assert adj_calls[0]["url"].startswith("http://dh.test")


def test_empty_string_params_dropped():
    """start_date='' 这类空串按「未指定」处理（网关对空串会报参数缺失）。"""
    relay, session = _relay([DH], [_body(["ts_code"], [])])
    relay.query_items("daily", ts_code="000001.SZ", start_date="", end_date="20260911")
    assert "start_date" not in session.calls[0]["params"]
    assert session.calls[0]["params"]["end_date"] == "20260911"


def test_stk_factor_pro_page_cap():
    """datahubco 超宽表 stk_factor_pro 单页上限 1000（实测 5000 会 400）。"""
    relay, session = _relay([DH], [_body(["ts_code"], [["000001.SZ"]] * 10)])
    relay.query_items("stk_factor_pro", ts_code="000001.SZ")
    assert session.calls[0]["params"]["limit"] == 1000


# ── backend 统一入口 ────────────────────────────────────────
def test_backend_get_tushare_pro_returns_relay():
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.core.trading import _api_config as cfg
    pro = cfg.get_tushare_pro()
    assert isinstance(pro, R.TushareRelay)
    assert hasattr(pro, "daily")          # __getattr__ → query
    assert pro is cfg.get_tushare_pro()   # 单例
