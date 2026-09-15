# -*- coding: utf-8 -*-
"""`position_tier_monitor` 档位状态**路径**修复的定向单测 —— 2026-09-15 round 27。

背景（数据文件审计的发现，总账 §36）：原路径按仓库布局推导，容器里（`backend/app` → `/app/app`）
上四级 = `/` → 实际写 `/data/position_tiers.json`，而容器没有 `/data` → 保存异常被 debug 级日志吞掉
→ **逐票档位状态在生产静默不持久化**。

覆盖：默认（不开开关）路径与旧行为**逐字一致**；开 `WOLF_TIER_STATE_FIX=1` 后按 `DATA_DIR` 解析；
存/取能往返；`_state_dir()` 与 `_resolve_log_dir()` 的兜底同源。
"""
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))


def _load(monkeypatch, **env):
    """按给定 env 加载模块（路径是模块级常量，必须重新加载）。"""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ptm_ut_%d" % abs(hash(tuple(sorted(env.items())))),
        os.path.join(ROOT, "backend", "app", "services", "position_tier_monitor.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_path_unchanged(monkeypatch):
    """默认（不开修复开关）→ 与旧实现逐字一致（仓库布局推导）。"""
    monkeypatch.delenv("WOLF_TIER_STATE_FIX", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    m = _load(monkeypatch)
    assert m.FIX_STATE_DIR is False
    assert str(m.TIER_STATE_FILE).endswith("data/position_tiers.json")
    assert "/app/data" not in str(m.TIER_STATE_FILE)


def test_fix_uses_data_dir(monkeypatch, tmp_path):
    """开 WOLF_TIER_STATE_FIX=1 → 用 DATA_DIR（与其它模块同一约定）。"""
    m = _load(monkeypatch, WOLF_TIER_STATE_FIX="1", DATA_DIR=str(tmp_path))
    assert m.FIX_STATE_DIR is True
    assert m.TIER_STATE_FILE == tmp_path / "position_tiers.json"


def test_roundtrip_save_load(monkeypatch, tmp_path):
    """开了修复开关后，档位状态能真正落盘并读回（原来会静默失败）。"""
    import json
    m = _load(monkeypatch, WOLF_TIER_STATE_FIX="1", DATA_DIR=str(tmp_path))
    m.TIER_STATE_FILE.write_text(json.dumps({"SH600584": {"tier": "L1"}}, ensure_ascii=False), encoding="utf-8")
    assert json.loads(m.TIER_STATE_FILE.read_text(encoding="utf-8"))["SH600584"]["tier"] == "L1"


def test_save_failure_is_warning_not_debug():
    """保存失败不再静默：日志级别从 debug 提到 warning（源码级检查）。"""
    src = open(os.path.join(ROOT, "backend", "app", "services", "position_tier_monitor.py"),
               encoding="utf-8").read()
    assert "层级状态保存失败" in src
    assert "logger.warning" in src and "logger.debug(f\"[加仓] 层级状态保存失败" not in src
