# -*- coding: utf-8 -*-
"""回测「为什么没布腿/没成交」留痕单测（2026-10-15）。

背景（实测）：
  ① `jobs/gate_blocked.jsonl` 每行只有 `{"theme","why"}` ⇒ 累计 1305 条**无法按天归属** ✗；
  ② 产腿器（`bt_day_legs.py` / `bt_day_legs_switch.py`）的 stdout 只落在 `<root>/_summary/arm_<day>.log`
     ⇒ 沙箱当天目录里看不到闸门判定（`SKIP_THEME_NOT_BUYABLE` / `ENTRY_FILTER` / `WOLF_THEME_CAP` ✗），
     事后只能**手动重跑**才知道为什么 0 条腿 ✗；
  ③ `jobs/lowdip_block_report.py` 全仓库无调用者（孤儿工具）⇒ 报告从没生成过 ✗。

覆盖：`bt_day_legs._gb_rec` 的字段契约（**旧 theme/why 必须保留** ✓）与 `bt_days.run(tee_path=…)` 的
「双份日志 + rc/超时语义逐字不变」✓。
"""
import importlib.util
import os
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "jobs"), os.path.join(_ROOT, "backend")):
    if _p not in os.sys.path:
        os.sys.path.insert(0, _p)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gb_rec_新字段齐全且旧字段保留():
    """① 一行 gate_blocked 记录：theme/why（旧）＋ day/chain/symbol/leg_kind/ts/src（新）。"""
    M = _load("_bt_day_legs_t", "jobs/bt_day_legs.py")
    rec = M._gb_rec("新能源/电池", "板块资金危险", "bt_day_legs", leg_kind="buy_253/254", day="20260105")
    assert rec["theme"] == "新能源/电池" and rec["why"] == "板块资金危险"     # 旧字段**一字不改** ⇒ 旧读方不受影响
    assert rec["day"] == "20260105" and rec["leg_kind"] == "buy_253/254" and rec["src"] == "bt_day_legs"
    assert rec["chain"] == "新能源/电池" and rec["symbol"] is None          # 主题级拦因 ⇒ 无票、chain 回落 theme
    assert len(rec["ts"]) == 19 and rec["ts"][4] == "-"                     # YYYY-MM-DD HH:MM:SS
    assert isinstance(M._fund_src_note(), dict)                             # 资金门数据源留痕：取不到给空 dict（不抛）


def test_run_给tee时写两份且rc不变(tmp_path):
    """② `bt_days.run(tee_path=…)`：rc 原样返回；摘要日志与 tee 都不缺行、tee 首行是分段头。"""
    M = _load("_bt_days_t", "jobs/bt_days.py")
    tee = str(tmp_path / "legs_producer_20260105.log")
    log = str(tmp_path / "arm_20260105.log")
    cmd = ["/bin/sh", "-c", "echo OUT-1; echo ERR-1 >&2; exit 7"]
    rc, _dt = M.run(cmd, log, timeout=60, tee_path=tee)
    assert rc == 7
    body = open(log, "rb").read()
    assert b"OUT-1" in body and b"ERR-1" in body                           # stderr 也进来了（合并流）
    tee_txt = open(tee, encoding="utf-8").read().splitlines()
    assert tee_txt[0].startswith("==== ") and " ==== " in tee_txt[0]        # 分段头 ===== <脚本名> ====
    assert "OUT-1" in tee_txt and "ERR-1" in tee_txt


def test_run_不给tee时行为逐字不变(tmp_path):
    """② 未给 `tee_path`（其余子步骤）⇒ 走原 `subprocess.call` 路径，不产生任何额外文件。"""
    M = _load("_bt_days_t2", "jobs/bt_days.py")
    log = str(tmp_path / "switch_20260105.log")
    cmd = ["/bin/sh", "-c", "echo A; echo B; exit 0"]
    rc, _dt = M.run(cmd, log, timeout=60)
    assert rc == 0
    assert open(log, encoding="utf-8").read().split() == ["A", "B"]
    assert os.listdir(tmp_path) == ["switch_20260105.log"]                  # 没多写任何文件


def test_run_超时语义一致_KILL后抛TimeoutExpired(tmp_path):
    """② 超时两条路径都必须 kill 后抛 `TimeoutExpired`（旧 `subprocess.call(timeout=)` 语义）。"""
    M = _load("_bt_days_t3", "jobs/bt_days.py")
    with pytest.raises(subprocess.TimeoutExpired):
        M.run(["/bin/sh", "-c", "sleep 30"], str(tmp_path / "t1.log"), timeout=1)
    with pytest.raises(subprocess.TimeoutExpired):
        M.run(["/bin/sh", "-c", "sleep 30"], str(tmp_path / "t2.log"), timeout=1,
              tee_path=str(tmp_path / "legs_producer_20260105.log"))
