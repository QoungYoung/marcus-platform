# -*- coding: utf-8 -*-
"""主营判定的**并发/超时/重试**口径（2026-09-20 用户拍板）。

用户两句话定了这件事：「先把预热并发 6 → 2，队尾等待缩到 ~4–5s，重新跑」→
「那加上超时重试呢，超时原因是并发太高还是请求太多？」（实测答案：**并发太高**）。

实测（13101 是**串行/单通道**服务）：
    1 并发 → 2.1–6.7s（纯串行 20 次延迟平坦、全 200、吞吐 0.23 次/秒）
    2 并发 → 中位 5.5s，最大 8.7s ｜ 3 并发 → 中位 9.6s，最大 12.8s ｜ 6 并发 → 9.1–13.0s
⇒ 延迟 ≈ 并发数 × 单发时长（排队）；并发**不涨吞吐**，只把队尾拉过超时线 ⇒ 5/6 必超时
  ⇒ 超时 = fail-open 放行 + 负缓存 ⇒ 主营校验（含 ④）静默失效（实测 0116/0119/0120 = 98%/92%/96%）。
最终口径：**主路径批量**（一次 20–40 只；实测 24 只一批 3.7–4.3s、与单票 48/48 一致）、
单票并发降到 **1**（只作兜底）、超时 20s、失败**退避重试** 2 次、负缓存 120s。
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


@pytest.fixture()
def LG(monkeypatch):
    import leg_gate
    import theme_member_llm
    importlib.reload(leg_gate)
    seen = {}

    def _fake_pf(items, workers=1):
        seen["workers"] = workers
        seen["n"] = len(items)
        return len(items)

    monkeypatch.setattr(theme_member_llm, "prefetch", _fake_pf, raising=False)
    monkeypatch.setattr(leg_gate, "concepts_of", lambda s: [], raising=False)
    return leg_gate, seen


def test_单票兜底并发默认是1(LG, monkeypatch):
    LG_, _ = LG
    monkeypatch.delenv("WOLF_MEMBER_PREWARM_WORKERS", raising=False)
    assert LG_._prewarm_workers() == 1, "主路径已批量；单票只兜底，串行即可（并发只涨延迟不涨吞吐）"


def test_env可覆盖且下限兜底(LG, monkeypatch):
    LG_, _ = LG
    monkeypatch.setenv("WOLF_MEMBER_PREWARM_WORKERS", "2")
    assert LG_._prewarm_workers() == 2
    monkeypatch.setenv("WOLF_MEMBER_PREWARM_WORKERS", "0")       # 0 会死锁 ⇒ 下限 1
    assert LG_._prewarm_workers() == 1
    monkeypatch.setenv("WOLF_MEMBER_PREWARM_WORKERS", "abc")     # 脏值回退 1
    assert LG_._prewarm_workers() == 1


def test_显式传参优先于env(LG, monkeypatch):
    """向后兼容：显式 workers 参数不容被 env 覆盖（老调用点可能传 6）。"""
    LG_, seen = LG
    monkeypatch.setenv("WOLF_MEMBER_PREWARM_WORKERS", "1")
    monkeypatch.setattr(LG_, "_MB_ENABLED_FOR_TEST", False, raising=False)
    import theme_member_batch as _B
    monkeypatch.setattr(_B, "enabled", lambda: False, raising=False)
    import theme_member_llm as _M
    monkeypatch.setattr(_M, "prefetch", lambda items, workers=1: (seen.update(workers=workers) or len(items)), raising=False)
    LG_.prefetch([("SZ000100", "半导体/芯片")], workers=5)
    assert seen["workers"] == 5


def test_pins钉住批量_重试_超时_负缓存():
    """pins 是回测口径的唯一入口：这组数是这次修复的全部旋钮，删一个就退回"静默失效"。"""
    txt = (REPO_ROOT / "jobs" / "bt_env_pins.sh").read_text(encoding="utf-8")
    for frag in ("WOLF_MEMBER_PREWARM_WORKERS='1'", "WOLF_MEMBER_BATCH='1'", "WOLF_MEMBER_BATCH_SIZE='40'",
                 "WOLF_MEMBER_RETRY='1'", "WOLF_MEMBER_RETRY_BACKOFF='2'",
                 "WOLF_MEMBER_LLM_TIMEOUT='20'", "WOLF_MEMBER_NEG_TTL='120'",
                 "WOLF_MEMBER_BATCH_TIMEOUT='60'", "WOLF_MEMBER_BATCH_RETRY='1'", "WOLF_MEMBER_FAIL_FAST='1'"):
        assert frag in txt, "pins 少了 %s" % frag


def test_超时预算覆盖实测最坏值():
    """口径自洽：批量一批 24 只实测 3.7–4.3s、40 只 10.0s；2 并发最坏 6.0s ⇒ 20s 都兜得住。"""
    worst_batch = 10.0
    worst_two_workers = 6.0
    timeout = float(os.getenv("WOLF_MEMBER_LLM_TIMEOUT", "20"))
    assert timeout > worst_batch * 1.5
    assert timeout > worst_two_workers * 2


def test_库内默认不改行为(monkeypatch):
    """批量开关库内默认关（生产零影响）—— 只有 pins 打开。"""
    import theme_member_batch as _B
    importlib.reload(_B)
    monkeypatch.delenv("WOLF_MEMBER_BATCH", raising=False)
    assert _B.enabled() is False
