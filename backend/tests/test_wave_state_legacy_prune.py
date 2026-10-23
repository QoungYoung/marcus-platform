# -*- coding: utf-8 -*-
"""单测：dated 老档治理（2026-10-23）。

覆盖：①`prune_legacy_wave_state.select` 只挑 2025 年前的 dated 档（live/history 一律不碰 ✓）
②挪动 + INDEX 留痕 + 重复跑幂等 + `--restore` 可逆 ✓
③`regen_wave_state_ladder.root_files()` 默认跳过老档（`--include-legacy` 才放开 ✓）
④`--no-pg` 之外的另一个安全阀：双写范围只认 `data/wave_state_history/*` ✓
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, "jobs")):
    if p not in sys.path:
        sys.path.insert(0, p)

import prune_legacy_wave_state as P  # noqa: E402
import regen_wave_state_ladder as R  # noqa: E402


def _mkfile(path, op="side", source=None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    obj = {"date": "2016-11-30", "level": "d3", "sub_level": "3-3", "operation": op}
    if source:
        obj["source"] = source
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def _local(tmp_path):
    """造一份迷你 data/：live + 2 个老 dated（两种命名）+ 1 个新 dated + 1 个 history。"""
    d = tmp_path / "data"
    (d / "wave_state_history").mkdir(parents=True)
    _mkfile(str(d / "wave_state.json"), op="defense", source="wave_ladder")
    _mkfile(str(d / "wave_state_20161130.json"), op="build")          # 老档（无横线命名）
    _mkfile(str(d / "wave_state_2016-06-24.json"), op="exit")         # 老档（有横线命名）
    _mkfile(str(d / "wave_state_2026-10-20.json"), op="defense", source="wave_ladder")
    _mkfile(str(d / "wave_state_history" / "20261020.json"), op="defense", source="wave_ladder")
    return str(d)


def test_select_only_pre_2025_dated(tmp_path):
    data = _local(tmp_path)
    got = [os.path.basename(f) for f in P.select(data, "20250102")]
    assert got == ["wave_state_2016-06-24.json", "wave_state_20161130.json"], got
    # 分界可调 ✓
    assert len(P.select(data, "20270101")) == 3
    assert P.day_of(os.path.join(data, "wave_state_2016-06-24.json")) == "20160624"


def test_apply_index_idempotent_and_restore(tmp_path, monkeypatch, capsys):
    data = _local(tmp_path)
    ldir = os.path.join(data, P.LEGACY_DIR)

    monkeypatch.setattr(sys, "argv", ["x", "--data", data, "--apply"])
    assert P.main() == 0
    assert os.path.isdir(ldir)
    # 老档已离开 data/，新的 dated / live / history 原样不动 ✓
    assert not os.path.exists(os.path.join(data, "wave_state_20161130.json"))
    assert os.path.exists(os.path.join(data, "wave_state_2026-10-20.json"))
    assert os.path.exists(os.path.join(data, "wave_state_history", "20261020.json"))
    idx = json.load(open(os.path.join(ldir, P.INDEX), encoding="utf-8"))
    assert len(idx["moved"]) == 2
    assert {m["file"] for m in idx["moved"]} == {"wave_state_20161130.json", "wave_state_2016-06-24.json"}
    assert all(m["sha256"] and m["date8"] for m in idx["moved"])       # 留痕可核 ✓

    # 幂等：再跑一次不重复记账 ✓
    monkeypatch.setattr(sys, "argv", ["x", "--data", data, "--apply"])
    assert P.main() == 0
    idx = json.load(open(os.path.join(ldir, P.INDEX), encoding="utf-8"))
    assert len(idx["moved"]) == 2

    # 可逆 ✓
    monkeypatch.setattr(sys, "argv", ["x", "--data", data, "--restore", "--apply"])
    assert P.main() == 0
    assert os.path.exists(os.path.join(data, "wave_state_20161130.json"))
    assert os.path.exists(os.path.join(data, "wave_state_2016-06-24.json"))


def test_regen_skips_legacy_dated(tmp_path, monkeypatch):
    data = _local(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert data == os.path.join(str(tmp_path), "data")

    paths = [f.replace("\\", "/") for f, _ in R.root_files()]
    assert "data/wave_state_20161130.json" not in paths and "data/wave_state_2016-06-24.json" not in paths
    assert "data/wave_state_2026-10-20.json" in paths          # 2025 年后的照常重算 ✓
    assert "data/wave_state_history/20261020.json" in paths
    assert "data/wave_state.json" in paths

    paths_all = [f.replace("\\", "/") for f, _ in R.root_files(include_legacy=True)]
    assert "data/wave_state_20161130.json" in paths_all        # 显式放开 ✓


def test_regen_pg_scope_history_only():
    """双写范围：只有 history 档回写 PG（dated 老档/新档、_bt_* 一律不回写 ✓）。"""
    assert R._is_pg_target(os.path.join("data", "wave_state_history", "20261020.json")) is True
    assert R._is_pg_target(os.path.join("data", "wave_state_2026-10-20.json")) is False
    assert R._is_pg_target(os.path.join("data", "wave_state_20161130.json")) is False
    assert R._is_pg_target(os.path.join("data", "wave_state.json")) is False
    assert R._is_pg_target(os.path.join("data", "_bt_year", "2026-06-05", "wave_state.json")) is False


# ───────────── 2026-10-23「可审计重算」─────────────
def test_collect_changes_uses_product_op():
    """★ 回归锁：变更清单必须比**产物 op**（`to_wave_state()` 的结果）。

    踩过的坑：拿 `ev["operation"]`（rung 档）去比 ✗ —— 叠加档（W底/build、末段/exit、主升 3-3）
    会改写 operation，文件里存的是改写后的值 ⇒ 虚报变更（实测把 4/556 虚报成 51/568 = 9.1% ✗）。
    """
    plan = [
        # (file, ev, old_op, ws)：rung op=side，但产物被 main_up_spec 改写成 build ⇒ 不该报变更 ✓
        ("data/wave_state_history/20250514.json",
         {"date": "20250514", "operation": "side", "key": "w45_ok"},
         "build", {"operation": "build"}),
        # 真的变了才报 ✓
        ("data/wave_state_history/20250107.json",
         {"date": "20250107", "operation": "side", "key": "w4_adj"},
         "defense", {"operation": "t_only"}),
    ]
    got = R.collect_changes(plan)
    assert len(got) == 1, got
    f, day, old, new = got[0]
    assert os.path.basename(f) == "20250107.json" and (old, new) == ("defense", "t_only")


def test_data_snapshot_stable_and_sensitive(tmp_path, monkeypatch):
    """输入指纹：同内容 ⇒ 同指纹 ✓；任一输入文件变了 ⇒ 指纹变 ✓（12 位十六进制）。"""
    import wave_ladder as W

    for name, body in (("index_daily_000001_SH.json", "A"),
                       ("index_daily_399001_SZ.json", "B"),
                       ("index_daily_399006_SZ.json", "C")):
        (tmp_path / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    monkeypatch.setattr(W, "_SNAP_CACHE", {})

    snap1 = W.data_snapshot()
    assert len(snap1) == 12 and snap1.isalnum()
    assert W.data_snapshot() == snap1                       # 稳定 ✓（且有缓存 ✓）
    srcs = W.data_sources()
    assert {s["code"] for s in srcs} >= {"000001.SH", "399001.SZ", "399006.SZ"}
    assert all(len(s["digest"]) == 12 for s in srcs if s["exists"])

    (tmp_path / "index_daily_399001_SZ.json").write_text("B-changed", encoding="utf-8")
    monkeypatch.setattr(W, "_SNAP_CACHE", {})
    assert W.data_snapshot() != snap1                        # 换了一份行情 ⇒ 指纹必变 ✓


def test_artifact_carries_snapshot_and_inputs(tmp_path, monkeypatch):
    """产物要能自证「用哪份行情算的」：`data_snapshot` + 紧凑 `data_inputs`（文件名@摘要 ✓）。"""
    import wave_ladder as W

    for name in ("index_daily_000001_SH.json", "index_daily_399001_SZ.json", "index_daily_399006_SZ.json"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    monkeypatch.setattr(W, "_SNAP_CACHE", {})

    rows = [("20250602", 100.0, 101.0, 1e12)]
    ev = {"date": "20250602", "key": "w4_adj", "rung_index": 0, "operation": "t_only",
          "features": {"data_snapshot": W.data_snapshot()}}
    ws = W.to_wave_state(ev)
    assert ws["data_snapshot"] == W.data_snapshot()
    assert ws["data_inputs"] and all("@" in x for x in ws["data_inputs"])
    assert ws["generator_version"] == W.WAVE_LADDER_VERSION
