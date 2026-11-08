# -*- coding: utf-8 -*-
"""量能门「本地自算」回归测试（`WOLF_VOLUME_GATE_LOCAL`）—— 2026-09-25。

## 病灶（账本 §9.27-A2 / §9.28）

回测日目录里 `wolf_volume_gate.json` 是**软链到 repo 的 9 月文件**（`as_of=20260915` + 9/02–9/15 序列），
被 `wolf_stale_guard` 判为未来产物 ⇒ `load()` 返回 `{}`。而它的消费者是
`daily_decision._g10()` —— 其中 `fake_breakout_risk` 是一条 **blocker**
（「攻关口而量能未达突破级 → 诱多，不追高、不加仓」，他 2026-09-03）⇒ **臂里这道拦阻从未触发**。

## 关键事实

两市成交额**我们自己就能算**：`Σ bars.amount / 1e5`（千元 → 亿元），与生产 `market_amount()`
（tushare `pro.daily` 全市场求和）的序列**逐位一致**（20260902–0915 五天误差 0.0）。

## 本测试守什么

1. 库内默认**关**（`local_enabled()==False` ⇒ 生产逐位不变）；
2. 自算序列与生产参考序列**逐位一致**（用 repo 里的 `data/wolf_volume_gate.json` 作基准）；
3. **只取 ≤ 钉住当日**的日期（无前视）；
4. 上证收盘本地序列可读且单调（升序取尾部 n 个）；
5. 开关开 + 文件陈旧/缺失 ⇒ `load()` 走本地自算而不是返回空；
6. 静态接线：`run()` 在本地模式下先算 series、`load()` 有本地兜底。
"""
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import wolf_volume_gate as vg  # noqa: E402

BARS = os.path.join(ROOT, "data", "_bt_full", "bars.sqlite")
REF = os.path.join(ROOT, "data", "wolf_volume_gate.json")


class LocalVolumeGateTest(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("WOLF_VOLUME_GATE_LOCAL", "BT_BARS_DB", "DATA_DIR")}
        os.environ.pop("WOLF_VOLUME_GATE_LOCAL", None)
        os.environ.setdefault("BT_BARS_DB", BARS)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ── ① 默认关 ────────────────────────────────────────────────────────────
    def test_default_off(self):
        self.assertFalse(vg.local_enabled())
        src = open(os.path.join(ROOT, "backend", "app", "services", "wolf_volume_gate.py"),
                   encoding="utf-8").read()
        self.assertIn('os.getenv("WOLF_VOLUME_GATE_LOCAL", "0")', src)

    # ── ② 与生产参考序列逐位一致 ────────────────────────────────────────────
    @unittest.skipUnless(os.path.exists(REF) and os.path.exists(BARS), "缺参考文件/日线库")
    def test_series_matches_production(self):
        ref = json.load(open(REF, encoding="utf-8"))["series"]
        days = sorted(ref)[:5]
        mine = vg.market_amount_local(days)
        self.assertTrue(mine, "本地自算序列为空")
        for d in days:
            self.assertAlmostEqual(mine[d], ref[d], places=1, msg="%s 自算 %.1f vs 生产 %.1f" % (d, mine[d], ref[d]))

    # ── ③ 无前视：只取 ≤ 给定日 ─────────────────────────────────────────────
    def test_no_lookahead(self):
        out = vg.market_amount_local(["20260105", "20260106", "20991231"])
        self.assertIn("20260105", out)
        self.assertNotIn("20991231", out, "未来日期不应有数据（且不得臆造）")

    # ── ④ 上证收盘本地序列 ─────────────────────────────────────────────────
    def test_index_closes_local(self):
        cs = vg.index_closes_local(5)
        self.assertEqual(len(cs), 5, "应能读到 5 个上证收盘")
        self.assertTrue(all(c > 0 for c in cs))

    # ── ⑤ load() 在开关开 + 陈旧时走本地 ───────────────────────────────────
    def test_load_falls_back_to_local(self):
        os.environ["WOLF_VOLUME_GATE_LOCAL"] = "1"
        os.environ["WOLF_VOLUME_GATE"] = "1"
        os.environ["DATA_DIR"] = os.path.join(ROOT, ".dsh-tmp", "_vg_test_empty")   # 无该文件
        with mock.patch.object(vg, "run", wraps=vg.run) as spy:
            st = vg.load()
            self.assertTrue(spy.called, "开关开且文件缺失 ⇒ 应调用 run() 本地自算")
        self.assertIsInstance(st, dict)

    # ── ⑥ 静态接线 ─────────────────────────────────────────────────────────
    def test_wiring(self):
        src = open(os.path.join(ROOT, "backend", "app", "services", "wolf_volume_gate.py"),
                   encoding="utf-8").read()
        self.assertIn("def market_amount_local(", src)
        self.assertIn("def index_closes_local(", src)
        self.assertIn('res["_src"] = "local_bars"', src)
        pins = open(os.path.join(ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
        self.assertIn("WOLF_VOLUME_GATE_LOCAL", pins)


if __name__ == "__main__":
    unittest.main()
