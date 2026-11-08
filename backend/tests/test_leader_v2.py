# -*- coding: utf-8 -*-
"""leader_v2 单测：向狼大 2025-12-06「风向标/龙头/核心」框架对齐的尺子。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

import leader_v2 as L  # noqa: E402


class TestSwitch(unittest.TestCase):
    def test_default_off(self):
        os.environ.pop("WOLF_LEADER_V2", None)
        self.assertFalse(L.enabled(), "库内默认关 ⇒ 生产零影响")

    def test_mv_topn_default_and_override(self):
        os.environ.pop("WOLF_LEADER_V2_MV_TOPN", None)
        self.assertEqual(L.mv_topn(), 5, "他的要点①：板块内总市值前 5")
        os.environ["WOLF_LEADER_V2_MV_TOPN"] = "3"
        try:
            self.assertEqual(L.mv_topn(), 3)
        finally:
            os.environ.pop("WOLF_LEADER_V2_MV_TOPN", None)


class TestHardGates(unittest.TestCase):
    def _ok(self, **kw):
        base = {"ret20": 10.0, "mv": 1e11, "close": 20.0, "ma5": 19.0, "mv_rank": 1}
        base.update(kw)
        return base

    def _ctx(self, **kw):
        base = {"concept_ret20": 5.0, "index_ret20": 1.0}
        base.update(kw)
        return base

    def test_all_pass(self):
        ok, why = L.hard_gates(self._ok(), self._ctx(), 5)
        self.assertTrue(ok, why)

    def test_g1_concept_weaker_than_index(self):
        ok, why = L.hard_gates(self._ok(), self._ctx(concept_ret20=0.5), 5)
        self.assertFalse(ok)
        self.assertTrue(any("G1" in w for w in why))

    def test_g2_stock_weaker_than_concept(self):
        ok, why = L.hard_gates(self._ok(ret20=4.0), self._ctx(), 5)
        self.assertFalse(ok)
        self.assertTrue(any("G2" in w for w in why))

    def test_g3_not_top5_market_cap(self):
        ok, why = L.hard_gates(self._ok(mv_rank=6), self._ctx(), 5)
        self.assertFalse(ok)
        self.assertTrue(any("G3" in w for w in why))

    def test_g4_below_ma5(self):
        ok, why = L.hard_gates(self._ok(close=18.0), self._ctx(), 5)
        self.assertFalse(ok)
        self.assertTrue(any("G4" in w for w in why))


class TestScore(unittest.TestCase):
    def test_weight_order_matches_corpus(self):
        # 他的核心要素序：政策点火 > 强赚钱效应 > 题材联动 > 体量大 > 资金 > 趋势领跑
        self.assertGreater(L.WEIGHTS["catalyst"], L.WEIGHTS["money_effect"])
        self.assertGreater(L.WEIGHTS["money_effect"], L.WEIGHTS["linkage"])
        self.assertGreater(L.WEIGHTS["linkage"], L.WEIGHTS["size"])
        self.assertGreater(L.WEIGHTS["size"], L.WEIGHTS["fund"])
        self.assertGreater(L.WEIGHTS["fund"], L.WEIGHTS["lead"])

    def test_missing_feature_is_neutral(self):
        f1 = {"catalyst": 0.5, "money_effect": 0.5, "linkage": 0.5, "size": 0.5, "fund": 0.5, "lead": 0.5}
        f2 = dict(f1)
        f2["fund"] = None                       # 缺项应被跳过而非当 0 分
        self.assertAlmostEqual(L.score(f1), L.score(f2), places=6)

    def test_higher_catalyst_wins(self):
        a = {"catalyst": 0.9, "money_effect": 0.5, "linkage": 0.5, "size": 0.5, "fund": 0.5, "lead": 0.5}
        b = {"catalyst": 0.1, "money_effect": 0.5, "linkage": 0.5, "size": 0.5, "fund": 0.5, "lead": 0.5}
        self.assertGreater(L.score(a), L.score(b))


class TestRankConcept(unittest.TestCase):
    def test_mv_rank_and_sorting(self):
        members = [
            {"symbol": "A", "ret20": 9.0, "ret5": 1.0, "mv": 5e10, "close": 10.0, "ma5": 9.0, "catalyst": 0.5, "fund": 0.5},
            {"symbol": "B", "ret20": 8.0, "ret5": 1.0, "mv": 9e10, "close": 10.0, "ma5": 9.0, "catalyst": 0.5, "fund": 0.5},
        ]
        out = L.rank_concept(members, {"concept_ret20": 5.0, "concept_ret5": 0.5}, index_ret20=1.0)
        ranks = {m["symbol"]: m["_v2_mv_rank"] for m in out}
        self.assertEqual(ranks["B"], 1, "市值大者排第 1")
        self.assertEqual(ranks["A"], 2)
        self.assertTrue(all(m["_v2_gate"] for m in out))

    def test_ungated_ranked_below_gated(self):
        members = [
            {"symbol": "GOOD", "ret20": 12.0, "ret5": 1.0, "mv": 9e10, "close": 10.0, "ma5": 9.0, "catalyst": 0.1, "fund": 0.1},
            {"symbol": "BAD", "ret20": 1.0, "ret5": 1.0, "mv": 9e10, "close": 10.0, "ma5": 9.0, "catalyst": 0.99, "fund": 0.99},
        ]
        out = L.rank_concept(members, {"concept_ret20": 5.0, "concept_ret5": 0.5}, index_ret20=1.0)
        self.assertEqual(out[0]["symbol"], "GOOD", "过硬门的排在不过门的前面（他的前提优先）")


class TestFundBlocks(unittest.TestCase):
    """2026-09-21 晚：语料点名的三项（业绩/产业链中枢/估值）数据补齐后的口径。"""

    def _clean(self):
        for k in ("WOLF_LEADER_V2_EARN", "WOLF_LEADER_V2_CHAIN", "WOLF_LEADER_V2_VAL",
                  "WOLF_FUND_TAG", "SZ_TAG"):
            os.environ.pop(k, None)

    def test_sub_switches_default_off(self):
        self._clean()
        self.assertFalse(L.earn_on()); self.assertFalse(L.chain_on()); self.assertFalse(L.val_on())
        self.assertEqual(L.weights(), L.WEIGHTS, "默认关 ⇒ 权重与老版完全一致（生产零影响）")

    def test_weights_follow_corpus_order(self):
        """他的核心标的要素序：政策点火>强赚钱效应>题材联动>板块内体量>基本面业绩>资金买盘>产业链中枢>趋势领跑>估值反转"""
        self._clean()
        for k in ("WOLF_LEADER_V2_EARN", "WOLF_LEADER_V2_CHAIN", "WOLF_LEADER_V2_VAL"):
            os.environ[k] = "1"
        try:
            w = L.weights()
            seq = ["catalyst", "money_effect", "linkage", "size", "earnings", "fund",
                   "chain", "lead", "valuation"]
            vals = [w[k] for k in seq]
            self.assertEqual(vals, sorted(vals, reverse=True), "权重必须严格按他的要素序递减")
        finally:
            self._clean()

    def test_feat_mappings(self):
        self.assertEqual(L.earn_feat({"ok": True, "flag": "good"}), 1.0)
        self.assertEqual(L.earn_feat({"ok": True, "flag": "bad"}), 0.0)
        self.assertIsNone(L.earn_feat({"ok": False, "flag": "good"}), "没数据=None(不参与打分)")
        self.assertIsNone(L.earn_feat(None))
        self.assertEqual(L.chain_feat({"ok": True, "flag": "core"}), 1.0)
        self.assertEqual(L.chain_feat({"ok": True, "flag": "mid"}), 1.0, "在链上就是 1.0（中枢度由稀缺度分位承担）")
        self.assertIsNone(L.chain_feat({"ok": False}), "链字典未覆盖 ⇒ 缺失")

    def test_chain_value_blends_onboard_and_scarcity(self):
        """产业链项 = 0.5×在链上 + 0.5×环节稀缺度（窄者高）；未覆盖=None。"""
        m_narrow = {"_chain": {"ok": True, "flag": "core", "width": 9}, "_v2p": {"chain_scarcity": 1.0}}
        m_wide = {"_chain": {"ok": True, "flag": "core", "width": 500}, "_v2p": {"chain_scarcity": 0.0}}
        self.assertAlmostEqual(L._chain_val(m_narrow), 1.0, places=6)
        self.assertAlmostEqual(L._chain_val(m_wide), 0.5, places=6)
        self.assertGreater(L._chain_val(m_narrow), L._chain_val(m_wide),
                           "窄环节(稀缺零件)必须高于宽环节(概念堆砌)")
        self.assertIsNone(L._chain_val({"_chain": {"ok": False}}), "未覆盖 ⇒ 缺失")

    def test_scarcity_is_rank_based_not_threshold(self):
        """环节稀缺度必须是**池内分位**（参数无关），不是固定阈值。"""
        members = [
            {"symbol": "NARROW", "ret20": 6.0, "ret5": 1.0, "mv": 9e10, "close": 10.0, "ma5": 9.0,
             "catalyst": 0.5, "fund": 0.5, "_chain": {"ok": True, "flag": "mid", "width": 9}},
            {"symbol": "WIDE", "ret20": 6.0, "ret5": 1.0, "mv": 9e10, "close": 10.0, "ma5": 9.0,
             "catalyst": 0.5, "fund": 0.5, "_chain": {"ok": True, "flag": "core", "width": 900}},
        ]
        out = L.rank_concept(members, {"concept_ret20": 5.0}, index_ret20=1.0)
        got = {m["symbol"]: (m.get("_v2p") or {}).get("chain_scarcity") for m in out}
        self.assertGreater(got["NARROW"], got["WIDE"], "窄环节分位更高")
        self.assertEqual(L.val_feat({"ok": True, "flag": "cheap"}), 1.0)
        self.assertEqual(L.val_feat({"ok": True, "flag": "rich"}), 0.0)

    def test_g5_earnings_gate_only_when_data_present(self):
        stock = {"ret20": 10.0, "mv": 1e11, "close": 20.0, "ma5": 19.0, "mv_rank": 1,
                 "earn_flag": "bad"}
        ctx = {"concept_ret20": 5.0, "index_ret20": 1.0}
        self._clean()
        ok, why = L.hard_gates(dict(stock), ctx, 5)
        self.assertTrue(ok, "开关关 ⇒ 业绩差不拦（生产零影响）")
        os.environ["WOLF_LEADER_V2_EARN"] = "1"
        try:
            ok, why = L.hard_gates(dict(stock), ctx, 5)
            self.assertFalse(ok)
            self.assertTrue(any("G5" in w for w in why))
            ok2, _ = L.hard_gates({**stock, "earn_flag": None}, ctx, 5)
            self.assertTrue(ok2, "取不到业绩 ⇒ 放行（fail-open，绝不因缺数据改变行为）")
        finally:
            self._clean()

    def test_attach_fund_noop_without_tag(self):
        self._clean()
        os.environ["WOLF_LEADER_V2_EARN"] = "1"
        try:
            rows = [{"symbol": "603986.SH"}]
            self.assertEqual(L.attach_fund(rows, "20260108"), 0, "无 tag ⇒ 不取数")
            self.assertIsNone(rows[0].get("_earn"))
        finally:
            self._clean()

    def test_missing_still_flags_scarcity_gap(self):
        self.assertTrue(any("稀缺零件" in x for x in L.MISSING),
                        "产品级'稀缺零件/不可替代性'无数据源，必须继续显式标注为缺项")


if __name__ == "__main__":
    unittest.main()