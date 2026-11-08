# -*- coding: utf-8 -*-
"""LLM 跨臂共享缓存单测（同 as-of + 同 prompt 的答复跨臂复用）。"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "jobs") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "jobs"))

import bt_llm_replay as LR   # noqa: E402


class _R:
    status_code = 200
    text = '{"reply": "A"}'

    def json(self):
        return {"reply": "A"}


def test_跨臂共享_fresh也复用(tmp_path, monkeypatch):
    monkeypatch.setenv("WOLF_LLM_SHARED_CACHE", "1")
    monkeypatch.setenv("BT_LLM_SHARED", str(tmp_path / "shared"))
    monkeypatch.delenv("BT_LLM_FRESH", raising=False)
    # 第一次：录一条 → 同时写进共享目录
    rl = LR.LLMReplay(agent="t_leg", as_of="20260105", cache_dir=str(tmp_path / "run"), mode="record")
    rl.real_post = lambda url, **kw: _R()
    r1 = rl.post("http://x/chat", json={"message": "hello"})
    assert r1.json()["reply"] == "A" and rl.n_record == 1
    sf = tmp_path / "shared" / "t_leg" / "20260105.json"
    assert sf.exists() and json.loads(sf.read_text(encoding="utf-8"))["calls"][0]["reply"] == "A"
    # 第二臂：fresh=1（不读自己的缓存）+ 空的跑缓存 ⇒ 应命中共享缓存、**不外呼**
    monkeypatch.setenv("BT_LLM_FRESH", "1")
    rl2 = LR.LLMReplay(agent="t_leg", as_of="20260105", cache_dir=str(tmp_path / "run2"), mode="record")

    def _boom(url, **kw):
        raise AssertionError("命中共享缓存时不应外呼")

    rl2.real_post = _boom
    r2 = rl2.post("http://x/chat", json={"message": "hello"})
    assert r2.json()["reply"] == "A"
    assert rl2.n_shared_hit == 1 and rl2.n_record == 0
    assert rl2.summary()["shared_hit"] == 1


def test_开关关时不共享(tmp_path, monkeypatch):
    monkeypatch.setenv("WOLF_LLM_SHARED_CACHE", "0")
    monkeypatch.setenv("BT_LLM_SHARED", str(tmp_path / "shared"))
    monkeypatch.setenv("BT_LLM_FRESH", "1")
    rl = LR.LLMReplay(agent="t_leg", as_of="20260106", cache_dir=str(tmp_path / "run3"), mode="record")
    assert rl.shared_on is False
    rl.real_post = lambda url, **kw: _R()
    rl.post("http://x/chat", json={"message": "hi"})
    assert not (tmp_path / "shared" / "t_leg" / "20260106.json").exists()
    assert rl.n_shared_hit == 0


def test_同prompt不同asof不串(tmp_path, monkeypatch):
    monkeypatch.setenv("WOLF_LLM_SHARED_CACHE", "1")
    monkeypatch.setenv("BT_LLM_SHARED", str(tmp_path / "shared"))
    monkeypatch.delenv("BT_LLM_FRESH", raising=False)
    a = LR.LLMReplay(agent="t_leg", as_of="20260105", cache_dir=str(tmp_path / "r1"), mode="record")
    a.real_post = lambda url, **kw: _R()
    a.post("http://x/chat", json={"message": "same"})
    b = LR.LLMReplay(agent="t_leg", as_of="20260106", cache_dir=str(tmp_path / "r2"), mode="record")
    b.real_post = lambda url, **kw: _R()
    b.post("http://x/chat", json={"message": "same"})
    assert (tmp_path / "shared" / "t_leg" / "20260105.json").exists()
    assert (tmp_path / "shared" / "t_leg" / "20260106.json").exists()   # 按 as_of 分文件，互不串


def test_开关存在():
    drv = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert '("WOLF_LLM_SHARED_CACHE", "1")' in drv
    src = open(os.path.join(_ROOT, "jobs", "bt_llm_replay.py"), encoding="utf-8").read()
    assert "shared_file" in src and "_append_shared" in src and "跨臂复用" in src
