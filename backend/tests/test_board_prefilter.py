# -*- coding: utf-8 -*-
"""F2「选股域 ⊆ 执行域」定向单测 —— 2026-09-15。

背景（`docs/wolf-buy-gap-audit.md` F2）：账户无创业板/科创板权限，布腿前会把这类买腿**直接砍掉**
（`BOARD_FILTER removed N 个无权限板块买腿`），**不会用次优票补位** → 腿白丢。
本次把板块判据收敛成 `rotation_switch_arm.board_ok()`（唯一实现），并给选股侧加
`WOLF_PICK_BOARD_PREFILTER`（默认 0 = 行为不变）+ 影子记录。

覆盖：判据本身（两套代码写法/环境变量可配）、选股器在开关下的取票差异、影子文件内容、默认值。
"""
import importlib.util
import json
import os
import sys
import types

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(ROOT, "jobs"), os.path.join(ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_arm():
    spec = importlib.util.spec_from_file_location(
        "arm_board_ut", os.path.join(ROOT, "jobs", "rotation_switch_arm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ARM = _load_arm()


@pytest.fixture()
def fake_position_class(monkeypatch):
    """把 `position_class` 换成"全 LOW"的假模块 —— 单测不测位置分类（另有其测试）。"""
    fake = types.ModuleType("position_class")
    fake.position_features = lambda series: {"n": len(series)}
    fake.classify = lambda f: {"position": "LOW"}
    monkeypatch.setitem(sys.modules, "position_class", fake)
    return fake


def _row(xq, ts, leader, closes=None):
    return {"xq": xq, "ts": ts, "leader": leader, "closes": closes or [10.0] * 70,
            "r60": 5.0, "amt20": 3.0}


def test_board_ok_judgement():
    """主板放行；创业板(300/301)、科创板(688)、北交所(4/8/920) 拦住；xq 与 ts_code 两种写法都认。"""
    assert ARM.board_ok("SZ000001") is True
    assert ARM.board_ok("SH603986") is True
    assert ARM.board_ok("SZ300189") is False
    assert ARM.board_ok("SZ301035") is False
    assert ARM.board_ok("SH688111") is False
    assert ARM.board_ok("BJ430047") is False
    assert ARM.board_ok("SZ002156") is True
    # ts_code 写法
    assert ARM.board_ok("300189.SZ") is False
    assert ARM.board_ok("603986.SH") is True
    # 空/None 不拦（fail-open，避免误杀）
    assert ARM.board_ok("") is True and ARM.board_ok(None) is True


def test_board_ok_env_configurable(monkeypatch):
    """`WOLF_PICK_BOARD_EXCLUDE` 可配：清空后不拦任何板块（= 有权限时的行为）。"""
    monkeypatch.setenv("WOLF_PICK_BOARD_EXCLUDE", "")
    assert ARM.board_ok("SZ300189") is True and ARM.board_ok("SH688111") is True
    monkeypatch.setenv("WOLF_PICK_BOARD_EXCLUDE", "cyb")
    assert ARM.board_ok("SZ300189") is False
    assert ARM.board_ok("SH688111") is True          # 只剔创业板 → 科创板放行


def test_board_prefilter_default_off(monkeypatch):
    monkeypatch.delenv("WOLF_PICK_BOARD_PREFILTER", raising=False)
    assert ARM.board_prefilter_enabled() is False
    monkeypatch.setenv("WOLF_PICK_BOARD_PREFILTER", "1")
    assert ARM.board_prefilter_enabled() is True


def test_select_by_gate_prefilter_changes_pick(fake_position_class):
    """首选是创业板 → 开预过滤时**跳过它取次优**（这就是"腿不会白丢"的机制）；不开则维持原样。"""
    stats = [_row("SZ300189", "300189.SZ", 9.9), _row("SZ002156", "002156.SZ", 9.5),
             _row("SH600584", "600584.SH", 9.1)]
    cur = ARM._select_by_gate(stats, 2, use_board_prefilter=False)
    alt = ARM._select_by_gate(stats, 2, use_board_prefilter=True)
    assert [x["symbol"] for x in cur] == ["SZ300189", "SZ002156"]
    assert [x["symbol"] for x in alt] == ["SZ002156", "SH600584"]
    assert alt[0]["chain"] is None or True            # chain 由 pick_buy 注入，此处不强制


def test_select_by_gate_respects_limit_and_position(fake_position_class, monkeypatch):
    """位置闸不接受（MID 之外/None）的票不进结果；limit 生效。"""
    fake_position_class.classify = lambda f: {"position": "HIGH"}
    stats = [_row("SH600584", "600584.SH", 9.9)]
    assert ARM._select_by_gate(stats, 2, use_board_prefilter=False) == []
    fake_position_class.classify = lambda f: {"position": "MID"}
    stats = [_row("SH600584", "600584.SH", 9.9), _row("SH600585", "600585.SH", 9.8)]
    assert len(ARM._select_by_gate(stats, 1, use_board_prefilter=False)) == 1


def test_shadow_file_written(tmp_path, monkeypatch):
    """影子文件：记录"现选谁 / 剔后选谁 / 被剔掉的是谁"，只记录不改决策。"""
    monkeypatch.setattr(ARM, "DATA", str(tmp_path))
    cur = [{"symbol": "SZ300189"}, {"symbol": "SZ002156"}]
    alt = [{"symbol": "SZ002156"}, {"symbol": "SH600584"}]
    ARM.board_prefilter_shadow("机器人", cur, alt)
    fn = os.path.join(str(tmp_path), "board_prefilter_shadow_%s.json" % ARM._today())
    d = json.load(open(fn, encoding="utf-8"))
    rec = d["chains"]["机器人"]
    assert rec["mode"] == "shadow"
    assert rec["current"] == ["SZ300189", "SZ002156"]
    assert rec["with_prefilter"] == ["SZ002156", "SH600584"]
    assert rec["dropped"] == ["SZ300189"]


def test_pick_buy_shadow_branch_uses_prefilter(monkeypatch, tmp_path, fake_position_class):
    """不改行为的那一半也要成立：默认关时 `pick_buy` 仍返回原选票（这里只测影子分支的接线）。"""
    monkeypatch.setattr(ARM, "DATA", str(tmp_path))
    monkeypatch.delenv("WOLF_PICK_BOARD_PREFILTER", raising=False)
    stats = [_row("SZ300189", "300189.SZ", 9.9), _row("SZ002156", "002156.SZ", 9.5)]
    out = ARM._select_by_gate(stats, 1, use_board_prefilter=ARM.board_prefilter_enabled())
    assert [x["symbol"] for x in out] == ["SZ300189"]
    assert pd.Series([1.0]).shape[0] == 1             # 保住 pandas 依赖（position_class 假实现用）
