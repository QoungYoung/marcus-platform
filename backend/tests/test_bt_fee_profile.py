# -*- coding: utf-8 -*-
"""单测：回测撮合的费用口径（2026-09-15 参数化，用户拍板按 0.1292%/往返）。

要钉死的行为：
  1. 默认口径 = 现行 A 股费率拆到单边（买 0.000396 / 卖 0.000896）→ **往返 0.1292%**；
  2. `BT_FEE_PROFILE=legacy` 能回到旧行为（买 0.0005 / 卖 0.0015 = 往返 0.2%，含 0.1% 印花税）；
  3. env 可分别覆盖单边费率；显式入参优先级最高；
  4. 与评估链口径一致：`jobs/eval_aligned_package.FEE_ROUNDTRIP == 0.1292`（防止两套口径分叉）。
"""
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(ROOT, "backend"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BP = importlib.import_module("app.core.trading.backtest_paper")


def test_default_profile_is_current_a_share_rates(monkeypatch):
    for k in ("BT_FEE_PROFILE", "BT_COMMISSION_BUY", "BT_COMMISSION_SELL"):
        monkeypatch.delenv(k, raising=False)
    b, s, prof = BP.resolve_commission()
    assert abs(b - 0.000396) < 1e-12 and abs(s - 0.000896) < 1e-12
    assert abs((b + s) * 100 - 0.1292) < 1e-9
    assert prof == BP.FEE_PROFILE_DEFAULT
    assert BP.roundtrip_fee_pct() == 0.1292


def test_legacy_profile_restores_old_rates(monkeypatch):
    monkeypatch.setenv("BT_FEE_PROFILE", "legacy")
    b, s, _ = BP.resolve_commission()
    assert (b, s) == (BP.COMMISSION_BUY_LEGACY, BP.COMMISSION_SELL_LEGACY)
    assert BP.roundtrip_fee_pct() == 0.2


def test_env_and_explicit_override(monkeypatch):
    monkeypatch.setenv("BT_FEE_PROFILE", BP.FEE_PROFILE_DEFAULT)
    monkeypatch.setenv("BT_COMMISSION_BUY", "0.001")
    monkeypatch.setenv("BT_COMMISSION_SELL", "0.002")
    assert BP.resolve_commission()[:2] == (0.001, 0.002)
    assert BP.resolve_commission(buy=0.0, sell=0.0)[:2] == (0.0, 0.0)      # 显式入参最高优先
    monkeypatch.setenv("BT_FEE_PROFILE", "legacy")
    assert BP.resolve_commission(buy=0.0001)[:2] == (0.0001, 0.0015)       # 单边覆盖也生效


def test_matches_eval_chain_fee_constant():
    """评估链 `FEE_ROUNDTRIP=0.1292`（总账 §10）必须与撮合口径一致，防止又出现两套数字。"""
    src = open(os.path.join(ROOT, "jobs", "eval_aligned_package.py"), encoding="utf-8").read()
    assert "FEE_ROUNDTRIP = 0.1292" in src
    assert BP.roundtrip_fee_pct() == 0.1292


# ── LLM 录制/回放层（wave agent / 交易腿 agent 共用）────────────────────────
def _load_llm():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bt_llm_replay_under_test", os.path.join(ROOT, "jobs", "bt_llm_replay.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_llm_replay_miss_is_loud(tmp_path, monkeypatch):
    """replay 模式缺缓存 → **必须报错**（否则回测会静默少一层 agent 决策）。"""
    L = _load_llm()
    monkeypatch.setenv("BT_LLM_CACHE", str(tmp_path))
    r = L.LLMReplay(agent="wave", as_of="20260911", mode="replay")
    try:
        r.post("http://x/chat", json={"message": "hi"})
        assert False, "应当报错"
    except L.LLMReplayError as e:
        assert "replay 缺少缓存" in str(e)


def test_llm_record_then_replay_same_reply(tmp_path, monkeypatch):
    """record 落盘 → replay 命中同一条 reply，且**不再外呼**。"""
    L = _load_llm()
    monkeypatch.setenv("BT_LLM_CACHE", str(tmp_path))
    calls = {"n": 0}

    class _Resp:
        status_code = 200
        text = '{"reply": "{\\"operation\\": \\"side\\"}"}'

        def json(self):
            calls["n"] += 1
            return {"reply": '{"operation": "side"}'}

        def raise_for_status(self):
            pass

    L.LLMReplay.real_post = staticmethod(lambda url, **kw: _Resp())
    rec = L.LLMReplay(agent="wave", as_of="20260911", mode="record")
    out1 = rec.post("http://x/chat", json={"message": "P"}).json()
    # record：① 本层内部读一次拿 reply 落盘；② 调用方（agent）自己再读一次 → 真外呼只有 1 次
    assert out1["reply"] == '{"operation": "side"}' and rec.n_record == 1 and calls["n"] == 2

    rep = L.LLMReplay(agent="wave", as_of="20260911", mode="replay")
    out2 = rep.post("http://x/chat", json={"message": "P"}).json()
    assert out2 == out1 and rep.n_hit == 1 and calls["n"] == 2          # 外呼次数没再增加 ✔


def test_llm_replay_detects_prompt_drift(tmp_path, monkeypatch):
    """prompt 与录制时不一致（输入/PIT 口径漂移）→ 默认报错；BT_LLM_STRICT=0 只警告。"""
    L = _load_llm()
    monkeypatch.setenv("BT_LLM_CACHE", str(tmp_path))
    L.LLMReplay.real_post = staticmethod(lambda url, **kw: type("R", (), {
        "status_code": 200, "text": "{}", "json": lambda self=None: {"reply": "R1"},
        "raise_for_status": lambda self=None: None})())
    L.LLMReplay(agent="wave", as_of="20260911", mode="record").post("http://x", json={"message": "P1"})

    monkeypatch.setenv("BT_LLM_STRICT", "1")
    try:
        L.LLMReplay(agent="wave", as_of="20260911", mode="replay").post("http://x", json={"message": "P2"})
        assert False, "应当报错"
    except L.LLMReplayError as e:
        assert "prompt 与录制时不一致" in str(e)
    monkeypatch.setenv("BT_LLM_STRICT", "0")
    r = L.LLMReplay(agent="wave", as_of="20260911", mode="replay")
    assert r.post("http://x", json={"message": "P2"}).json()["reply"] == "R1" and r.warnings
