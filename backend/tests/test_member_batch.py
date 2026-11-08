# -*- coding: utf-8 -*-
"""主营校验**批量**判定 + **退避重试** + 负缓存时钟修正（2026-09-20）。

用户：「我们的主营校验能批量校验而不是每次只校验一个吗」「那加上超时重试呢」。
实测依据（详见 docs/wolf-buy-parameter-ledger.md 末节）：
  · 13101 串行：1 并发 2.1–6.7s；2 并发中位 5.5s；3 并发 9.6s；6 并发 9.1–13.0s ⇒ 延迟≈并发×单发
  · 批量：n=10/25/40 → 5.0s / 18.9s / 10.0s，全部行可解析 ⇒ 单票成本 4.5s → 0.25s
"""
import importlib
import json
import sys
import time
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


@pytest.fixture()
def ML(monkeypatch, tmp_path):
    """隔离缓存目录 + 打开主营校验（不发网络：调用方都把 _ask_llm 换掉）。"""
    monkeypatch.setenv("WOLF_MEMBER_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("WOLF_THEME_MEMBER_CHECK", "1")
    monkeypatch.setenv("WOLF_MAIN_CLASS_RULE", "0")      # 主类规则关 ⇒ needs_dsh 恒真（除白名单）
    import theme_main_class as _T
    monkeypatch.setattr(_T, "RULE_ON", False, raising=False)
    import theme_member_llm as _M
    importlib.reload(_M)
    _M._CACHE.clear()
    return _M


@pytest.fixture()
def MB(ML, monkeypatch):
    import theme_member_batch as _B
    importlib.reload(_B)
    return _B


# ─────────────────────────── 解析 ───────────────────────────

def test_解析各种行格式(MB):
    rep = "1 无关\n2. 有关\n3、无关\n4：有关\n5）无关\n6 无关\n"
    assert MB.parse_reply(rep, 6) == {1: "无关", 2: "有关", 3: "无关", 4: "有关", 5: "无关", 6: "无关"}


def test_解析容忍噪声与越界(MB):
    rep = "好的，结果如下：\n1 无关  （面板，非半导体）\n2 有关\n7 无关\n0 有关\n1 有关\n"
    got = MB.parse_reply(rep, 6)
    assert got == {1: "无关", 2: "有关"}, "越界(7/0)要丢、重复(1)取首次"
    assert "无关  （面板" not in got.values()


def test_解析缺行不补(MB):
    assert MB.parse_reply("1 无关\n3 有关\n", 3) == {1: "无关", 3: "有关"}


def test_提示词与单票同义且带编号清单(MB, ML):
    p = MB.build_prompt("半导体/芯片", [("SZ000100", ["半导体", "面板"]), ("SH600183", ["覆铜板"])])
    assert "2 只A股" in p and "1. 代码 SZ000100；概念：半导体/面板" in p and "2. 代码 SH600183" in p
    assert "只输出 2 行" in p and "序号 无关" in p
    single = ML.member_prompt("SZ000100", ["半导体", "面板"], "半导体/芯片")
    for frag in ("**主营业务**", "**完全无关**", "仅概念沾边不算有关"):
        assert frag in p and frag in single


# ─────────────────────── 批量 → 单票同一缓存 ───────────────────────

def test_批量结论写回单票缓存(MB, ML, monkeypatch):
    monkeypatch.setattr(ML, "_ask_llm", lambda prompt, timeout=None: "1 无关\n2 有关\n", raising=False)
    st = MB.check_batch([("SZ000100", ["半导体"]), ("SH600183", ["覆铜板"])], "半导体/芯片")
    assert st["calls"] == 1 and st["ok"] == 2 and st["miss"] == 0
    assert ML.cached("SZ000100", ["半导体"], "半导体/芯片") == (False, "无关")
    assert ML.cached("SH600183", ["覆铜板"], "半导体/芯片") == (True, "有关")
    # 单票路径 is_member 必须直接吃到（不再发网络）
    monkeypatch.setattr(ML, "_ask_llm", lambda prompt, timeout=None: (_ for _ in ()).throw(AssertionError("不该再问")), raising=False)
    assert ML.is_member("SZ000100", ["半导体"], "半导体/芯片")[0] is False


def test_解析不到的票不写缓存也不猜(MB, ML, monkeypatch):
    monkeypatch.setattr(ML, "_ask_llm", lambda prompt, timeout=None: "1 无关\n", raising=False)
    st = MB.check_batch([("SZ000100", ["半导体"]), ("SH600183", ["覆铜板"])], "半导体/芯片")
    assert st["ok"] == 1 and st["miss"] == 1
    assert ML.cached("SH600183", ["覆铜板"], "半导体/芯片") is None, "缺行不能猜结论"


def test_批量失败不写负缓存由单票兜底(MB, ML, monkeypatch):
    def _boom(prompt, timeout=None):
        raise TimeoutError("timed out")
    monkeypatch.setattr(ML, "_ask_llm", _boom, raising=False)
    st = MB.check_batch([("SZ000100", ["半导体"])], "半导体/芯片")
    assert st["err"] == 1 and st["ok"] == 0
    assert ML.cached("SZ000100", ["半导体"], "半导体/芯片") is None


def test_分块按batch_size切分(MB, ML, monkeypatch):
    calls = []
    monkeypatch.setattr(ML, "_ask_llm", lambda prompt, timeout=None: (calls.append(prompt) or
                        "\n".join("%d 有关" % i for i in range(1, 6))), raising=False)
    MB.check_batch([("SZ00000%d" % i, ["半导体"]) for i in range(1, 11)], "半导体/芯片", size=5)
    assert len(calls) == 2 and "5 只A股" in calls[0] and "5 只A股" in calls[1]


def test_白名单与主类拒不需要问dsh(MB, ML, monkeypatch):
    monkeypatch.setenv("WOLF_MEMBER_WHITELIST", "SH600658")
    importlib.reload(ML)
    monkeypatch.setattr(ML, "_ask_llm", lambda prompt, timeout=None: (_ for _ in ()).throw(AssertionError("不该问")), raising=False)
    monkeypatch.setattr(ML, "needs_dsh", lambda s, c, t: False, raising=False)
    st = MB.check_batch([("SH600658", ["半导体"])], "半导体/芯片")
    assert st["calls"] == 0 and st["ok"] == 1


# ─────────────────────────── 重试 ───────────────────────────

def test_重试两次后成功(ML, monkeypatch):
    monkeypatch.setenv("WOLF_MEMBER_RETRY", "2")
    monkeypatch.setenv("WOLF_MEMBER_RETRY_BACKOFF", "0")
    importlib.reload(ML)
    n = {"i": 0}

    class _R:
        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        n["i"] += 1
        if n["i"] < 3:
            raise TimeoutError("timed out")
        return _R(json.dumps({"reply": "有关"}).encode())

    monkeypatch.setattr(ML.urllib.request, "urlopen", _urlopen, raising=False)
    assert ML._ask_llm("x") == "有关"
    assert n["i"] == 3 and ML.stats()["retry"] == 2


def test_重试用尽才抛(MB, ML, monkeypatch):
    monkeypatch.setenv("WOLF_MEMBER_RETRY", "1")
    monkeypatch.setenv("WOLF_MEMBER_RETRY_BACKOFF", "0")
    importlib.reload(ML)
    n = {"i": 0}

    def _boom(req, timeout=None):
        n["i"] += 1
        raise OSError("HTTP Error 405: Method Not Allowed")

    monkeypatch.setattr(ML.urllib.request, "urlopen", _boom, raising=False)
    with pytest.raises(Exception):
        ML._ask_llm("x")
    assert n["i"] == 2, "重试 1 次 + 首发 1 次 = 2 次请求"
    # 单票路径：异常 ⇒ fail-open + 负缓存 + **留痕**
    out = ML.is_member("SZ000100", ["半导体"], "半导体/芯片")
    assert out[0] is True and out[1].startswith("ERR")
    assert ML.stats()["err"] >= 1 and ML.stats()["warn"] >= 1


def test_退避序列带抖动且递增(ML, monkeypatch):
    monkeypatch.setenv("WOLF_MEMBER_RETRY", "2")
    monkeypatch.setenv("WOLF_MEMBER_RETRY_BACKOFF", "2")
    importlib.reload(ML)
    slept = []
    monkeypatch.setattr(ML._time, "sleep", lambda s: slept.append(s), raising=False)
    monkeypatch.setattr(ML.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(TimeoutError("t")), raising=False)
    with pytest.raises(Exception):
        ML._ask_llm("x")
    assert len(slept) == 2
    assert 2.0 * 0.6 <= slept[0] <= 2.0 * 1.4, "第一次退避 ~2s（±40%）"
    assert 4.0 * 0.6 <= slept[1] <= 4.0 * 1.4, "第二次退避 ~4s（±40%）"


# ──────────────────── 负缓存时钟（回放重跑陷阱）────────────────────

def test_负缓存_at在未来视为过期(MB, ML, monkeypatch):
    """回放把进程时钟钉在回放日 ⇒ 重跑更早日期时旧 neg 条目 age<0，必须重试而不是继续 fail-open。"""
    p = ML._ckey("SZ000100", "半导体/芯片", ["半导体"])
    import os
    os.makedirs(ML.CACHE_DIR, exist_ok=True)
    with open(os.path.join(ML.CACHE_DIR, p + ".json"), "w", encoding="utf-8") as f:
        json.dump({"keep": True, "reply": "ERR timed out", "neg": True,
                   "at": time.time() + 86400 * 14, "err": "TimeoutError"}, f)
    assert ML.cached("SZ000100", ["半导体"], "半导体/芯片") is None, "未来时间戳的 neg 必须视为过期"


def test_负缓存_tTL内仍生效(MB, ML):
    p = ML._ckey("SZ000100", "半导体/芯片", ["半导体"])
    import os
    os.makedirs(ML.CACHE_DIR, exist_ok=True)
    with open(os.path.join(ML.CACHE_DIR, p + ".json"), "w", encoding="utf-8") as f:
        json.dump({"keep": True, "reply": "ERR timed out", "neg": True, "at": time.time(), "err": "x"}, f)
    assert ML.cached("SZ000100", ["半导体"], "半导体/芯片") == (True, "neg_cache")


# ─────────────────────────── 接线与 pins ───────────────────────────

def test_批量默认关_库内不改行为(ML, monkeypatch):
    import theme_member_batch as _B
    importlib.reload(_B)
    monkeypatch.delenv("WOLF_MEMBER_BATCH", raising=False)
    assert _B.enabled() is False


def test_pins钉住批量与重试():
    txt = (REPO_ROOT / "jobs" / "bt_env_pins.sh").read_text(encoding="utf-8")
    for frag in ("WOLF_MEMBER_BATCH='1'", "WOLF_MEMBER_BATCH_SIZE='40'", "WOLF_MEMBER_RETRY='1'",
                 "WOLF_MEMBER_RETRY_BACKOFF='2'", "WOLF_MEMBER_LLM_TIMEOUT='20'", "WOLF_MEMBER_NEG_TTL='120'",
                 "WOLF_MEMBER_BATCH_TIMEOUT='60'", "WOLF_MEMBER_BATCH_RETRY='1'", "WOLF_MEMBER_FAIL_FAST='1'"):
        assert frag in txt, "pins 少了 %s" % frag


def test_leg_gate_批量优先后单票兜底(monkeypatch):
    import leg_gate as _LG
    import theme_member_batch as _B
    import theme_member_llm as _M
    importlib.reload(_LG)
    calls = {"batch": [], "single": 0}
    monkeypatch.setattr(_B, "enabled", lambda: True, raising=False)
    monkeypatch.setattr(_B, "warm", lambda its, th, size=None: (calls["batch"].append((th, len(its))) or
                        {"asked": len(its), "ok": len(its), "miss": 0, "calls": 1, "err": 0}), raising=False)
    monkeypatch.setattr(_M, "cached", lambda s, c, t: (True, "有关"), raising=False)

    def _pf(items, workers=1):
        calls["single"] += len(items)
        return len(items)

    monkeypatch.setattr(_M, "prefetch", _pf, raising=False)
    n = _LG.prefetch([("SZ000100", "半导体/芯片"), ("SH600183", "AI/算力/科技")])
    assert calls["batch"] == [("半导体/芯片", 1), ("AI/算力/科技", 1)], "按主题分批"
    assert calls["single"] == 0, "批量已覆盖 ⇒ 不该再走单票"
    assert n == 2


# ─────────────── 缓存 key 与 prompt 对齐（2026-09-20 修重复判定）───────────────

def test_key只由prompt可见的概念决定(MB, ML):
    """key 必须由"prompt 真正展示的那部分概念"派生：前 12 位相同 ⇒ 同 key（同 prompt ⇒ 判一次）。"""
    a = ["c%d" % i for i in range(1, 13)] + ["x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8"]
    b = ["c%d" % i for i in range(1, 13)] + ["y1", "y2"]          # 前 12 位同、其后不同
    assert ML._ckey("SZ000100", "半导体/芯片", a) == ML._ckey("SZ000100", "半导体/芯片", b)
    # 而且这两者的 prompt 确实一模一样 ⇒ 合并 key 是**正确**的（不是省事）
    assert ML.member_prompt("SZ000100", a, "半导体/芯片") == ML.member_prompt("SZ000100", b, "半导体/芯片")


def test_前12位不同则key不同(MB, ML):
    a = ["c%d" % i for i in range(1, 13)]
    b = ["c%d" % i for i in range(1, 12)] + ["zz"]
    assert ML._ckey("SZ000100", "半导体/芯片", a) != ML._ckey("SZ000100", "半导体/芯片", b)


def test_票与主题仍进key(MB, ML):
    cs = ["半导体", "面板"]
    assert ML._ckey("SZ000100", "半导体/芯片", cs) != ML._ckey("SZ000100", "AI/算力/科技", cs)
    assert ML._ckey("SZ000100", "半导体/芯片", cs) != ML._ckey("SH600183", "半导体/芯片", cs)


def test_批量结论与单票命中同一个key(MB, ML, monkeypatch):
    """批量写回的 key 必须与单票查的 key 一致（否则批量等于白干）。"""
    full = ["c%d" % i for i in range(1, 16)]
    monkeypatch.setattr(ML, "_ask_llm", lambda prompt, timeout=None: "1 无关\n", raising=False)
    MB.check_batch([("SZ000100", full)], "半导体/芯片")
    assert ML.cached("SZ000100", full, "半导体/芯片") == (False, "无关")
    # 概念表只差 12 位之后（模拟 PG vs 沙箱库差异）也应命中同一缓存
    shorter = full[:12] + ["别的概念"]
    assert ML.cached("SZ000100", shorter, "半导体/芯片") == (False, "无关"), "同 prompt 必须同 key"


def test_prompt里出现的就是key用的那段(MB, ML):
    """不变量：prompt 文本可由 (symbol, theme, _prompt_concepts(concepts)) 完全复现。"""
    cs = ["半导体", "国产芯片"] + ["t%d" % i for i in range(20)]
    p = ML.member_prompt("SZ000100", cs, "半导体/芯片")
    assert ML._prompt_concepts(cs) in p
    assert "t1" in p and "t%d" % 20 not in p, "prompt 只展示前 12 个概念"
