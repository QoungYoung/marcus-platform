# -*- coding: utf-8 -*-
"""Focused tests for jobs/build_pit_theme_members.py (no DB / no network)."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "jobs"))

import build_pit_theme_members as b  # noqa: E402


def test_norm_name_variants():
    assert b.norm_name("京东方Ａ") == "京东方A"
    assert b.norm_name("万  科Ａ") == "万科A"
    assert b.norm_name("*ST闻泰") == "闻泰"
    assert b.norm_name("ST华通") == "华通"
    assert b.norm_name("君实生物-U") == "君实生物"


def test_iter_leader_runs_marks_ffill():
    items = list(b.iter_leader_runs(
        ["20250101", "20250102", "20250103", "20250104"],
        ["A", "A", "B", ""],
    ))
    assert [x[1] for x in items] == ["A", "A", "B", ""]
    assert items[0][2] is True and items[0][3] == "20250101" and items[0][4] == 1
    assert items[1][2] is False and items[1][3] == "20250101" and items[1][4] == 2
    assert items[2][2] is True and items[2][3] == "20250103" and items[2][4] == 1


def _spec(name="T", level="parent", parent="T", requested=("C1",), matched=("C1",)):
    return b.ThemeSpec(name, level, parent, tuple(requested), tuple(matched))


def test_concept_hist_rows_no_future_and_ffill_source_date():
    hist = {
        "BK1": {
            "name": "C1",
            "dates": ["20250101", "20250102", "20250103"],
            "leader": ["A", "A", "B"],
            "coverage": 3,
        }
    }
    rows, unmatched, _ = b.build_concept_hist_rows(
        hist, [_spec()], {"A": "000001.SZ", "B": "000002.SZ"}, {},
    )
    assert not unmatched
    assert len(rows) == 3
    assert rows[0]["observation_type"] == "fresh"
    assert rows[1]["observation_type"] == "ffill_repeat"
    assert rows[1]["source_date"] == "20250101"
    assert rows[1]["as_of_date"] == "20250102"
    assert all(r["source_date"] <= r["as_of_date"] for r in rows)


def test_chain_map_gate_rejects_before_snapshot():
    cmap = {
        "date": "20250101",
        "mv_date": "20250101",
        "themes": [{
            "theme": "T",
            "segments": [{
                "concepts": ["C1"],
                "candidates": ["000001.SZ"],
                "leading_verified": [],
                "verified_all": [],
            }],
        }],
    }
    specs = [_spec()]
    rows, meta = b.build_chain_map_rows(
        [("data/chain_map_20250101.json", cmap)], specs, "20250601",
    )
    assert rows == []
    assert meta["excluded"] and "snapshot_gate" in meta["excluded"][0]["reason"]

    rows2, meta2 = b.build_chain_map_rows(
        [("data/chain_map_20250101.json", cmap)], specs, "20250101",
    )
    assert not meta2["excluded"]
    assert len(rows2) == 1
    assert rows2[0]["observation_type"] == "dated_artifact"
    assert rows2[0]["source_date"] == rows2[0]["as_of_date"] == "20250101"


def test_asof_members_strict_and_window():
    obs = [
        {"as_of_date": "20250101", "theme": "T", "ts_code": "000001.SZ",
         "concept_name": "C1", "source_date": "20250101",
         "observation_type": "fresh", "quality": "high"},
        {"as_of_date": "20250102", "theme": "T", "ts_code": "000002.SZ",
         "concept_name": "C2", "source_date": "20250102",
         "observation_type": "fresh", "quality": "high"},
    ]
    strict = b.asof_members(obs, "20250101", "T", max_staleness_days=0)
    assert [m["ts_code"] for m in strict["members"]] == ["000001.SZ"]

    window = b.asof_members(obs, "20250102", "T", max_staleness_days=5)
    assert {m["ts_code"] for m in window["members"]} == {"000001.SZ", "000002.SZ"}

    # 请求日之前的证据才可用；请求日之后的行绝不能进结果。
    before = b.asof_members(obs, "20241231", "T", max_staleness_days=999)
    assert before["n_members"] == 0


def test_validate_rejects_future_source(tmp_path):
    out = tmp_path
    spec = _spec()
    b.write_csv(out / "observations.csv", [{
        "as_of_date": "20250101", "theme": "T", "theme_level": "parent",
        "parent_theme": "T", "ts_code": "000001.SZ", "concept_name": "C1",
        "source": "concept_hist.leader", "source_date": "20250102",
        "observation_type": "fresh", "quality": "high", "notes": "",
    }], b.OBS_FIELDS)
    b.write_csv(out / "coverage.csv", [{
        "as_of_date": "20250101", "theme": "T", "theme_level": "parent",
        "parent_theme": "T", "n_members": 1, "n_fresh": 1, "n_carried": 0,
        "n_concepts": 1, "n_sources": 1, "status": "observed",
    }], b.COV_FIELDS)
    b.write_csv(out / "current_snapshot_not_pit.csv", [], b.SNAPSHOT_FIELDS)

    res = b.validate_outputs(str(out), [spec], "20250101", "strict")
    assert not res["passed"]
    assert res["checks"]["no_future_source_date"]["passed"] is False


@pytest.mark.skipif(not (REPO / "data" / "concept_hist.json").exists(),
                    reason="local concept_hist.json not present")
def test_real_data_smoke(tmp_path):
    manifest = b.build(out_dir=str(tmp_path), quiet=True)
    assert manifest["validation"]["passed"] is True
    assert manifest["coverage"]["parent_themes"]["n"] == 14
    assert manifest["coverage"]["subthemes"]["n"] == 49
    assert manifest["coverage"]["rows"]["observations"] > 1000
    assert manifest["coverage"]["dates"]["min"] <= "20250820"

    with open(tmp_path / "observations.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert all(r["source_date"] <= r["as_of_date"] for r in rows)
    assert all(not r["source"].startswith("current_snapshot") for r in rows)

    with open(tmp_path / "current_snapshot_not_pit.csv", encoding="utf-8", newline="") as f:
        snap = list(csv.DictReader(f))
    assert snap
    assert all(r["pit"] == "false" for r in snap)
