# -*- coding: utf-8 -*-
"""member 预热的三条加速开关（2026-09-20）。

背景实测：0212 的 `legs_switch.预热 = 1215.66s`，其中 **18 次逐票兜底 × ~42s ≈ 756s 全是白等**——
批量因排队超时后，逐票再问同一个串行服务几乎必然再超时（每票还 ×2 次尝试 + 退避）。

三个新旋钮（**库内默认＝现状**，只在 pins 里给新臂开）：
  · `WOLF_MEMBER_BATCH_TIMEOUT`（默认=单票超时）：批量专属超时预算
  · `WOLF_MEMBER_BATCH_RETRY`（默认 0）：整批重试次数（一次请求覆盖 N 只，比逐票划算）
  · `WOLF_MEMBER_FAIL_FAST`（默认 0）：批量失败后**不再逐票兜底**，整批写负缓存
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


@pytest.fixture()
def ML(monkeypatch, tmp_path):
    monkeypatch.setenv("WOLF_MEMBER_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("WOLF_THEME_MEMBER_CHECK", "1")
    monkeypatch.setenv("WOLF_MAIN_CLASS_RULE", "0")
    import theme_main_class as _T
    monkeypatch.setattr(_T, "RULE_ON", False, raising=False)
    import theme_member_llm as _M
    importlib.reload(_M)
    _M._CACHE.clear()
    return _M


@pytest.fixture()
def MB(ML):
    import theme_member_batch as _B
    importlib.reload(_B)
    return _B


class TestKnobs(ML.__class__ if False else object):
    def test_batch_timeout_default_and_override(self, ML, monkeypatch):
        monkeypatch.delenv("WOLF_MEMBER_BATCH_TIMEOUT", raising=False)
        assert ML.batch_timeout() == ML.TIMEOUT                 # 默认＝现状（单票超时）
        monkeypatch.setenv("WOLF_MEMBER_BATCH_TIMEOUT", "60")
        assert ML.batch_timeout() == 60.0
        monkeypatch.setenv("WOLF_MEMBER_BATCH_TIMEOUT", "abc")  # 写坏 ⇒ 退回默认
        assert ML.batch_timeout() == ML.TIMEOUT

    def test_fail_fast_default_off(self, ML, monkeypatch):
        monkeypatch.delenv("WOLF_MEMBER_FAIL_FAST", raising=False)
        assert ML.fail_fast() is False
        for v in ("1", "true", "YES", "on"):
            monkeypatch.setenv("WOLF_MEMBER_FAIL_FAST", v)
            assert ML.fail_fast() is True, v
        for v in ("0", "false", "", "off"):
            monkeypatch.setenv("WOLF_MEMBER_FAIL_FAST", v)
            assert ML.fail_fast() is False, v

    def test_batch_retry_default_zero(self, ML, monkeypatch):
        monkeypatch.delenv("WOLF_MEMBER_BATCH_RETRY", raising=False)
        assert ML.batch_retry() == 0
        monkeypatch.setenv("WOLF_MEMBER_BATCH_RETRY", "2")
        assert ML.batch_retry() == 2
        monkeypatch.setenv("WOLF_MEMBER_BATCH_RETRY", "abc")
        assert ML.batch_retry() == 0


class TestFailFast:
    ITEMS = [("SH600000", ["银行"]), ("SH600001", ["银行"]), ("SH600002", ["银行"])]

    def test_off_keeps_per_symbol_fallback(self, ML, MB, monkeypatch):
        """默认（fail-fast 关）：批量失败 ⇒ 不写负缓存、failed_syms 为空 ⇒ 调用方照旧逐票兜底。"""
        monkeypatch.setenv("WOLF_MEMBER_FAIL_FAST", "0")
        monkeypatch.setattr(MB.ML, "_ask_llm", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("x")))
        st = MB.check_batch(self.ITEMS, "银行")
        assert st["err"] == 3 and st["failed_syms"] == []
        assert ML.cached("SH600000", ["银行"], "银行") is None

    def test_on_negcaches_whole_batch_and_reports(self, ML, MB, monkeypatch):
        """fail-fast 开：批量失败 ⇒ 整批写负缓存（fail-open 放行）+ 回报 failed_syms。"""
        monkeypatch.setenv("WOLF_MEMBER_FAIL_FAST", "1")
        monkeypatch.setenv("WOLF_MEMBER_BATCH_RETRY", "0")
        monkeypatch.setattr(MB.ML, "_ask_llm", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("x")))
        st = MB.check_batch(self.ITEMS, "银行")
        assert st["err"] == 3
        assert sorted(st["failed_syms"]) == ["SH600000", "SH600001", "SH600002"]
        for sym, cs in self.ITEMS:
            hit = ML.cached(sym, cs, "银行")
            assert hit is not None and hit[0] is True          # 负缓存 ⇒ 放行
            assert "neg" in str(hit[1])

    def test_batch_retry_attempts_are_batched(self, ML, MB, monkeypatch):
        """整批重试：1 次失败 + 1 次成功 ⇒ 只发 2 次请求（不是 N 次），且全部落缓存。"""
        monkeypatch.setenv("WOLF_MEMBER_BATCH_RETRY", "1")
        calls = {"n": 0}

        def _fake(prompt, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("first")
            return "1.有关\n2.无关\n3.有关"
        monkeypatch.setattr(MB.ML, "_ask_llm", _fake)
        st = MB.check_batch(self.ITEMS, "银行")
        assert calls["n"] == 2 and st["ok"] == 3

    def test_batch_timeout_passed_through(self, ML, MB, monkeypatch):
        monkeypatch.setenv("WOLF_MEMBER_BATCH_TIMEOUT", "45")
        seen = {}
        monkeypatch.setattr(MB.ML, "_ask_llm",
                            lambda p, timeout=None: (seen.update({"t": timeout}), "1.有关\n2.有关\n3.有关")[1])
        MB.check_batch(self.ITEMS, "银行")
        assert seen["t"] == 45.0


class TestPrewarmSkipsFailed:
    def test_prefetch_excludes_failfast_symbols(self, ML, monkeypatch):
        """leg_gate.prefetch：fail-fast 回报的票不再进入单票兜底。"""
        import leg_gate as LG
        import theme_member_batch as _B
        monkeypatch.setenv("WOLF_MEMBER_BATCH", "1")
        monkeypatch.setattr(_B, "enabled", lambda: True, raising=False)
        monkeypatch.setattr(_B, "warm",
                            lambda items, theme, size=None: {"asked": len(items), "ok": 0, "miss": len(items),
                                                             "calls": 1, "err": len(items),
                                                             "failed_syms": [s for s, _ in items]},
                            raising=False)
        import theme_member_llm as _M
        seen = []
        monkeypatch.setattr(_M, "prefetch", lambda left, workers=None: (seen.extend(left), 0)[1], raising=False)
        n = LG.prefetch([("SH600000", "银行"), ("SH600001", "银行")])
        assert seen == []            # 全被 fail-fast 覆盖 ⇒ 一次单票兜底都不发
        assert n == 2
