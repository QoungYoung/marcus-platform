# -*- coding: utf-8 -*-
"""建仓最小手数（用户 2026-09-22 拍板"修 2b"）：买后持仓不足 2 手 ⇒ 补齐到 2 手。

依据：`t_base_floor._rebase_floor` 的「持仓 < 2 手 ⇒ 整仓即底仓」⇒ 一手仓的 T 仓恒为 0
⇒ 止盈类卖腿永久失效。实测 T5 一手仓 9 笔（21%）、145 条卖腿被拦时持仓正好 100 股；
兆易创新 603986 因此从 0318 的 +1.3% 拖成 0323 的 −9.6%（−2,987）。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_build_lots as L  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WOLF_BUILD_MIN_LOTS", raising=False)


def test_库内默认关():
    assert L.min_lots() == 0 and not L.enabled()
    assert L.topup_volume(0, 100) == (100, ""), "关着 ⇒ 逐字不变（生产零影响）"


def test_开两手的三种情形(monkeypatch):
    monkeypatch.setenv("WOLF_BUILD_MIN_LOTS", "2")
    # ① 空仓买 100（兆易创新那种情形）⇒ 补到 200
    v, why = L.topup_volume(0, 100)
    assert v == 200 and "不足 2 手" in why
    # ② 已持 100、再买 100 ⇒ 正好 200，不动
    assert L.topup_volume(100, 100)[0] == 100
    # ③ 已持 100、买 300 ⇒ 400，不动
    assert L.topup_volume(100, 300)[0] == 300
    # ④ 空仓买 500 ⇒ 不动
    assert L.topup_volume(0, 500)[0] == 500
    # ⑤ 已持 1000 ⇒ 加 100 也不动（买后 ≥2 手）
    assert L.topup_volume(1000, 100)[0] == 100


def test_非整手与非法输入(monkeypatch):
    monkeypatch.setenv("WOLF_BUILD_MIN_LOTS", "2")
    assert L.topup_volume(0, 0)[0] == 0, "不买就不管"
    assert L.topup_volume(100, 0)[0] == 0
    assert L.topup_volume(0, 100, lots=3)[0] == 300, "可配 3 手"
    monkeypatch.setenv("WOLF_BUILD_MIN_LOTS", "abc")
    assert L.min_lots() == 0, "非法值 ⇒ 关"


def test_接线_网关在执行口抬高():
    """源码级：抬高必须发生在**下单唯一入口**，否则各条买腿路径（建仓/低吸/AI）漏掉。"""
    src = (REPO_ROOT / "backend/app/services/t_gateway.py").read_text(encoding="utf-8")
    body = src[src.index("def gateway_execute"):]
    i = body.index("topup_volume")
    j = body.index("# 1) 校验")
    assert i < j, "抬高必须发生在 validate_order 之前（否则按旧股数校验/落单）"
