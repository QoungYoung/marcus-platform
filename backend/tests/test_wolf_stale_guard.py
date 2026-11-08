# -*- coding: utf-8 -*-
"""陈旧环境产物护栏（内容级 as-of 校验）单测。"""
import json
import os
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "backend"), os.path.join(_ROOT, "apps", "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import wolf_stale_guard as G   # noqa: E402
from app.services import daily_decision as dd   # noqa: E402


def test_过期与未来都算陈旧(monkeypatch):
    monkeypatch.setattr(G, "GUARD_ON", True)
    assert G.is_stale({"as_of": "20200101"})[0] is True          # 过期产物（沙箱带着旧文件）
    assert G.is_stale({"as_of": "20260919"})[0] is False         # 真钟当日

    class _FakeDT(datetime):                                     # 模拟回放钉住时钟
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 5)

    monkeypatch.setattr(G, "datetime", _FakeDT)
    assert G.is_stale({"as_of": "20260915"})[0] is True          # 未来产物（9 月桩判 1 月）
    assert G.is_stale({"date": "2026-01-05"})[0] is False        # 当日（带横杠也认）
    assert G.is_stale({"as_of": "20251231"})[0] is False         # 5 天内
    assert G.is_stale({"as_of": "20200101"})[0] is True
    monkeypatch.setattr(G, "GUARD_ON", False)
    assert G.is_stale({"as_of": "20200101"})[0] is False         # 护栏关 ⇒ 不判


def test_load_json_陈旧返回空(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "GUARD_ON", True)
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"as_of": "20200101", "v": 1}), encoding="utf-8")
    assert G.load_json(str(p), tag="t") == {}                    # 陈旧 ⇒ 视为缺失
    p.write_text(json.dumps({"as_of": "20260919", "v": 1}), encoding="utf-8")
    assert G.load_json(str(p), tag="t") == {"as_of": "20260919", "v": 1}
    assert G.load_json(str(tmp_path / "nope.json")) == {}         # 读失败 ⇒ {}


def test_无日期不拦(monkeypatch):
    monkeypatch.setattr(G, "GUARD_ON", True)
    assert G.is_stale({})[0] is False
    assert "无日期" in G.is_stale({})[1]


def test_浪型live优先(tmp_path, monkeypatch):
    """A：带日期的是历史回填产物（实测 op=exit）⇒ live 优先后不再被它改档位。"""
    (tmp_path / "wave_state.json").write_text(
        json.dumps({"date": "2025-12-31", "operation": "build", "level": "d3"}), encoding="utf-8")
    (tmp_path / "wave_state_20251231.json").write_text(
        json.dumps({"date": "2025-12-31", "operation": "exit", "level": "d3"}), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(dd, "WAVE_LIVE_FIRST", True)
    w, src = dd._load_wave("20251231")
    assert w["operation"] == "build" and src["kind"] == "live_first"
    monkeypatch.setattr(dd, "WAVE_LIVE_FIRST", False)
    w2, _ = dd._load_wave("20251231")
    assert w2["operation"] == "exit"                             # 关开关 ⇒ 旧行为（读回填）


def test_驱动开关存在():
    _drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    for k in ("WOLF_WAVE_LIVE_FIRST", "WOLF_STALE_ARTIFACT_GUARD", "WOLF_MAIN_CLASS_RULE", "WOLF_EXEC_GATE"):
        assert '("%s", "1")' % k in _drv, k


def test_口径覆盖层_存在且被runner使用():
    """C：回测口径覆盖层必须在 env 文件**之后**生效，否则生产镜像快照会盖掉驱动默认。"""
    pins = os.path.join(_ROOT, "jobs", "bt_env_pins.sh")
    assert os.path.isfile(pins)
    body = open(pins, encoding="utf-8").read()
    assert "WOLF_THEME_VOLFUND_GATE='0'" in body          # 未拍板却被生产打开的闸
    for k in ("WOLF_MAIN_CLASS_RULE", "WOLF_WAVE_LIVE_FIRST", "WOLF_STALE_ARTIFACT_GUARD",
              "WOLF_EXEC_GATE", "WOLF_THEME_TIER_GATE", "WOLF_ASOF_FILE_FILTER"):
        assert "export %s=" % k in body, k
    for r in ("bt_size_run.sh", "bt_branch_run.sh"):
        s = open(os.path.join(_ROOT, "jobs", r), encoding="utf-8").read()
        assert ". jobs/bt_env_pins.sh" in s, r
        assert s.index('. "$ENVF"') < s.index(". jobs/bt_env_pins.sh"), r   # 顺序：env → pins
    drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert "_audit_env()" in drv and "[pins]" in drv
