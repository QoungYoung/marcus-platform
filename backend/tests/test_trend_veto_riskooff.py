# -*- coding: utf-8 -*-
"""风控减仓腿（derisk_cut / 避险）豁免"趋势约束" + 触发理由超长截断（2026-09-18 回测实测发现）。"""
import os, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TrendVetoRiskOffTest(unittest.TestCase):
    def _src(self, rel):
        return open(os.path.join(ROOT, rel), encoding="utf-8").read()

    def test_switch_default_off_and_gateway_wires_it(self):
        src = self._src("app/services/t_gateway.py")
        self.assertIn('os.getenv("WOLF_TREND_VETO_RISKOFF", "0")', src)      # 库内默认关
        self.assertIn("skip_trend_constraint=_risk_off_skip", src)          # 真的传进执行器
        self.assertIn('"derisk_cut"', src)                                  # 风控腿识别关键词

    def test_executor_supports_skip_flag(self):
        src = self._src("app/core/trading/marcus_trade.py")
        self.assertIn("skip_trend_constraint: bool = False", src)
        self.assertIn("if not skip_trend_constraint:", src)

    def test_backtest_driver_enables(self):
        src = open(os.path.join(ROOT, "..", "jobs", "bt_prod_run.py"), encoding="utf-8").read()
        self.assertIn('("WOLF_TREND_VETO_RISKOFF", "1")', src)

    def test_reason_truncated_to_column_width(self):
        """`t_triggers.reason` 是 VARCHAR(256)：超长写入会抛异常并把整笔卖出记成"执行异常"。"""
        src = self._src("app/services/t_gateway.py")
        self.assertIn('reason=("执行异常: %s" % e)[:250]', src)


if __name__ == "__main__":
    unittest.main()
