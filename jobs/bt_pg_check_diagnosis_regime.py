# -*- coding: utf-8 -*-
"""bt_pg_check_diagnosis_regime.py — "补 `market_diagnosis` 之前 vs 之后" 对下游读取方的差异实证。

对指定交易日 D，在**同一个进程**里把时钟钉到 D，跑**生产函数**：

  1. `t_regime.compute_regime(force=True)` —— 环境闸门合成（L1 日频基准 ← `market_diagnosis`）
     · BEFORE：把 `t_regime._read_market_diagnosis` 换成"查不到行"（**= 回填前 1–7 月的原状**）；
     · AFTER ：用真函数读库（= 回填后）。
  2. `trade_graph._read_market_regime()` / `api.indicator._get_market_regime_for_calc()` —— 另外两个读取方
     （无行时分别返回 `("unknown", … "⚠️ 今日尚未执行盘前诊断")` 与默认 `'trend'`）。它们走 ORM，无法在
     不写库的前提下"隐藏行"，因此**用 `--hole <真实仍缺行的那天>` 演示 before 语义**（默认 20260908 —— 生产
     当天 `morning_diagnosis` 根本没跑，日志无记录、表里也没行）。

L2/L3 被隔离（`_fetch_index_quotes` → `{}`、`t_db.upsert_regime_state` → 空操作），使差异只来自 L1，
且不在回测 `t_regime_state` 表里留探针行。

用法：
    .venv/bin/python jobs/bt_pg_check_diagnosis_regime.py --days 20260310,20260520,20260701
"""
from __future__ import annotations

import argparse
import os
import sys

JOBS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(JOBS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="20260310,20260520,20260701")
    ap.add_argument("--hole", default="20260908", help="真实仍缺行的一天（用于演示 before 语义）")
    ap.add_argument("--db-url", default=os.getenv("BT_PG_URL") or os.getenv("DATABASE_URL")
                    or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    ap.add_argument("--data-dir", default=os.path.join(REPO, "data", "_bt_year", "20260310"))
    a = ap.parse_args()
    os.environ["DATABASE_URL"] = a.db_url
    os.environ["DATA_DIR"] = a.data_dir
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    for sub in ("backend", "apps/main_line", "core", "jobs", ""):
        p = os.path.join(REPO, sub) if sub else REPO
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)

    import importlib.util
    spec = importlib.util.spec_from_file_location("bfd", os.path.join(JOBS, "bt_pg_backfill_diagnosis.py"))
    bfd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bfd)

    from app.services import t_regime, trade_graph
    from app.api import indicator

    # ── 隔离 L2/L3 + 不写 t_regime_state（只留 L1 的差异）──
    t_regime._fetch_index_quotes = lambda: {}
    t_regime.t_db.upsert_regime_state = lambda *x, **k: None
    real_read = t_regime._read_market_diagnosis

    import datetime as _dtm

    def _rebind_datetime():
        """把 t_regime / trade_graph 里**模块级** `from datetime import datetime` 的引用换成当前钉钟类。

        ⚠️ 这是本探针的必需动作，也是**回测驱动的既有约定**：`jobs/bt_prod_run.py` 先 `_pin_clock_dynamic()`
        再 import 生产模块，于是 `from datetime import datetime` 拿到的就是钉钟类；本探针是先 import 再钉钟
        （想反复钉不同日期），所以必须手动把模块属性重新指到 `datetime.datetime`（= 当前钉钟类）。
        若不做这一步，`t_regime._read_market_diagnosis()` 会拿**真实墙钟日期**去查表（实测 → 永远 default）。
        """
        t_regime.datetime = _dtm.datetime
        trade_graph.datetime = _dtm.datetime
        indicator.datetime = _dtm.datetime

    def gate(day):
        bfd.pin_clock(day, "1030")
        _rebind_datetime()
        t_regime._read_market_diagnosis = lambda: None
        t_regime._regime_cache["result"] = None
        before = t_regime.compute_regime(force=True)
        t_regime._read_market_diagnosis = real_read
        t_regime._regime_cache["result"] = None
        after = t_regime.compute_regime(force=True)
        return before, after

    def fmt(r):
        return "regime=%-8s gate_low_buy=%-11s gate_high_sell=%-8s regime_day=%-8s daily_source=%s" % (
            r.get("regime"), r.get("gate_low_buy"), r.get("gate_high_sell"),
            r.get("regime_day"), r.get("daily_source"))

    print("== 1) t_regime.compute_regime()：补之前（无行） vs 补之后（有行）==")
    for day in [d.strip() for d in a.days.split(",") if d.strip()]:
        before, after = gate(day)
        print("[%s]" % day)
        print("   BEFORE(无当日行) %s" % fmt(before))
        print("   AFTER (回填后)   %s" % fmt(after))
        chg = [k for k in ("regime", "gate_low_buy", "gate_high_sell")
               if before.get(k) != after.get(k)]
        print("   → 变化字段: %s" % (("、".join(chg) + "  " + str({k: (before.get(k), after.get(k)) for k in chg}))
                                    if chg else "无（regime/gate 三项全同）"))

    print("\n== 2) 另外两个读取方（trade_graph / indicator）==")
    for day in [d.strip() for d in a.days.split(",") if d.strip()] + [a.hole]:
        bfd.pin_clock(day, "1030")
        _rebind_datetime()
        tg = trade_graph._read_market_regime()
        ic = indicator._get_market_regime_for_calc()
        tag = "（真实缺行日 = before 语义）" if day == a.hole else ""
        print("[%s]%s trade_graph=%s ｜ indicator._get_market_regime_for_calc()=%s"
              % (day, tag, (tg[0], tg[1]), ic))
    return 0


if __name__ == "__main__":
    sys.exit(main())
