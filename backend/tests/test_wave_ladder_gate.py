# -*- coding: utf-8 -*-
"""单测：浪型链接入生产消费点的适配层（backend/app/services/wave_ladder_gate.py）。

要点：**默认关 ⇒ 生产逐字不变**；打开才接管；任何异常/取不到 ⇒ 原样返回（fail-open）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "backend"), os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wave_ladder as W                      # noqa: E402
from app.services import wave_ladder_gate as G  # noqa: E402


def test_switch_off_is_passthrough(monkeypatch):
    monkeypatch.delenv("WOLF_WAVE_LADDER", raising=False)
    monkeypatch.setattr(W, "ladder_operation", lambda asof=None: "defense")
    monkeypatch.setattr(W, "shadow_log", lambda *a, **k: None)
    assert G.adopt_wave_op("build") == "build"           # 生产 op 原样 ✓
    assert W.adopt_operation("build") == "build"


def test_switch_on_takes_over(monkeypatch):
    monkeypatch.setenv("WOLF_WAVE_LADDER", "1")
    monkeypatch.setattr(W, "ladder_operation", lambda asof=None: "exit")
    monkeypatch.setattr(W, "shadow_log", lambda *a, **k: None)
    assert G.adopt_wave_op("build") == "exit"            # 接管 ✓
    monkeypatch.setattr(W, "ladder_operation", lambda asof=None: None)
    assert G.adopt_wave_op("build") == "build"           # 算不出 ⇒ 回落 ✓


def test_fail_open_never_raises(monkeypatch):
    monkeypatch.setenv("WOLF_WAVE_LADDER", "1")
    monkeypatch.setattr(W, "ladder_operation", lambda asof=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert W.adopt_operation("side") == "side"           # 异常 ⇒ 原样 ✓
    monkeypatch.setattr(G, "_MOD", False)
    assert G.adopt_wave_op("side") == "side"             # 模块加载失败 ⇒ 原样 ✓


def test_shadow_log_written_and_deduped(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "LOG_DIR", str(tmp_path)) if hasattr(W, "LOG_DIR") else None
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.delenv("WOLF_WAVE_LADDER", raising=False)
    W.ADOPT_CACHE.clear()
    W.shadow_log("build", "defense")
    W.shadow_log("build", "defense")                     # 同键 ⇒ 只记一次 ✓
    W.shadow_log("build", "exit")                        # 换 op ⇒ 再记一条 ✓
    p = tmp_path / "wave_ladder_ab.jsonl"
    assert p.exists(), "影子日志没落盘"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 2, rows
    assert rows[0]["prod_op"] == "build" and rows[0]["ladder_op"] == "defense"
    assert rows[0]["adopted"] == "build" and rows[0]["switch"] == "shadow"


def test_consumer_sites_call_the_gate():
    """三个消费点都必须经过适配层（防止以后有人把这一行删掉）。"""
    root = ROOT
    for rel, needle in (("backend/app/services/p2_entry_gate.py", "adopt_wave_op"),
                        ("backend/app/services/wolf_253_build.py", "adopt_wave_op"),
                        ("apps/main_line/wave_alloc.py", "adopt_operation")):
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        assert needle in src, "%s 未接浪型链入口" % rel
