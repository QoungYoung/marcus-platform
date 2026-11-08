# -*- coding: utf-8 -*-
"""④ 主类规则的 allow **不再绝对定案**：仍过一次 dsh 主营校验，dsh 说"无关"就拒。

用户 2026-09-20：「只做第四步，不要用代码规则收紧」。
背景：主类词取自东财**概念标签**（非行业字段）⇒ 面板股 TCL科技(SZ000100) 因概念里含"半导体/国产芯片"
被判 主类=半导体 ⇒ allow ⇒ dsh 被短路（实测 dsh 对该票在 AI/算力/科技 与 半导体/芯片 下都判"无关"）。
"""
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


@pytest.fixture()
def M(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv("WOLF_MEMBER_CACHE_DIR", str(tmp_path))     # 隔离磁盘缓存
    monkeypatch.setenv("WOLF_THEME_MEMBER_CHECK", "1")
    monkeypatch.setenv("WOLF_MAIN_CLASS_RULE", "1")                 # 主类规则须开，否则 decide 恒为 defer
    # ⚠️ `theme_main_class.RULE_ON` 是**模块级**读 env 的：若它已被别的测试先导入，setenv 不生效
    #    ⇒ 必须直接 setattr（否则 decide 恒为 defer，本文件的 allow/reject 用例会假失败）。
    import theme_main_class as _T
    monkeypatch.setattr(_T, "RULE_ON", True, raising=False)
    import theme_member_llm as _M
    importlib.reload(_M)
    _M._CACHE.clear()
    return _M


def _fake_urlopen(reply):
    class _R:
        def __init__(self, payload):
            self._p = payload

        def read(self):
            return json.dumps({"reply": self._p}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda req, timeout=None: _R(reply)


CONCEPTS = ["半导体", "半导体概念", "国产芯片", "面板", "光学光电子", "显示技术"]


def test_默认不启用_主类allow仍然短路dsh(M, monkeypatch):
    monkeypatch.delenv("WOLF_MEMBER_DSH_VETO", raising=False)
    import importlib
    importlib.reload(M)
    monkeypatch.setattr(M.urllib.request, "urlopen", _fake_urlopen("无关"))
    keep, why = M.is_member("SZ000100", CONCEPTS, "AI/算力/科技")
    assert keep is True and why.startswith("主类:"), (keep, why)


def test_开启后_dsh可以否决主类的allow(M, monkeypatch):
    monkeypatch.setenv("WOLF_MEMBER_DSH_VETO", "1")
    import importlib
    importlib.reload(M)
    monkeypatch.setattr(M.urllib.request, "urlopen", _fake_urlopen("无关"))
    keep, why = M.is_member("SZ000100", CONCEPTS, "AI/算力/科技")
    assert keep is False, (keep, why)
    assert "主类allow" in why, why          # 理由里留下"主类原判 allow"，便于审计


def test_开启后_dsh说有关则放行(M, monkeypatch):
    monkeypatch.setenv("WOLF_MEMBER_DSH_VETO", "1")
    import importlib
    importlib.reload(M)
    monkeypatch.setattr(M.urllib.request, "urlopen", _fake_urlopen("有关"))
    keep, why = M.is_member("SZ000100", CONCEPTS, "AI/算力/科技")
    assert keep is True, (keep, why)


def test_主类reject仍然确定性拒_不问dsh(M, monkeypatch):
    monkeypatch.setenv("WOLF_MEMBER_DSH_VETO", "1")
    import importlib
    importlib.reload(M)
    called = {"n": 0}

    def _boom(req, timeout=None):
        called["n"] += 1
        raise AssertionError("主类 reject 不应调用 dsh")

    monkeypatch.setattr(M.urllib.request, "urlopen", _boom)
    keep, why = M.is_member("SH600018", ["体育产业", "港口航运"], "AI/算力/科技")
    assert keep is False and why.startswith("主类:"), (keep, why)
    assert called["n"] == 0


def test_校验总开关关掉时不受影响(M, monkeypatch):
    monkeypatch.setenv("WOLF_MEMBER_DSH_VETO", "1")
    monkeypatch.setenv("WOLF_THEME_MEMBER_CHECK", "0")
    import importlib
    importlib.reload(M)
    monkeypatch.setattr(M.urllib.request, "urlopen", _fake_urlopen("无关"))
    keep, _why = M.is_member("SZ000100", CONCEPTS, "AI/算力/科技")
    assert keep is True, "主营校验整体关闭时不该因为没有 dsh 而拒票"


def test_接线护栏_开关与pins(M, monkeypatch):  # noqa: D401
    src = (REPO_ROOT / "apps/main_line/theme_member_llm.py").read_text(encoding="utf-8")
    assert "WOLF_MEMBER_DSH_VETO" in src and "_tag(" in src
    pins = (REPO_ROOT / "jobs/bt_env_pins.sh").read_text(encoding="utf-8")
    assert "WOLF_MEMBER_DSH_VETO='1'" in pins
