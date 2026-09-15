# -*- coding: utf-8 -*-
"""单测：验收页自检工具的**间接 env 键**识别（2026-09-15 round 40）。

起因：`wolf_theme_vol_fund.FAILCLOSED_ENV = "WOLF_FUND_GATE_FAILCLOSED"` + `os.getenv(FAILCLOSED_ENV, "1")`
这种间接写法只扫调用点会漏 → 自检报"文档提到、代码里没有"的**假阳性**（实测 1 个）。
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load():
    spec = importlib.util.spec_from_file_location(
        "vac_under_test", os.path.join(ROOT, "jobs", "verify_acceptance_claims.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_code_knobs_sees_indirect_env_constant(tmp_path):
    mod = _load()
    p = tmp_path / "m.py"
    p.write_text(
        "import os\n"
        "FAILCLOSED_ENV = 'WOLF_FUND_GATE_FAILCLOSED'\n"
        "NOTE = 'NOT_AN_ENV_KEY'\n"
        "def f():\n"
        "    return os.getenv(FAILCLOSED_ENV, '1')\n"
        "def g():\n"
        "    return os.getenv('WOLF_DIRECT', '0')\n", encoding="utf-8")
    knobs = mod.code_knobs([str(p)])
    assert "WOLF_FUND_GATE_FAILCLOSED" in knobs                      # 间接形态也认
    assert "WOLF_DIRECT" in knobs
    recs = [r for r in knobs["WOLF_FUND_GATE_FAILCLOSED"] if r[1] is not None]
    assert recs and str(recs[0][1]) == "1"                            # 默认值来自 getenv 调用点
    assert "NOT_AN_ENV_KEY" not in knobs


def test_real_repo_indirect_key_found():
    """真实回归：生产里那个间接键必须被扫到（否则验收页自检永远是 1 个假阳性）。"""
    mod = _load()
    f = os.path.join(ROOT, "apps", "main_line", "wolf_theme_vol_fund.py")
    knobs = mod.code_knobs([f])
    assert "WOLF_FUND_GATE_FAILCLOSED" in knobs
