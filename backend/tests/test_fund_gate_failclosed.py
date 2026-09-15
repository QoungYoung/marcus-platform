# -*- coding: utf-8 -*-
"""单测：资金门「数据读取失败 → 停止买入 + QQ 通知」（2026-09-15 用户拍板）。

覆盖：
  ① `check()` 在三处数据不足/陈旧分支上标注 `data_missing`（**返回值不变**，策略由调用方施加）；
  ② `theme_volfund_ok`：开关默认 1（fail-closed）→ 判不通过 + 通知（同键去重）；置 0 → 恢复放行；
  ③ `wolf_context._fund_data_missing`：同样按开关处置（默认 stop，0 时放行）。
"""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "backend"), str(ROOT / "apps" / "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

VF = importlib.import_module("wolf_theme_vol_fund")
WC = importlib.import_module("wolf_context")


@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(VF, "_send_qq", lambda msg: (sent.append(msg), True)[1], raising=False)
    return tmp_path, sent


def test_check_marks_data_missing_but_keeps_return_unchanged():
    ok, why, detail = VF.check([], [])                      # 量能序列不足
    assert ok is True and detail.get("data_missing") is True
    assert detail.get("missing_kind") == "vol_insufficient"

    amounts = [1.0] * 15 + [100.0] * 5                        # 近 5 日远高于 MA10 → 量能活跃
    ok, why, detail = VF.check(amounts, [1.0, 2.0])          # 资金序列不足
    assert detail.get("data_missing") is True
    assert detail.get("missing_kind") == "fund_insufficient"

    ok, why, detail = VF.check(amounts, [5.0] * 5)            # 资金序列全同（前值填充）
    assert detail.get("data_missing") is True
    assert detail.get("missing_kind") == "fund_stale"


def test_volfund_ok_failclosed_default_stops_and_notifies(tmp_data, monkeypatch):
    _, sent = tmp_data
    monkeypatch.delenv("WOLF_FUND_GATE_FAILCLOSED", raising=False)   # 默认 = 1
    assert VF.failclosed_enabled() is True
    monkeypatch.setattr(VF, "theme_amounts", lambda *a, **k: [])
    monkeypatch.setattr(VF, "theme_nets", lambda *a, **k: [])
    ok, why = VF.theme_volfund_ok("农业")
    assert ok is False and "fail-closed" in why
    assert len(sent) == 1 and "农业" in sent[0]
    ok2, _ = VF.theme_volfund_ok("农业")                     # 同 (日期,key) 去重 → 不再发
    assert ok2 is False and len(sent) == 1
    import datetime as _dt
    alerts = json.loads((tmp_data[0] / ("fund_gate_alerts_%s.json" % _dt.date.today().strftime("%Y%m%d"))).read_text(encoding="utf-8"))
    assert any(k.startswith("volfund:") for k in alerts)


def test_volfund_ok_legacy_when_switch_off(tmp_data, monkeypatch):
    monkeypatch.setenv("WOLF_FUND_GATE_FAILCLOSED", "0")
    monkeypatch.setattr(VF, "theme_amounts", lambda *a, **k: [])
    monkeypatch.setattr(VF, "theme_nets", lambda *a, **k: [])
    assert VF.failclosed_enabled() is False
    ok, why = VF.theme_volfund_ok("农业")
    assert ok is True and "放行" in why


def test_fund_data_missing_policy(tmp_data, monkeypatch):
    monkeypatch.delenv("WOLF_FUND_GATE_FAILCLOSED", raising=False)
    danger, why = WC._fund_data_missing("农业", "序列不足")
    assert danger is True and "fail-closed" in why                 # 默认：拦
    monkeypatch.setenv("WOLF_FUND_GATE_FAILCLOSED", "0")
    danger2, why2 = WC._fund_data_missing("农业", "序列不足")
    assert danger2 is False and "放行" in why2                      # 关掉开关 → 旧的 fail-open
