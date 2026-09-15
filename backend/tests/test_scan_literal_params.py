# -*- coding: utf-8 -*-
"""单测：jobs/scan_literal_params.py —— 函数字面量旋钮的识别规则（round 32，总账 §41）。

背景：`build_param_ledger.py --coverage` 只扫 env 键 + 模块级**大写**常量，
函数默认值/局部字面量（如 `def pick_v2(..., limit=2)`、`look = 8`）**不在任何清单里**。
本脚本补这一档，识别规则要可预期：策略语义名收、工程语义名不收、非字面量不收。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "jobs"))

import scan_literal_params as S  # noqa: E402


def _write(tmp_path, body):
    p = tmp_path / "wolf_demo.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_param_default_and_local_assignment_are_found(tmp_path):
    p = _write(tmp_path, (
        "def pick(theme='x', limit=2, look=8):\n"
        "    return theme, limit, look\n"
        "\n"
        "def run():\n"
        "    look = 8\n"
        "    return look\n"
    ))
    rows = S.scan_file(str(p))
    names = {(r["kind"], r["name"], r["value"]) for r in rows}
    assert ("默认值", "pick(limit=)", "2") in names
    assert ("默认值", "pick(look=)", "8") in names
    assert ("赋值", "look", "8") in names


def test_engineering_names_and_uppercase_constants_are_excluded(tmp_path):
    p = _write(tmp_path, (
        "def fetch(timeout=30, retries=3, port=8000, depth=0):\n"
        "    return timeout, retries, port, depth\n"
        "\n"
        "MAX_LEGS = 4\n"          # 模块级大写常量 → 属于另一档（build_param_ledger 负责）
        "ratio_x = 'not a number'\n"   # 非数字字面量 → 不收
        "x = 5\n"                 # 名字无语义 → 不收
    ))
    rows = S.scan_file(str(p))
    assert rows == [], rows


def test_registered_literals_are_visible_to_the_scanner():
    """§41 登记进 INVENTORY 的三条字面量，扫描器必须真的看得到（防"文档写了、工具看不见"）。"""
    found = []
    for f in S.iter_files(S.BUY_MODULES):
        found.extend(S.scan_file(f))
    names = " ".join("%s=%s" % (r["name"], r["value"]) for r in found)
    assert "pick_v2(limit=)=2" in names, names[:400]
    assert "scan_t_candidates(limit=)=20" in names, names[:400]
    assert "ai_select_and_build(select_limit=)=5" in names, names[:400]
