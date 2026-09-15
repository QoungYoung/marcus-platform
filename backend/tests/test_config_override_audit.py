# -*- coding: utf-8 -*-
"""单测：jobs/audit_config_overrides.py —— 「生效值 vs 代码默认」自查（round 32，总账 §42）。

背景：参数有三层来源（DB 配置表 → 配置文件 → 代码内置默认），跑起来的**生效值**可能不等于代码默认。
实测：`profit_take.enabled` DB=true / 代码默认 false；`no_rebuild_symbols` DB 2 只 / 代码默认 1 只。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "jobs"))

import audit_config_overrides as A  # noqa: E402


def test_flatten_nested_dict():
    flat = A.flatten({"a": 1, "b": {"c": 2, "d": {"e": 3}}, "f": [1, 2]})
    assert flat == {"a": 1, "b.c": 2, "b.d.e": 3, "f": [1, 2]}


def test_diff_statuses():
    db = {"x": 1, "y": 2, "only_db": 3}
    code = {"x": 1, "y": 9, "only_code": 4}
    rows = {r["key"]: r["status"] for r in A.diff(db, code)}
    assert rows == {"x": "一致", "y": "DB覆盖", "only_db": "仅DB", "only_code": "仅代码"}


def test_read_module_dict_and_inline_default(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(
        "DEFAULTS = {'a': 1, 'b': {'c': 2}}\n"
        "\n"
        "def _cfg():\n"
        "    default = {'x': 1, 'y': {'z': 2}}\n"
        "    return default\n",
        encoding="utf-8")
    assert A.read_module_dict(str(p), "DEFAULTS") == {"a": 1, "b": {"c": 2}}
    assert A.read_inline_default(str(p)) == {"x": 1, "y": {"z": 2}}
    assert A.read_module_dict(str(p), "NOT_THERE") == {}


def test_real_containers_are_readable():
    """真实的两个容器必须读得到（防"文档写了、工具读不到"）。"""
    bp = A.read_module_dict(str(ROOT / "backend/app/services/t_build.py"), "BUILD_PARAMS_DEFAULT")
    assert len(bp) >= 50, len(bp)
    assert "no_rebuild_symbols" in bp
    wd = A.read_inline_default(str(ROOT / "backend/app/services/wolf_discipline.py"))
    assert "position_cap" in wd and "profit_take" in wd
    assert wd["profit_take"]["enabled"] is False        # 代码默认关；生产 DB 已开（见 §42）
    assert wd["position_cap"]["tier_targets"]["build"] == 75.0
