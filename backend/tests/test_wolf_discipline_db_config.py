# -*- coding: utf-8 -*-
"""狼大纪律配置**落库**（2026-09-11）单测。

背景：此前配置散在 `config/wolf_discipline.json` 与运行时实际读的
`DATA_DIR/wolf_discipline.json` **两份**，改一份漏一份 → "生效值与预期不符"（当天真实踩到）。
现在 Postgres 的 `wolf_discipline_config`(id=1, JSONB) 是唯一事实来源：
读序 = DB → 文件 → 内置默认；空表自动用文件播种；DB 不可用有**熔断**（不拖住监控线程）。
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_discipline as WD  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "0")      # 单测不打真库
    monkeypatch.delenv("WOLF_DISCIPLINE_CFG_TTL", raising=False)
    WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
    WD._CFG_DB_BREAK.update({"until": 0.0, "fails": 0, "warned": False})
    yield
    WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})


def write_file(tmp_path, obj):
    (tmp_path / "wolf_discipline.json").write_text(json.dumps(obj), encoding="utf-8")
    WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})


class TestReadOrder:
    def test_defaults_when_nothing_available(self, tmp_path, monkeypatch):
        """既无 DB 也无文件 → 用内置默认（本地仓库存在 config/ 兜底, 故显式屏蔽文件层）。"""
        monkeypatch.setattr(WD, "_cfg_file", lambda: {})
        WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
        assert WD.config_source() == "default"
        assert WD.tier_target_pct("build") == 75.0

    def test_file_overrides_defaults(self, tmp_path):
        write_file(tmp_path, {"position_cap": {"tier_targets": {"build": 88}}})
        assert WD.tier_target_pct("build") == 88.0
        assert WD.config_source() == "file"

    def test_section_merge_keeps_other_keys(self, tmp_path):
        write_file(tmp_path, {"position_cap": {"tier_targets": {"build": 88}}})
        c = WD._cfg()["position_cap"]
        assert c["tier_targets"]["build"] == 88.0
        assert c["tier_targets"]["defense"] == 30.0        # 未覆盖的档保持内置值
        assert c["tier_enabled"] is True

    def test_db_read_used_when_available(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "1")
        monkeypatch.setattr(WD, "_cfg_db_read", lambda: {"position_cap": {"tier_targets": {"build": 66}}})
        monkeypatch.setattr(WD, "_cfg_db_write", lambda *a, **k: True)
        WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
        assert WD.tier_target_pct("build") == 66.0
        assert WD.config_source() == "db"

    def test_autoseed_from_file_when_db_empty(self, tmp_path, monkeypatch):
        """空表 + 有文件 → 用文件播种进 DB（上线行为不变），来源标 db(seeded)。"""
        write_file(tmp_path, {"position_cap": {"tier_targets": {"build": 77}}})
        seen = {}
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "1")
        monkeypatch.setattr(WD, "_cfg_db_read", lambda: None)
        monkeypatch.setattr(WD, "_cfg_db_write", lambda cfg, updated_by="": seen.update(cfg=cfg, by=updated_by) or True)
        WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
        assert WD.tier_target_pct("build") == 77.0
        assert WD.config_source() == "db(seeded)"
        assert seen["by"] == "autoseed" and seen["cfg"]["position_cap"]["tier_targets"]["build"] == 77

    def test_no_seed_when_file_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(WD, "_cfg_file", lambda: {})       # 显式"无文件"
        called = []
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "1")
        monkeypatch.setattr(WD, "_cfg_db_read", lambda: None)
        monkeypatch.setattr(WD, "_cfg_db_write", lambda *a, **k: called.append(1) or True)
        WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
        WD._cfg()
        assert called == []                                  # 没文件就不写库(避免把默认值固化)

    def test_ttl_cache_avoids_repeat_db_calls(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "1")
        monkeypatch.setattr(WD, "_cfg_db_read", lambda: (calls.append(1), {"position_cap": {}})[1])
        monkeypatch.setattr(WD, "_cfg_db_write", lambda *a, **k: True)
        WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
        WD._cfg(); WD._cfg(); WD._cfg()
        assert len(calls) == 1                               # 60s 内只查一次


class TestCircuitBreaker:
    def test_db_failure_opens_breaker_and_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "1")
        write_file(tmp_path, {"position_cap": {"tier_targets": {"build": 71}}})
        import app.database as DB

        class _Boom:
            def __init__(self, *a, **k): raise RuntimeError("pg down")

        monkeypatch.setattr(DB, "SessionLocal", _Boom)
        WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
        assert WD.tier_target_pct("build") == 71.0            # 退回文件
        assert WD._CFG_DB_BREAK["fails"] == 1
        assert WD._CFG_DB_BREAK["until"] > 0                  # 熔断窗口已开

    def _touch_probe(self, monkeypatch):
        """把 SessionLocal 换成"一碰就记录"的桩：用来证明守卫生效（patch _cfg_db_read 会绕过守卫）。"""
        import app.database as DB
        touched = []

        class _Rec:
            def __init__(self, *a, **k): touched.append(1); raise RuntimeError("touched")
        monkeypatch.setattr(DB, "SessionLocal", _Rec)
        return touched

    def test_breaker_skips_db_within_window(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "1")
        write_file(tmp_path, {"position_cap": {"tier_targets": {"build": 72}}})
        WD._CFG_DB_BREAK.update({"until": 9e18, "fails": 1, "warned": True})
        touched = self._touch_probe(monkeypatch)
        WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
        assert WD.tier_target_pct("build") == 72.0
        assert touched == []                                  # 熔断中根本不碰 DB

    def test_switch_off_skips_db_entirely(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "0")
        write_file(tmp_path, {"position_cap": {"tier_targets": {"build": 73}}})
        touched = self._touch_probe(monkeypatch)
        WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
        assert WD.tier_target_pct("build") == 73.0
        assert touched == []


class TestSave:
    def test_save_writes_file_and_clears_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "1")
        monkeypatch.setattr(WD, "_cfg_db_write", lambda cfg, updated_by="": True)
        WD._CFG_CACHE.update({"at": 9e18, "cfg": {"x": 1}, "src": "db"})
        assert WD.save_cfg({"position_cap": {"tier_targets": {"build": 90}}}, updated_by="test") is True
        assert WD._CFG_CACHE["cfg"] is None                   # 缓存已失效
        on_disk = json.loads((tmp_path / "wolf_discipline.json").read_text(encoding="utf-8"))
        assert on_disk["position_cap"]["tier_targets"]["build"] == 90   # 文件同时落一份作离线兜底


class TestDeepMerge:
    """浅合并会把嵌套块整体替换 → 静默丢档位（单测抓到的真实缺陷）。"""

    def test_nested_partial_update_keeps_siblings(self):
        base = {"position_cap": {"tier_targets": {"build": 75, "defense": 30}, "enabled": True}}
        out = WD.deep_merge(base, {"position_cap": {"tier_targets": {"build": 88}}})
        assert out["position_cap"]["tier_targets"] == {"build": 88, "defense": 30}
        assert out["position_cap"]["enabled"] is True

    def test_base_not_mutated(self):
        base = {"a": {"b": 1}}
        WD.deep_merge(base, {"a": {"c": 2}})
        assert base == {"a": {"b": 1}}

    def test_scalar_overrides_dict(self):
        assert WD.deep_merge({"a": {"b": 1}}, {"a": 5}) == {"a": 5}
