# -*- coding: utf-8 -*-
"""api-responsiveness 回归测试（OpenSpec change: fix-api-event-loop-blocking）。

覆盖：
- api 层不存在模块级「无 await 的 async def」handler（事件循环阻塞隐患）
- golden-pit /status 缓存命中 / deadline 降级 / 后台回填
- golden-pit router handler 运行在线程池（普通 def）
- 外部数据源有界超时（ArkVol 默认 10s / Tushare timeout / 腾讯行情 8s / indices 单次 2s）
"""
import ast
import inspect
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
API_DIR = Path(__file__).resolve().parent.parent / "app" / "api"


# ── 事件循环解阻塞（D1）──

def test_no_module_level_no_await_async_handlers():
    """api 层不应存在模块级、无 await 且非生成器的 async def handler。"""
    offenders = []
    for f in sorted(API_DIR.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            has_await = any(isinstance(n, (ast.Await, ast.Yield, ast.YieldFrom)) for n in ast.walk(node))
            if not has_await:
                offenders.append(f"{f.name}:{node.lineno} async def {node.name}")
    assert not offenders, f"发现无 await 的 async def（同步重活应在线程池执行）: {offenders}"


def test_golden_pit_router_handlers_run_in_threadpool():
    """golden-pit 关键 handler 应为普通 def（FastAPI 线程池执行，不阻塞事件循环）。"""
    from app.api import golden_pit as gp

    CO_COROUTINE = 0x0080
    for name in (
        "get_golden_pit_status",
        "get_golden_pit_history",
        "get_golden_pit_snapshots",
        "get_tech_status_api",
        "get_dca_status",
    ):
        fn = getattr(gp, name)
        assert not (fn.__code__.co_flags & CO_COROUTINE), f"{name} 仍是 async def，会阻塞事件循环"


# ── golden-pit 缓存 / deadline（D2）──

def test_golden_pit_status_cache_hit(monkeypatch):
    from app.services.golden_pit_service import GoldenPitService

    svc = GoldenPitService(arkvol=None)
    calls = {"n": 0}

    def fake_db():
        calls["n"] += 1
        return {"as_of": "2026-08-26", "indices": [], "window_active": False}

    monkeypatch.setattr(svc, "_get_status_from_db", fake_db)
    monkeypatch.setattr(svc, "_get_status_from_api", lambda: None)

    first = svc.get_status(ttl=300, deadline=5.0)
    assert first["_source"] in ("db", "api")
    second = svc.get_status(ttl=300, deadline=5.0)
    assert second["_source"] == "cached"
    assert calls["n"] == 1, "缓存命中后不应重算"


def test_golden_pit_status_deadline_returns_stale_and_backfills(monkeypatch):
    from app.services.golden_pit_service import GoldenPitService

    svc = GoldenPitService(arkvol=None)

    def slow_db():
        time.sleep(1.0)
        return {"as_of": "2026-08-26", "indices": [], "window_active": False}

    monkeypatch.setattr(svc, "_get_status_from_db", slow_db)
    monkeypatch.setattr(svc, "_get_status_from_api", lambda: None)

    t0 = time.time()
    result = svc.get_status(ttl=300, deadline=0.3)
    elapsed = time.time() - t0
    assert elapsed < 0.8, f"deadline 未生效: {elapsed:.2f}s"
    assert result["_source"] in ("stale", "degraded") or result.get("_degraded_reason")

    # 后台计算完成后缓存应回填
    deadline_wait = time.time() + 5
    while time.time() < deadline_wait:
        with svc._status_lock:
            if svc._status_cache is not None:
                break
        time.sleep(0.05)
    assert svc._status_cache is not None, "后台计算未回填缓存"


# ── 外部调用有界超时（D3）──

def test_arkvol_timeout_default_is_10():
    from app.services.arkvol_service import ARKVOL_TIMEOUT

    assert ARKVOL_TIMEOUT == 10.0


def test_tushare_pro_api_passes_timeout():
    from app.core.trading import _api_config

    src = inspect.getsource(_api_config.get_tushare_pro)
    assert "timeout=" in src, "get_tushare_pro 未传入有界 timeout"


def test_xueqiu_get_stock_quote_has_timeout_param():
    src = (REPO_ROOT / "core" / "xueqiu_engine.py").read_text(encoding="utf-8")
    seg = src[src.index("def get_stock_quote"):src.index("def get_stock_quotes")]
    assert "timeout: int = 8" in seg
    assert "timeout=timeout" in seg


def test_market_indices_uses_bounded_quote_timeout():
    src = (API_DIR / "market.py").read_text(encoding="utf-8")
    seg = src[src.index("def get_market_indices"):]
    assert "timeout=2" in seg, "indices 串行抓取未收紧单次超时（总预算应 <=10s）"
