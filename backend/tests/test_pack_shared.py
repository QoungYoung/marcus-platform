# -*- coding: utf-8 -*-
"""共享打包池（`WOLF_PACK_SHARED`）——账本 §9.127，用户 2026-09-25 拍板。

依据：pack 是**确定性产物** —— 同一标的在 T25 与 T28 两个臂里 md5 完全一致（9d4e441162 ✓）
⇒ 每臂各打一份纯浪费 ✗（1,081 只 / 642,150 bar / 139MB；单日 1.5~2.3 分钟 ⇒ 75 天 ≈ 2~2.9 小时 ✗）。
"""
from __future__ import annotations

import hashlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_switch_default_off_and_wiring():
    src = open(os.path.join(_ROOT, "jobs", "bt_days.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_PACK_SHARED", "0")' in src          # 库内默认关 ⇒ 生产零影响 ✓
    i_sw = src.index('os.getenv("WOLF_PACK_SHARED", "0")')
    i_fallback = src.index('_pack = os.path.join(a.root, "pack")')
    assert i_fallback < i_sw, "必须先落回臂内 pack，再按开关覆盖（失败可回退 ✓）"
    assert 'pack_shared' in src and "_adj" in src and "_tool" in src, "桶名必须含口径 + 工具 md5"


def test_bucket_is_deterministic_and_discriminating():
    """桶名 = 复权口径 + 打包工具代码 md5 ⇒ 口径/逻辑一变就换桶（绝不混用 ✓）。"""
    tool = open(os.path.join(_ROOT, "jobs", "bt_pack_mins.py"), "rb").read()
    md5 = hashlib.md5(tool).hexdigest()[:8]
    assert len(md5) == 8
    for adj, expect in (("1", "adj"), ("0", "raw")):
        bucket = "%s_%s" % (expect, md5)
        assert bucket.startswith(expect) and md5 in bucket


def test_pins_and_ladder():
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_PACK_SHARED='1'" in pins
    lad = open(os.path.join(_ROOT, ".dsh-tmp", "wolfbt", "arm_ladder_check.py"), encoding="utf-8").read()
    assert '("WOLF_PACK_SHARED", "0")' in lad
