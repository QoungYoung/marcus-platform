# -*- coding: utf-8 -*-
"""③ 可见性测试（2026-09-14，买入层落差审计 B11 的落地件）。

要钉死的行为：
  1. pick_v2 正常 → picks 带 `pick_source='v2'`，写 `pick_health_<date>.json`，**不回落** legacy；
  2. pick_v2 抛错 → **默认**（WOLF_PICK_FAIL_MODE 未设）仍回落 legacy（= 旧行为，零变化），
     picks 带 `pick_source='legacy'`，且审计文件留 `last_error`；
  3. pick_v2 抛错 + `WOLF_PICK_FAIL_MODE=wait` → **不回落**、返回空（当天不布该主题的腿）+ 审计 `source='err_wait'`；
  4. `PICK_HEALTH` 行格式（theme/source/status/n/err）—— 让"选择层到底跑没跑"在日志里一眼可见；
  5. 删票黑名单按**文件路径**加载（生产实测 `from app.services...` 报 `No module named 'app'`）；
  6. 路径摘要 `legs_by_source` 能把 pathA / v2 / legacy 分开计数（此前无法区分）。
"""
import importlib.util
import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARM = os.path.join(ROOT, "jobs", "rotation_switch_arm.py")


def _load_arm(tmp_path, monkeypatch):
    """把 rotation_switch_arm 当模块加载（DATA 指向 tmp，避免碰生产 data/）。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("rsa_under_test", ARM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DATA = str(tmp_path)
    return mod


def _fake_v2(monkeypatch, behavior, status=None):
    """注入假的 wolf_confirm_pick 模块（confirm_pick 是函数内 import → 用 sys.modules 生效）。"""
    def pick_v2(theme, exclude=None, limit=2, concepts=None, status_out=None, **kw):
        if isinstance(status_out, dict) and status is not None:
            status_out.update(status)
        if behavior == "raise":
            raise RuntimeError("HTTP Error 307: Temporary Redirect")
        if behavior == "empty":
            return []
        return [{"symbol": "SH600000", "ts_code": "600000.SH", "reason": {}}]
    monkeypatch.setitem(sys.modules, "wolf_confirm_pick",
                        types.SimpleNamespace(pick_v2=pick_v2))


def test_v2_ok_tagged_and_no_legacy(tmp_path, monkeypatch, capsys):
    arm = _load_arm(tmp_path, monkeypatch)
    _fake_v2(monkeypatch, "ok", {"status": "ok"})
    called = {"legacy": 0}
    monkeypatch.setattr(arm, "_legacy_confirm_pick",
                        lambda *a, **k: called.__setitem__("legacy", called["legacy"] + 1) or [])
    picks = arm.confirm_pick("农业", exclude=set(), limit=2)
    assert [p["symbol"] for p in picks] == ["SH600000"]
    assert picks[0]["pick_source"] == "v2"
    assert called["legacy"] == 0, "v2 成功时不得走 legacy"
    err = capsys.readouterr().err
    assert "PICK_HEALTH theme=农业 source=v2 status=ok n=1" in err
    health = json.load(open(tmp_path / ("pick_health_%s.json" % arm._today()), encoding="utf-8"))
    assert health["themes"]["农业"]["source"] == "v2"


def test_v2_error_falls_back_legacy_by_default(tmp_path, monkeypatch, capsys):
    arm = _load_arm(tmp_path, monkeypatch)
    monkeypatch.delenv("WOLF_PICK_FAIL_MODE", raising=False)
    _fake_v2(monkeypatch, "raise")
    monkeypatch.setattr(arm, "_legacy_confirm_pick",
                        lambda *a, **k: [{"symbol": "SH600108", "ts_code": "600108.SH"}])
    picks = arm.confirm_pick("农业", exclude=set(), limit=2)
    assert [p["symbol"] for p in picks] == ["SH600108"]
    assert picks[0]["pick_source"] == "legacy"
    err = capsys.readouterr().err
    assert "WOLF_PICK_V2_ERR" in err and "PICK_HEALTH theme=农业 source=err" in err
    health = json.load(open(tmp_path / ("pick_health_%s.json" % arm._today()), encoding="utf-8"))
    assert health["last_error"]["source"] == "err"
    assert "307" in health["last_error"]["err"]


def test_v2_error_wait_mode_no_fallback(tmp_path, monkeypatch, capsys):
    arm = _load_arm(tmp_path, monkeypatch)
    monkeypatch.setenv("WOLF_PICK_FAIL_MODE", "wait")
    _fake_v2(monkeypatch, "raise")
    called = {"legacy": 0}
    monkeypatch.setattr(arm, "_legacy_confirm_pick",
                        lambda *a, **k: called.__setitem__("legacy", called["legacy"] + 1) or [])
    picks = arm.confirm_pick("农业", exclude=set(), limit=2)
    assert picks == []
    assert called["legacy"] == 0, "wait 模式不得回落 legacy 扫描序"
    err = capsys.readouterr().err
    assert "WOLF_PICK_V2_FAIL_WAIT" in err and "source=err_wait" in err


def test_v2_empty_ok_waits(tmp_path, monkeypatch):
    """位置闸否掉全部（status=ok）→ 空窗等待，不得回落（P0-4 语义不能被本轮改动破坏）。"""
    arm = _load_arm(tmp_path, monkeypatch)
    _fake_v2(monkeypatch, "empty", {"status": "ok"})
    called = {"legacy": 0}
    monkeypatch.setattr(arm, "_legacy_confirm_pick",
                        lambda *a, **k: called.__setitem__("legacy", called["legacy"] + 1) or [])
    assert arm.confirm_pick("农业", exclude=set(), limit=2) == []
    assert called["legacy"] == 0


def test_v2_datagap_falls_back(tmp_path, monkeypatch):
    """确认域缺失（no_universe/no_scored）→ 允许回落 legacy（数据问题不该全天不布腿）。"""
    arm = _load_arm(tmp_path, monkeypatch)
    _fake_v2(monkeypatch, "empty", {"status": "no_universe"})
    monkeypatch.setattr(arm, "_legacy_confirm_pick",
                        lambda *a, **k: [{"symbol": "SH600108", "ts_code": "600108.SH"}])
    picks = arm.confirm_pick("农业", exclude=set(), limit=2)
    assert picks and picks[0]["pick_source"] == "legacy"


def test_ticket_ban_loaded_by_file_path(tmp_path, monkeypatch):
    """删票黑名单必须能按绝对文件路径加载（生产 `No module named 'app'` 的根因）。"""
    arm = _load_arm(tmp_path, monkeypatch)
    import importlib.util as ilu
    path = os.path.join(ROOT, "backend", "app", "services", "wolf_ticket_ban.py")
    assert os.path.exists(path)
    spec = ilu.spec_from_file_location("wolf_ticket_ban_probe", path)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    mod.STATE_FILE = os.path.join(str(tmp_path), "wolf_ticket_ban.json")
    # 无文件 → 空黑名单、不抛异常（= 生产当前状态，修好导入也不改变行为）
    assert mod.banned_symbols(["stock"]) == {}


def test_source_label_separates_paths():
    """legs_by_source 的归类：pathA / v2 / legacy 必须能分开（否则等于没有可见性）。"""
    src = open(ARM, encoding="utf-8").read()
    assert 'PICK_PATH_SUMMARY legs_by_source=' in src
    assert '_b.get("pick_source") or ("pathA" if _b.get("side") in ("mainline", "defensive_resource") else "?")' in src
    assert 'pick_source", "v2"' in src
    assert 'pick_source", "legacy"' in src
