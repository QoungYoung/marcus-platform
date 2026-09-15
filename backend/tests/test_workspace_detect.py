# -*- coding: utf-8 -*-
"""`config.Settings._detect_workspace()` 容器兜底修复的定向单测 —— 2026-09-15 round 29。

背景（路径审计 §37）：`_detect_workspace()` 在容器里"上溯三级"= **`/`**（`backend/app` 挂在 `/app/app`），
生产只因 compose 注入 `MARCUS_WORKSPACE=/app` 才没炸 —— 一旦该环境变量丢失，
`workspace_path` 会变成 `/`，`data/memory/apps` 全部落到根目录（§36 那一族的根因）。

覆盖：
  ① 容器标记目录存在（`/app/app` + `/app/data`）→ 返回 `/app`；
  ② 标记不存在（宿主机）→ 返回仓库根，**与旧行为逐字一致**；
  ③ 环境变量优先：`MARCUS_WORKSPACE` 设了就不走探测（生产实际路径，行为零变化）。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings  # noqa: E402


def _fake_isdir(present):
    def _f(p):
        return str(p) in present
    return _f


def test_container_marker_returns_app(monkeypatch):
    """容器：/app/app 与 /app/data 都在 → /app（旧实现会给 /）。"""
    monkeypatch.setattr(os.path, "isdir", _fake_isdir({"/app/app", "/app/data"}))
    assert Settings._detect_workspace() == Path("/app")


def test_host_fallback_unchanged(monkeypatch):
    """宿主机：没有 /app/app → 保持原逻辑（仓库根），行为零变化。"""
    monkeypatch.setattr(os.path, "isdir", _fake_isdir(set()))
    assert Settings._detect_workspace() == ROOT


def test_partial_marker_still_falls_back(monkeypatch):
    """只满足一半标记（如有人手工建了 /app/app）→ 保守回退，不猜。"""
    monkeypatch.setattr(os.path, "isdir", _fake_isdir({"/app/app"}))
    assert Settings._detect_workspace() == ROOT


def test_env_wins_over_detection(monkeypatch):
    """环境变量优先：生产注入 MARCUS_WORKSPACE=/app → 完全不触发探测。"""
    monkeypatch.setenv("MARCUS_WORKSPACE", "/app")
    monkeypatch.setattr(os.path, "isdir", _fake_isdir(set()))  # 即便探测不可用
    s = Settings()
    assert s.MARCUS_WORKSPACE == "/app"
    assert s.workspace_path == Path("/app")
    assert s.data_dir == Path("/app/data")
