# -*- coding: utf-8 -*-
"""G5（2026-09-14）：方向高低位 → 去弱留强分两套相反规则。

狼大 2026-09-04 15:07：「之前的高位方向 大科技这些…卖强的 留弱的 拉升后都走；
之前的低位方向 AI软券商军工这些…找辨识度最高老龙头埋伏，强的留 弱的丢」。
"""
import importlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))


@pytest.fixture()
def pd_mod():
    return importlib.import_module("position_discipline")


@pytest.fixture()
def dp(tmp_path, monkeypatch):
    """指向临时分类结果文件的方向模块。"""
    p = tmp_path / "position_class_result.json"
    # 概念名取自 fusion_mainline.THEME_CONCEPTS（机器人/智能制造），保证能映射上
    data = {
        "BK9001.DC": {"name": "机器人概念", "position": "HIGH", "trend": "UP", "op": "t_only"},
        "BK9002.DC": {"name": "人形机器人", "position": "HIGH", "trend": "UP", "op": "t_only"},
        "BK9003.DC": {"name": "工业母机", "position": "MID", "trend": "UP", "op": "hold"},
        "BK9004.DC": {"name": "白酒", "position": "LOW", "trend": "DOWN", "op": "defense"},
        "BK9005.DC": {"name": "免税概念", "position": "LOW", "trend": "DOWN", "op": "defense"},
        "BK9006.DC": {"name": "创新药", "position": "MID", "trend": "UP", "op": "hold"},
        "BK9007.DC": {"name": "半导体概念", "position": "MID", "trend": "UP", "op": "hold"},
    }
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("WOLF_DIRECTION_POS_FILE", str(p))
    monkeypatch.setenv("WOLF_DIRECTION_POS_MAX_AGE_H", "0")   # 关掉新鲜度护栏（测试用临时文件）
    monkeypatch.delenv("WOLF_DIRECTION_POS", raising=False)
    monkeypatch.delenv("WOLF_POSITION_DISC_HIGH", raising=False)
    mod = importlib.import_module("app.services.wolf_direction_position")
    mod._CACHE.update({"t": 0.0, "mtime": None, "by_name": {}, "by_code": {}})
    return mod


def test_direction_position_high_by_enrichment(dp):
    """机器人/智能制造：2/7 HIGH = 28.6% ≥ 2×基准(28.6%) → HIGH（基准=2/7）。"""
    r = dp.direction_position("机器人/智能制造")
    assert r["verdict"] == "HIGH"
    assert r["n_high"] == 2
    assert "基准" in r["reason"]


def test_direction_position_low_and_unknown(dp):
    assert dp.direction_position("消费/内需")["verdict"] == "LOW"       # 2 个 LOW 概念
    assert dp.direction_position("不存在方向/XYZ")["verdict"] == "UNKNOWN"
    assert dp.direction_position("")["verdict"] == "UNKNOWN"


def test_direction_position_stale_is_unknown(dp, monkeypatch):
    monkeypatch.setenv("WOLF_DIRECTION_POS_MAX_AGE_H", "0.0001")
    r = dp.direction_position("机器人/智能制造")
    assert r["verdict"] in ("HIGH", "UNKNOWN")     # 超龄护栏只在真的超龄时降级
    monkeypatch.setenv("WOLF_DIRECTION_POS_MAX_AGE_H", "-1")
    assert dp.direction_position("机器人/智能制造")["verdict"] == "HIGH"   # ≤0 视为关闭护栏


def test_direction_position_switch_off(dp, monkeypatch):
    monkeypatch.setenv("WOLF_DIRECTION_POS", "0")
    assert dp.direction_position("机器人/智能制造")["verdict"] == "UNKNOWN"


def test_branch_switch_off(dp, monkeypatch):
    monkeypatch.setenv("WOLF_POSITION_DISC_HIGH", "0")
    r = dp.branch_of("机器人/智能制造")
    assert r["branch"] == "low_sell_weak" and r["reason"] == "WOLF_POSITION_DISC_HIGH=0"


def test_select_weak_high_branch_sells_strongest(pd_mod):
    """高位方向：卖最强（升序会卖最弱 → 必须相反），且**每组必留一只**。"""
    items = [{"symbol": "B", "rebound": 6.0, "dir_pos": "HIGH"},
             {"symbol": "D", "rebound": 9.5, "dir_pos": "HIGH"},
             {"symbol": "A", "rebound": 1.0, "dir_pos": "LOW"},
             {"symbol": "C", "rebound": 4.5, "dir_pos": "LOW"}]
    r = pd_mod.select_weak(items)
    picked = [(s["symbol"], s["branch"]) for s in r["sells"]]
    assert ("D", "high_sell_strong") in picked          # 高位分支卖最强
    assert ("B", "high_sell_strong") not in picked      # 该方向最弱者保留
    assert r["branches"]["high_sell_strong"]["picked"] == 1
    assert r["branches"]["low_sell_weak"]["picked"] == 1


def test_select_weak_low_branch_unchanged(pd_mod):
    """低位/中位/未知方向 = 原 P1-6 行为：卖最弱者。"""
    items = [{"symbol": "A", "rebound": 1.0, "dir_pos": "LOW"},
             {"symbol": "B", "rebound": 6.0, "dir_pos": "MID"},
             {"symbol": "C", "rebound": 2.0, "dir_pos": "UNKNOWN"},
             {"symbol": "D", "rebound": 8.0, "dir_pos": None}]
    r = pd_mod.select_weak(items)
    assert [s["symbol"] for s in r["sells"]] == ["A", "C"]
    assert all(s["branch"] == "low_sell_weak" for s in r["sells"])


def test_select_weak_global_switch_restores_legacy(pd_mod, monkeypatch):
    items = [{"symbol": "B", "rebound": 6.0, "dir_pos": "HIGH"},
             {"symbol": "D", "rebound": 9.5, "dir_pos": "HIGH"},
             {"symbol": "A", "rebound": 1.0, "dir_pos": "LOW"},
             {"symbol": "C", "rebound": 4.5, "dir_pos": "LOW"}]
    monkeypatch.setenv("WOLF_POSITION_DISC_HIGH", "0")
    r = pd_mod.select_weak(items)
    assert [s["symbol"] for s in r["sells"]] == ["A", "C"]
    assert all(s["branch"] == "low_sell_weak" for s in r["sells"])


def test_select_weak_group_needs_internal_spread(pd_mod):
    """分支内分化不足 → 该分支不动（高位 5.0/5.5 差 0.5% < 3%）。"""
    items = [{"symbol": "X", "rebound": 5.0, "dir_pos": "HIGH"},
             {"symbol": "Y", "rebound": 5.5, "dir_pos": "HIGH"},
             {"symbol": "Z", "rebound": 1.0, "dir_pos": "MID"},
             {"symbol": "W", "rebound": 1.2, "dir_pos": "MID"}]
    r = pd_mod.select_weak(items)
    assert r["sells"] == []
    assert "high_sell_strong" in (r["skip"] or "")


def test_directive_mentions_both_branches(pd_mod):
    sells = [{"symbol": "D", "rebound": 9.5, "branch": "high_sell_strong"},
             {"symbol": "A", "rebound": 1.0, "branch": "low_sell_weak"}]
    txt = pd_mod.directive(sells, 8.5)
    assert "卖强留弱" in txt and "留强丢弱" in txt
    assert "2026-09-04" in txt and "2026-04-23" in txt
