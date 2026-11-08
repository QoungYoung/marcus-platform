# -*- coding: utf-8 -*-
"""触发静默单测（无T仓可卖 / G8 上影线命中 ⇒ 当日该腿不再生成重复触发）。"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "backend"))

from app.services import t_trigger_mute as TM   # noqa: E402


def test_登记与查询(tmp_path, monkeypatch):
    monkeypatch.setattr(TM, "ON", True)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    TM._CACHE.update({"path": None, "mtime": None, "data": {}})
    TM._LOGGED.clear()
    assert TM.is_muted("SH603660", "custom_vwap_sell") is False
    TM.mute("SH603660", "custom_vwap_sell", "无T仓可卖（底仓保护）")
    assert TM.is_muted("SH603660", "custom_vwap_sell") is True
    assert TM.is_muted("SH603660", "high_sell") is False          # 只静默命中过的那条腿
    assert TM.is_muted("SZ000636", "custom_vwap_sell") is False   # 只静默命中过的标的
    f = list(tmp_path.glob("trigger_mute_*.json"))[0]
    data = json.loads(f.read_text(encoding="utf-8"))
    assert "SH603660|custom_vwap_sell" in data
    assert "无T仓" in data["SH603660|custom_vwap_sell"]["why"]


def test_幂等(tmp_path, monkeypatch):
    monkeypatch.setattr(TM, "ON", True)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    TM._CACHE.update({"path": None, "mtime": None, "data": {}})
    TM.mute("SH603660", "high_sell", "G8 上影线停机")
    TM.mute("SH603660", "high_sell", "G8 上影线停机")            # 第二次不应报错/覆盖
    f = list(tmp_path.glob("trigger_mute_*.json"))[0]
    assert len(json.loads(f.read_text(encoding="utf-8"))) == 1


def test_开关关时不动(tmp_path, monkeypatch):
    monkeypatch.setattr(TM, "ON", False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    TM._CACHE.update({"path": None, "mtime": None, "data": {}})
    TM.mute("SH603660", "custom_vwap_sell", "x")
    assert TM.is_muted("SH603660", "custom_vwap_sell") is False
    assert not list(tmp_path.glob("trigger_mute_*.json"))


def test_已接线且默认库内关():
    src = open(os.path.join(_ROOT, "backend/app/services/t_monitor.py"), encoding="utf-8").read()
    assert src.count("t_trigger_mute") >= 4                       # 2 处查询 + 2 处登记
    assert "G8 上影线停机: " in src and "无T仓可卖（底仓保护）" in src
    drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert '("WOLF_TRIGGER_MUTE", "1")' in drv
    assert 'os.getenv("WOLF_TRIGGER_MUTE"' in open(
        os.path.join(_ROOT, "backend/app/services/t_trigger_mute.py"), encoding="utf-8").read()
