# -*- coding: utf-8 -*-
"""配置/浪型状态**路径解析**回归（2026-09-11 生产踩坑）。

坑：`current_operation()` 原来写 `os.path.join(os.environ.get("DATA_DIR", "data"), "wave_state.json")`，
生产容器里 **DATA_DIR 环境变量是 None** → 退化成**相对路径**：
`docker exec`（cwd=/app）读得到，**uvicorn 进程 cwd 不是 /app** → 读不到 →
`current_operation()` 返回 None → **仓位分档静默失效**（回落 total_max_pct=0 = 完全没有上限）。
修法：多候选（DATA_DIR → <workspace>/data → cwd/data → 相对 data），与 position_tier 同源。
"""
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_discipline as WD  # noqa: E402


def _reset():
    WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
    WD._CFG_DB_BREAK.update({"until": 0.0, "fails": 0, "warned": False})


class TestPathResolution:
    def test_no_data_dir_env_still_finds_via_workspace(self, tmp_path, monkeypatch):
        """核心回归：DATA_DIR 未设置时，也要能从 <workspace>/data 找到 wave_state。"""
        monkeypatch.delenv("DATA_DIR", raising=False)
        ws = tmp_path / "ws"
        (ws / "data").mkdir(parents=True)
        (ws / "data" / "wave_state.json").write_text(json.dumps({"operation": "defense"}), encoding="utf-8")
        monkeypatch.setenv("MARCUS_WORKSPACE", str(ws))
        monkeypatch.setattr(WD, "_ws_root", lambda: str(ws))
        assert WD.current_operation() == "defense"

    def test_data_dir_env_takes_priority(self, tmp_path, monkeypatch):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        for d, op in ((d1, "build"), (d2, "exit")):
            d.mkdir()
            (d / "wave_state.json").write_text(json.dumps({"operation": op}), encoding="utf-8")
        monkeypatch.setenv("DATA_DIR", str(d1))
        monkeypatch.setattr(WD, "_ws_root", lambda: str(d2))
        assert WD.current_operation() == "build"

    def test_missing_everywhere_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(WD, "_ws_root", lambda: str(tmp_path / "nowhere"))
        monkeypatch.chdir(tmp_path)
        assert WD.current_operation() is None

    def test_tier_lookup_uses_resolved_operation(self, tmp_path, monkeypatch):
        """端到端：解析到 defense → 分档目标 30%（而不是静默回落成"无上限"）。"""
        monkeypatch.delenv("DATA_DIR", raising=False)
        ws = tmp_path / "ws"
        (ws / "data").mkdir(parents=True)
        (ws / "data" / "wave_state.json").write_text(json.dumps({"operation": "defense"}), encoding="utf-8")
        monkeypatch.setenv("MARCUS_WORKSPACE", str(ws))
        monkeypatch.setattr(WD, "_ws_root", lambda: str(ws))
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "0")
        _reset()
        assert WD.tier_target_pct() == 30.0
        pf = {"cash": 50000.0, "total_asset": 100000.0, "positions": []}
        assert WD.position_cap(pf)["allowed"] is False      # 50% 超过 defense 档 30%

    def test_cfg_file_uses_same_candidates(self, tmp_path, monkeypatch):
        d = tmp_path / "d"
        d.mkdir()
        (d / "wolf_discipline.json").write_text(
            json.dumps({"position_cap": {"tier_targets": {"build": 66}}}), encoding="utf-8")
        monkeypatch.setenv("DATA_DIR", str(d))
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "0")
        _reset()
        assert WD.tier_target_pct("build") == 66.0
