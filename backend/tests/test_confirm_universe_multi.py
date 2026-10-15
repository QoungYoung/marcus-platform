# -*- coding: utf-8 -*-
"""判级域「多归属」与日线取数起点单测（2026-10-15 查实的两处 ✗）。

背景（实证 20260106 ✓）：
  ① 判级落盘 `stock_confirm_result.json` 每条记录**只打一个** `theme` 标签 ✗，
     而 `THEME_CONCEPTS` 存在**多归属**概念（存储芯片/AI芯片 同属 AI/算力/科技 与 半导体/芯片 ✓）
     ⇒ 精确匹配单标签 ⇒ `confirm_universe('半导体/芯片')` **恒空** ✗ ⇒ `no_universe`
     ⇒ 回退 legacy（无龙头约束）⇒ 买杂毛 ✗
  ② `fetch_daily` **硬编码** `start_date="20260301"` ✗ ⇒ as-of < 该日 ⇒ 0 行 ⇒ `len(rows)>=80` 全不过
     ⇒ `no_scored` ⇒ v2 选股整段失效 ✗（与 §9.870 同型 ✗）

本测试锁死：两个开关**默认关 = 逐字不变** ✓；开启后行为符合预期 ✓；未收录概念**不下沉** ✓。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

_APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps", "main_line")
sys.path.insert(0, os.path.abspath(_APP))

import wolf_confirm_pick as WCP  # noqa: E402


class TestMultiThemeUniverse(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("WOLF_UNIVERSE_MULTI_THEME")
        os.environ.pop("WOLF_UNIVERSE_MULTI_THEME", None)
        self._tmp = tempfile.mkdtemp(prefix="wcp_uni_")
        self._old_data, self._old_c2t = WCP.DATA, WCP._concept_themes
        WCP.DATA = self._tmp
        WCP._C2T_CACHE.clear()
        with open(os.path.join(self._tmp, "stock_confirm_result.json"), "w", encoding="utf-8") as f:
            json.dump({
                "存储芯片": {"theme": "AI/算力/科技", "stocks": [{"code": "002156.SZ"}]},
                "光通信模块": {"theme": "AI/算力/科技", "stocks": [{"code": "300308.SZ"}]},
                "冷门概念": {"theme": "冷门主题", "stocks": [{"code": "600000.SH"}]},
            }, f, ensure_ascii=False)

    def tearDown(self):
        WCP.DATA, WCP._concept_themes = self._old_data, self._old_c2t
        WCP._C2T_CACHE.clear()
        if self._saved is None:
            os.environ.pop("WOLF_UNIVERSE_MULTI_THEME", None)
        else:
            os.environ["WOLF_UNIVERSE_MULTI_THEME"] = self._saved
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_default_off_single_tag(self):
        """默认关 ⇒ 原单标签精确匹配（逐字不变 ✓）。"""
        self.assertEqual(list(WCP.confirm_universe("半导体/芯片")), [], "单标签下 半导体/芯片 应为空（复现病灶 ✗）")
        self.assertEqual(set(WCP.confirm_universe("AI/算力/科技")), {"存储芯片", "光通信模块"})

    def test_multi_theme_hits_second_owner(self):
        """开启 ⇒ 多归属概念在两个主题下**都命中** ✓。"""
        os.environ["WOLF_UNIVERSE_MULTI_THEME"] = "1"
        WCP._concept_themes = lambda: {"存储芯片": {"AI/算力/科技", "半导体/芯片"},
                                       "光通信模块": {"AI/算力/科技"}}
        self.assertEqual(sorted(WCP.confirm_universe("半导体/芯片")), ["存储芯片"], "多归属必须命中 ✓")
        self.assertEqual(set(WCP.confirm_universe("AI/算力/科技")), {"存储芯片", "光通信模块"})
        self.assertEqual(WCP.confirm_universe("半导体/芯片")["存储芯片"], ["002156.SZ"])

    def test_unknown_concept_falls_back_to_tag(self):
        """映射表**未收录**的概念 ⇒ 回落原单标签 ✓（口径不下沉 ✗）。"""
        os.environ["WOLF_UNIVERSE_MULTI_THEME"] = "1"
        WCP._concept_themes = lambda: {"存储芯片": {"AI/算力/科技", "半导体/芯片"}}
        self.assertEqual(list(WCP.confirm_universe("冷门主题")), ["冷门概念"], "未收录 ⇒ 按原标签 ✓")


class TestDailyStart(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("WOLF_DAILY_START_DAYS")
        os.environ.pop("WOLF_DAILY_START_DAYS", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("WOLF_DAILY_START_DAYS", None)
        else:
            os.environ["WOLF_DAILY_START_DAYS"] = self._saved

    def test_default_unchanged(self):
        """默认 ⇒ 原硬编码 '20260301'（逐字不变 ✓）。"""
        self.assertEqual(WCP._daily_start("20260105"), "20260301")

    def test_wide_window(self):
        """开启 ⇒ end 往前推 N 自然日 ✓（覆盖 ≥80 交易日 ✓）。"""
        os.environ["WOLF_DAILY_START_DAYS"] = "400"
        self.assertEqual(WCP._daily_start("20260105"), "20241201", "400 自然日 ⇒ 覆盖 ≥80 交易日 ✓")

    def test_bad_value_falls_back(self):
        os.environ["WOLF_DAILY_START_DAYS"] = "abc"
        self.assertEqual(WCP._daily_start("20260105"), "20260301")


if __name__ == "__main__":
    unittest.main()


class TestTraceStage(unittest.TestCase):
    """逐阶段留痕（2026-10-15 ✓）：`status_out["stages"]` 必须记录「哪一步剔了谁」✓。"""

    def test_records_kept_dropped_and_sample(self):
        st = {}
        WCP._trace_stage(st, "low_mid",
                         [{"ts": "300475.SZ", "pos": "LOW", "leader": 0.9}],
                         dropped=[{"ts": "002156.SZ", "pos": "HIGH", "leader": 0.39, "rs": -5.0}],
                         note="要求 pos ∈ {LOW,MID}")
        stg = st["stages"][0]
        self.assertEqual(stg["stage"], "low_mid")
        self.assertEqual(stg["kept"], 1)
        self.assertEqual(stg["dropped"], 1)
        self.assertEqual(stg["sample_dropped"][0]["ts"], "002156.SZ")
        self.assertEqual(stg["sample_dropped"][0]["pos"], "HIGH")

    def test_none_status_is_noop(self):
        WCP._trace_stage(None, "scored", [{"ts": "X"}])   # 不抛 ✓

    def test_sample_capped_at_10(self):
        st = {}
        WCP._trace_stage(st, "concept_top2", [], dropped=[{"ts": "T%02d" % i} for i in range(25)])
        self.assertEqual(len(st["stages"][0]["sample_dropped"]), 10)
        self.assertEqual(st["stages"][0]["dropped"], 25)


class TestChainMomentumTop(unittest.TestCase):
    """选链动量通道（2026-10-15 ✓）：纯函数单测，锁死排序/门槛/异常安全 ✓。"""

    @classmethod
    def setUpClass(cls):
        import rotation_universe as RU
        cls.RU = RU

    def _hist(self, mapping):
        # mapping: 概念名 -> [close...]（末位=最新 ✓）
        return {"BK%03d.DC" % i: {"name": nm, "close": cs} for i, (nm, cs) in enumerate(mapping.items())}

    def test_ranked_by_5d_return(self):
        h = self._hist({"甲": [10, 10, 10, 10, 10, 12],      # +20% ✓
                        "乙": [10, 10, 10, 10, 10, 11],      # +10%
                        "丙": [10, 10, 10, 10, 10, 9]})      # −10%
        out = self.RU.chain_momentum_top(h, ["甲", "乙", "丙"], 2)
        self.assertEqual([s for s, _ in out], ["甲", "乙"], "应按 5 日涨幅降序取前 2 ✓")
        self.assertAlmostEqual(out[0][1], 0.2, places=6)

    def test_off_and_insufficient(self):
        h = self._hist({"甲": [10, 10, 10]})                      # 只有 3 根 ⇒ 跳过 ✓
        self.assertEqual(self.RU.chain_momentum_top(h, ["甲"], 0), [], "n=0 ⇒ 关 ✓")
        self.assertEqual(self.RU.chain_momentum_top(h, ["甲"], 2), [], "数据不足 ⇒ 空 ✓")
        self.assertEqual(self.RU.chain_momentum_top(None, ["甲"], 2), [], "hist 缺失 ⇒ 空（不抛 ✓）")

    def test_meta_groups_excluded(self):
        h = self._hist({"甲": [10, 10, 10, 10, 10, 12], "银行": [10, 10, 10, 10, 10, 99]})
        out = self.RU.chain_momentum_top(h, ["甲", "银行"], 2, meta_groups={"银行"})
        self.assertEqual([s for s, _ in out], ["甲"], "元组概念（如银行）应被排除 ✓")
