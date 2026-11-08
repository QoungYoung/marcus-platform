# -*- coding: utf-8 -*-
"""看板计价空间自动判定（`detect_price_space`）回归测试 —— 2026-09-24。

## 病灶（账本 §9.15）

T13/T14 用 `WOLF_ADJ_PRICE=1` ⇒ **成交价与现金都在前复权空间**；而看板
（`jobs/bt_dashboard.py` 净值重建）、`jobs/bt_arm_compare.py`、`.dsh-tmp/wolfbt/diag_curve.py`
都用**不复权** `bars.sqlite` 给持仓计价 ⇒ 持仓市值虚影 = Σ 股数 ×(不复权收 − 复权收)。

实测（drabt14，0128 收盘）：SZ003026 1100 股，不复权收 34.740 / 复权收 23.878
（`adj_factors.json` 里 20260605「10转4.5」倍数 1.4549 被前复权提前折进回测期）
⇒ 虚影 +11,949，当日持仓虚影合计 +12,166 ⇒ 看板 0128 +9.69%、0129 单日 −11,098，
真实为 +4.95% / −4,298（虚影全期累计为 0 ⇒ 期末权益相同，只错在权益水平/单日/回撤）。

## 本测试守什么

1. 成交价贴合**复权**收盘（有未来除权事件的票）⇒ 判 `adj`；
2. 成交价贴合**不复权**收盘 ⇒ 判 `raw`（T5–T12 不能被误判成 adj）；
3. 两套收盘完全相同（无事件）⇒ 无信息，退回 `raw`；
4. 样本分歧 ⇒ **保守退回 raw**（宁可少修，不可把不复权臂算错）；
5. 小样本但方向完全一致且中位误差差 2 倍以上 ⇒ 仍可判（实测 T8 只有 2 笔受影响）；
6. 复权库缺失 / 成交为空 ⇒ 安全退回 `raw`，不抛异常；
7. `WOLF_CURVE_SPACE` 可强制覆盖（排障用）；
8. 静态接线：看板的净值重建、持仓估值、逐日分解三处 `closes()` 都传 `bars=`，
   且 `bars_for_space('adj')` 指向 `bars_adj.sqlite`。
"""
import argparse
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_dashboard():
    spec = importlib.util.spec_from_file_location("bt_dashboard_mod",
                                                  os.path.join(ROOT, "jobs", "bt_dashboard.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _load_dashboard()


def _mkdb(path, rows):
    """rows: [(ts_code, trade_date, close)]"""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE bars (ts_code TEXT, trade_date TEXT, close REAL,"
                " PRIMARY KEY (ts_code, trade_date))")
    con.executemany("INSERT INTO bars VALUES (?,?,?)", rows)
    con.commit()
    con.close()


class PriceSpaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="psp_")
        self.raw = os.path.join(self.tmp, "bars.sqlite")
        self.adj = os.path.join(self.tmp, "bars_adj.sqlite")

    def _db(self, raw_rows, adj_rows):
        _mkdb(self.raw, raw_rows)
        _mkdb(self.adj, adj_rows)
        return self.raw, self.adj

    def _detect(self, trades, env=None):
        old = os.environ.get("WOLF_CURVE_SPACE")
        if env is None:
            os.environ.pop("WOLF_CURVE_SPACE", None)
        else:
            os.environ["WOLF_CURVE_SPACE"] = env
        try:
            return B.detect_price_space(trades, self.raw, self.adj)
        finally:
            if old is None:
                os.environ.pop("WOLF_CURVE_SPACE", None)
            else:
                os.environ["WOLF_CURVE_SPACE"] = old

    # ── ① 贴合复权收盘 ⇒ adj ────────────────────────────────────────────────
    def test_adj_space_detected(self):
        # 不复权 34.74 / 复权 23.878（倍数 1.4549）；成交价 23.465（复权空间，盘中偏离 ~1.8%）
        raw, adj = self._db(
            [("003026.SZ", "20260128", 34.740), ("003026.SZ", "20260129", 34.940),
             ("002156.SZ", "20260128", 54.110), ("002156.SZ", "20260129", 51.470)],
            [("003026.SZ", "20260128", 23.878), ("003026.SZ", "20260129", 24.010),
             ("002156.SZ", "20260128", 54.110), ("002156.SZ", "20260129", 51.470)])
        trades = [{"symbol": "SZ003026", "trade_date": "2026-01-29", "price": 23.465},
                  {"symbol": "SZ003026", "trade_date": "2026-01-28", "price": 23.900},
                  {"symbol": "SZ002156", "trade_date": "2026-01-29", "price": 53.780}]
        info = self._detect(trades)
        self.assertEqual(info["space"], "adj")
        self.assertEqual(info["n_affected"], 2)       # SZ002156 两套相同 ⇒ 不计入
        self.assertEqual(info["wins_adj"], 2)
        self.assertEqual(info["wins_raw"], 0)
        self.assertLess(info["med_adj"], info["med_raw"])

    # ── ② 贴合不复权收盘 ⇒ raw（T5–T12 保护）────────────────────────────────
    def test_raw_space_detected(self):
        raw, adj = self._db(
            [("003026.SZ", "20260129", 34.940), ("003026.SZ", "20260130", 35.100),
             ("003026.SZ", "20260202", 33.200)],
            [("003026.SZ", "20260129", 24.010), ("003026.SZ", "20260130", 24.120),
             ("003026.SZ", "20260202", 22.815)])
        trades = [{"symbol": "SZ003026", "trade_date": "2026-01-29", "price": 34.800},
                  {"symbol": "SZ003026", "trade_date": "2026-01-30", "price": 35.220},
                  {"symbol": "SZ003026", "trade_date": "2026-02-02", "price": 33.010}]
        self.assertEqual(self._detect(trades)["space"], "raw")

    # ── ③ 两套收盘相同 ⇒ 无信息 ⇒ raw ───────────────────────────────────────
    def test_no_event_falls_back_raw(self):
        rows = [("002156.SZ", "20260129", 51.470)]
        raw, adj = self._db(rows, rows)
        info = self._detect([{"symbol": "SZ002156", "trade_date": "2026-01-29", "price": 51.500}])
        self.assertEqual(info["space"], "raw")
        self.assertEqual(info["n_affected"], 0)
        self.assertIn("无复权事件", info["why"])

    # ── ④ 样本分歧 ⇒ 保守 raw ──────────────────────────────────────────────
    def test_disagreement_falls_back_raw(self):
        raw, adj = self._db(
            [("003026.SZ", "20260129", 34.940), ("003026.SZ", "20260130", 35.100),
             ("003026.SZ", "20260202", 33.200), ("003026.SZ", "20260203", 33.000)],
            [("003026.SZ", "20260129", 24.010), ("003026.SZ", "20260130", 24.120),
             ("003026.SZ", "20260202", 22.815), ("003026.SZ", "20260203", 22.680)])
        trades = [{"symbol": "SZ003026", "trade_date": "2026-01-29", "price": 34.900},   # raw 贴合
                  {"symbol": "SZ003026", "trade_date": "2026-01-30", "price": 24.100},   # adj 贴合
                  {"symbol": "SZ003026", "trade_date": "2026-02-02", "price": 33.150},   # raw 贴合
                  {"symbol": "SZ003026", "trade_date": "2026-02-03", "price": 22.700}]   # adj 贴合
        info = self._detect(trades)
        self.assertEqual(info["space"], "raw")
        self.assertIn("分歧", info["why"])

    # ── ⑤ 小样本但方向一致 + 2 倍差距 ⇒ 可判 ────────────────────────────────
    def test_small_sample_one_sided(self):
        raw, adj = self._db(
            [("003026.SZ", "20260129", 34.940), ("003026.SZ", "20260130", 35.100)],
            [("003026.SZ", "20260129", 24.010), ("003026.SZ", "20260130", 24.120)])
        info = self._detect([{"symbol": "SZ003026", "trade_date": "2026-01-29", "price": 34.800},
                             {"symbol": "SZ003026", "trade_date": "2026-01-30", "price": 24.050}])
        self.assertEqual(info["n_affected"], 2)
        self.assertEqual(info["space"], "raw")        # 1 raw + 1 adj ⇒ 分歧
        info2 = self._detect([{"symbol": "SZ003026", "trade_date": "2026-01-29", "price": 24.000},
                              {"symbol": "SZ003026", "trade_date": "2026-01-30", "price": 24.100}])
        self.assertEqual(info2["space"], "adj")       # 2/2 adj 且差距 > 2 倍 ⇒ 仍判 adj

    # ── ⑥ 缺库/空成交 ⇒ fail-safe raw ──────────────────────────────────────
    def test_missing_db_or_empty(self):
        self._db([("003026.SZ", "20260129", 34.940)], [("003026.SZ", "20260129", 24.010)])
        trades = [{"symbol": "SZ003026", "trade_date": "2026-01-29", "price": 24.000}]
        self.assertEqual(B.detect_price_space(trades, self.raw, "")["space"], "raw")
        self.assertEqual(B.detect_price_space([], self.raw, self.adj)["space"], "raw")
        self.assertEqual(B.detect_price_space(trades, os.path.join(self.tmp, "nope.sqlite"),
                                              self.adj)["space"], "raw")

    # ── ⑦ 环境变量强制 ─────────────────────────────────────────────────────
    def test_env_override(self):
        raw, adj = self._db([("003026.SZ", "20260129", 34.940)], [("003026.SZ", "20260129", 24.010)])
        trades = [{"symbol": "SZ003026", "trade_date": "2026-01-29", "price": 34.900}]
        self.assertEqual(self._detect(trades, env="adj")["space"], "adj")
        self.assertEqual(self._detect(trades, env="raw")["space"], "raw")

    # ── ⑧ 静态接线：三处计价都带 bars= ──────────────────────────────────────
    def test_wiring(self):
        src = open(os.path.join(ROOT, "jobs", "bt_dashboard.py"), encoding="utf-8").read()
        self.assertIn("def detect_price_space(", src)
        self.assertIn('self._curve_space = self.price_space(trades_pg)', src)
        self.assertIn("closes = self.closes(symbols, curve_start, curve_end, bars=curve_bars)", src)
        self.assertIn("self.closes(symbols, curve_start, curve_end, bars=curve_bars)", src)
        self.assertIn("bars=self.bars_for_space(_sp)", src)
        self.assertIn('ap.add_argument("--bars-adj"', src)
        cmp_src = open(os.path.join(ROOT, "jobs", "bt_arm_compare.py"), encoding="utf-8").read()
        self.assertIn("mod.detect_price_space(", cmp_src)

    def test_bars_for_space(self):
        self._db([("003026.SZ", "20260129", 34.940)], [("003026.SZ", "20260129", 24.010)])
        ns = argparse.Namespace(root=self.tmp, bars=self.raw, bars_adj=self.adj,
                                mins=self.tmp, log=os.path.join(self.tmp, "x.log"),
                                pg="", account="t", ttl=3.0, auto=False)
        st = B.Store(ns)
        self.assertEqual(st.bars_for_space("adj"), self.adj)
        self.assertEqual(st.bars_for_space("raw"), self.raw)
        st.bars_adj_db = os.path.join(self.tmp, "missing.sqlite")
        self.assertEqual(st.bars_for_space("adj"), self.raw)      # 复权库缺失 ⇒ 安全退回


if __name__ == "__main__":
    unittest.main()
