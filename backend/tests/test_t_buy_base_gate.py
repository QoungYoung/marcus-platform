# -*- coding: utf-8 -*-
"""正T买腿前置：无底仓不发腿（狼大 2026-08-25「我今天没抄底 没有资格T」）。

实测依据：drabj13 全窗 89 条无底仓正T买腿 T+3 −2.66%(t=−4.20)，
         66 条有底仓正T买腿 T+3 +0.71% —— 见 wolf_t_base_gate 模块头。
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_t_base_gate as G  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WOLF_T_BUY_REQUIRE_BASE", raising=False)


def test_开关默认关_逐字旧行为():
    assert not G.enabled()
    assert G.allow(True) == (True, "")
    ok, why = G.allow(False)
    assert ok and why == "", "库内默认关 ⇒ 无底仓也放行（生产零影响）"


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_开关打开才生效(monkeypatch, val):
    monkeypatch.setenv("WOLF_T_BUY_REQUIRE_BASE", val)
    assert G.enabled()
    assert G.allow(True) == (True, ""), "有底仓必须放行"
    ok, why = G.allow(False)
    assert not ok and "没有资格T" in why


@pytest.mark.parametrize("val", ["0", "false", "off", "", "  "])
def test_关闭值(monkeypatch, val):
    monkeypatch.setenv("WOLF_T_BUY_REQUIRE_BASE", val)
    assert not G.enabled()
    assert G.allow(False)[0] is True


def test_原因引用语料():
    os.environ["WOLF_T_BUY_REQUIRE_BASE"] = "1"
    try:
        _ok, why = G.allow(False)
        assert "2026-08-25" in why and "做T的仓位变加仓" in why
    finally:
        os.environ.pop("WOLF_T_BUY_REQUIRE_BASE", None)
