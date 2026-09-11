# -*- coding: utf-8 -*-
"""层①「无利空」公告判据单测（2026-09-11）。

狼大原话（2026-03-05）：「是自己逻辑的有效跌破 **除非是意外事件，黑天鹅那种**。
如果是**无利空**13日内下跌那新低后-3%就是逻辑问题 要控制损失就必须止损。后面涨是别的逻辑」

核心是**风险不对称**：误报（把常规消息当利空）= 该止损没止损（危险）；漏报 = 少豁免一次仍按①止损（安全）
→ 判据必须"宁可漏不可滥"。本测试把这条纪律钉死在用例里。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_neg_event as W  # noqa: E402


# ── 必须命中（意外事件/黑天鹅档） ──

class TestClassifyHits:
    def test_risk_types(self):
        # 注：「股份质押、冻结」已**刻意移出**白名单（2026-09-11 实测：该类型把当日 25 条
        #     常规质押/解质押全部误报进来，而质押不是黑天鹅）→ 由 TestProdTuningRegression 钉死。
        for t in ("风险提示", "退市风险提示", "停牌", "行政处罚", "立案调查", "诉讼", "仲裁",
                  "违规", "会计差错更正", "非标意见"):
            ok, why = W.classify_notice(t, "某某公司公告")
            assert ok is True, f"类型 {t} 应命中"
            assert why

    def test_title_phrases(self):
        cases = [
            "*ST清越:关于公司股票触及交易类强制退市的风险提示暨停牌的公告",
            "关于公司收到中国证监会立案告知书的公告",
            "关于公司及相关人员收到行政处罚决定书的公告",
            "关于控股股东所持股份被司法冻结的公告",
            "关于公司董事长被留置的公告",
            "关于全资子公司发生安全事故并停产的公告",
            "2026年半年度业绩预告：预计亏损",
            "关于审计机构出具无法表示意见审计报告的公告",
            "关于控股股东部分股份存在平仓风险的提示性公告",
        ]
        for s in cases:
            ok, why = W.classify_notice("其他", s)
            assert ok is True, f"标题应命中: {s}"
            assert "标题命中" in why

    def test_real_prod_samples(self):
        """生产实测样本（2026-09-11 当日真实公告标题）。"""
        for t, s in [("风险提示", "*ST清越:清越科技关于公司股票触及交易类强制退市的风险提示暨停牌的公告"),
                     ("处罚", "优彩资源:关于最近五年被证券监管部门和交易所处罚或采取监管措施情况的公告"),
                     ("诉讼", "*ST建艺:关于新增累计诉讼、仲裁情况的公告")]:
            assert W.classify_notice(t, s)[0] is True


# ── 必须**不**命中（否则会"该止不止"） ──

class TestClassifyMisses:
    def test_routine_financials(self):
        """常规财报同比小幅波动 **不是** 利空事件 —— 狼大语境里这属"无利空"，必须照常止损。"""
        ok, _ = W.classify_notice("其他", "贵州茅台600519.SH)：2026年中报净利润为445.17亿元、同比较去年同期下降1.95%")
        assert ok is False

    def test_common_low_risk_items(self):
        for t, s in [
            ("调研活动", "000591太阳能投资者关系管理信息20260911"),
            ("股份质押、冻结", "居然智家:关于控股股东部分股份质押的公告"),          # 常规质押 ≠ 黑天鹅
            ("其他", "翱捷科技:关于持股5%以上股东权益变动触及1%刻度、提前终止减持计划暨减持股份结果公告"),
            ("提供/对外担保公告", "汇成股份:关于为全资子公司提供担保的进展公告"),
            ("股东大会决议公告", "某某公司2026年第二次临时股东大会决议公告"),
            ("分配方案实施", "某某公司2025年年度权益分派实施公告"),
            ("法律意见书", "某某律师事务所关于公司2026年第二次临时股东大会的法律意见书"),
        ]:
            ok, why = W.classify_notice(t, s)
            assert ok is False, f"不应命中: {t} / {s}  (why={why})"

    def test_good_news_not_flagged(self):
        for s in ["关于签订重大合同的公告", "关于收到政府补助的公告", "关于产品中标的公告"]:
            assert W.classify_notice("其他", s)[0] is False

    def test_empty(self):
        assert W.classify_notice("", "") == (False, "")
        assert W.classify_notice(None, None) == (False, "")


# ── 过滤：只看持仓 ──

class TestScan:
    NOTICES = [
        {"code": "600519", "name": "贵州茅台", "title": "关于收到行政处罚决定书的公告", "ntype": "处罚", "date": "20260911"},
        {"code": "000001", "name": "平安银行", "title": "关于收到行政处罚决定书的公告", "ntype": "处罚", "date": "20260911"},
        {"code": "588170", "name": "科创半导体ETF", "title": "基金份额上市交易公告", "ntype": "其他", "date": "20260911"},
    ]

    def test_only_holdings(self):
        hits = W.scan(self.NOTICES, ["SH600519", "SH588170"])
        assert [h["symbol"] for h in hits] == ["SH600519"]

    def test_symbol_mapping_forms(self):
        for sym in ("SH600519", "600519.SH", "600519"):
            assert W.scan(self.NOTICES, [sym])[0]["symbol"] == sym


# ── 文件合并 / 清理 / fail-safe ──

class TestMergeAndWrite:
    def test_new_marker_schema(self):
        hits = [{"symbol": "SH600519", "code": "600519", "name": "贵州茅台",
                 "ntype": "处罚", "title": "关于收到行政处罚决定书的公告",
                 "date": "20260911", "why": "公告类型=处罚"}]
        d, touched = W.merge_events(hits, "20260911", cur={})
        assert touched == ["SH600519"]
        rec = d["SH600519"]
        assert rec["date"] == "20260911" and rec["source"] == "notice"
        assert "处罚" in rec["note"] and "行政处罚" in rec["note"]

    def test_unchanged_is_not_touched(self):
        hits = [{"symbol": "SH600519", "code": "600519", "name": "A", "ntype": "处罚",
                 "title": "T", "date": "20260911", "why": "x"}]
        d1, t1 = W.merge_events(hits, "20260911", cur={})
        d2, t2 = W.merge_events(hits, "20260911", cur=d1)
        assert t1 == ["SH600519"] and t2 == []      # 重复执行不刷 touched

    def test_expired_purged(self):
        cur = {"SH600000": {"date": "20260601", "note": "老事件"},
               "SH600001": {"date": "20260910", "note": "新事件"}}
        d, _ = W.merge_events([], "20260911", keep_days=30, cur=cur)
        assert "SH600000" not in d and "SH600001" in d

    def test_multiple_same_day_merged(self):
        hits = [{"symbol": "SH600519", "code": "600519", "name": "A", "ntype": "立案",
                 "title": "立案调查", "date": "20260911", "why": "x"},
                {"symbol": "SH600519", "code": "600519", "name": "A", "ntype": "诉讼",
                 "title": "重大诉讼", "date": "20260911", "why": "y"}]
        d, _ = W.merge_events(hits, "20260911", cur={})
        assert "立案调查" in d["SH600519"]["note"] and "重大诉讼" in d["SH600519"]["note"]

    def test_fetch_failure_does_not_touch_file(self, tmp_path, monkeypatch):
        """**fail-safe**: 拉不到公告时绝不能动既有标记文件（否则等于凭空清空豁免）。"""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        p = tmp_path / W.NEG_EVENT_FILE
        p.write_text(json.dumps({"SH600519": {"date": "20260911", "note": "原有"}}), encoding="utf-8")
        before = p.read_text(encoding="utf-8")
        res = W.scan_and_mark(["SH600519"], "20260911", notices=None)
        # 本机无网/无 akshare 时 fetch 返回 None → 走 fail-safe；有网时也允许成功
        if res.get("reason") == "fetch_failed":
            assert p.read_text(encoding="utf-8") == before

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        notices = [{"code": "600519", "name": "A", "ntype": "处罚", "title": "行政处罚决定书", "date": "20260911"}]
        res = W.scan_and_mark(["SH600519"], "20260911", dry_run=True, notices=notices)
        assert res["hits"] == 1 and res["written"] is False
        assert not (tmp_path / W.NEG_EVENT_FILE).exists()

    def test_write_then_read_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        notices = [{"code": "600519", "name": "A", "ntype": "处罚", "title": "行政处罚决定书", "date": "20260911"}]
        res = W.scan_and_mark(["SH600519"], "20260911", dry_run=False, notices=notices)
        assert res["written"] is True
        d = W.load_events()
        assert d["SH600519"]["source"] == "notice"


# ── 与读侧（① 的两条机制）联通 ──

class TestReaderIntegration:
    def test_resolve_stop_exempts_on_marker(self, tmp_path, monkeypatch):
        """写进文件的标记必须真的让 ①结构止损 退回 stop_loss_price（跨模块联通）。"""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        (tmp_path / W.NEG_EVENT_FILE).write_text(
            json.dumps({"SH600519": {"date": "20260818", "note": "公告类型=处罚", "source": "notice"}}),
            encoding="utf-8")
        from app.services import wolf_early_stop as E
        bars = [{"date": "202608%02d" % d, "close": 10, "high": 11, "low": 9, "vol": 1} for d in range(1, 31)]
        stop, src, why = E.resolve_stop(9.5, bars, "20260817", symbol="SH600519", today="20260818")
        assert (stop, src) == (9.5, "neg_event")
        assert "别的逻辑" in why

    def test_logic_time_stop_exempts_on_marker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        (tmp_path / W.NEG_EVENT_FILE).write_text(
            json.dumps({"SH600519": {"date": "20260818", "note": "公告类型=诉讼", "source": "notice"}}),
            encoding="utf-8")
        from app.services import wolf_early_stop as E
        bars = [{"date": "202608%02d" % d, "close": 10, "high": (12 if d <= 10 else 11),
                 "low": 9, "vol": 1} for d in range(1, 31)]
        ok, why = E.logic_time_stop(bars, "20260810", symbol="SH600519", today="20260818")
        assert ok is False and "别的逻辑" in why

    def test_no_marker_means_normal_stop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        from app.services import wolf_early_stop as E
        bars = [{"date": "202608%02d" % d, "close": 10, "high": 11, "low": 9, "vol": 1} for d in range(1, 31)]
        _, src, _ = E.resolve_stop(9.5, bars, "20260817", symbol="SH600519", today="20260818")
        assert src == "wolf_early_swing"

# ── 生产实测调参回归（2026-09-11：当日 1144 条公告里 38 条命中 → 收紧到 16 条） ──

class TestProdTuningRegression:
    """这些是**当日真实公告**，收紧前会误报。误报=该止不止，必须钉死。"""

    def test_pledge_family_is_not_flagged(self):
        """质押/解质押/展期是常态（当日误报主因：类型 `股份质押、冻结` 含"冻结"二字）。"""
        for t, s in [
            ("股份质押、冻结", "爱旭股份:关于控股股东部分股份质押及解除质押的公告"),
            ("股份质押、冻结", "钧达股份:关于控股股东部分股份解除质押的公告"),
            ("股份质押、冻结", "朗姿股份:关于公司控股股东解除质押及质押展期的公告"),
            ("股份质押、冻结", "水晶光电:关于控股股东部分股份解除质押及质押的公告"),
            ("股份质押、冻结", "岳阳兴长:关于持股5%以上股东之一致行动人股份解除质押及再质押的公告"),
            ("股份质押、冻结", "瑞鹄模具:关于控股股东部分股权质押的公告"),
        ]:
            assert W.classify_notice(t, s)[0] is False, f"质押家族不应命中: {s}"

    def test_real_freeze_is_still_flagged(self):
        """真·冻结仍要命中（走标题判据：被冻结/轮候冻结/账户冻结）。"""
        for t, s in [
            ("其他", "汇通控股:关于公司募集资金账户部分资金被冻结的公告"),
            ("股份质押、冻结", "衢州发展:关于5%以上股东股份被轮候冻结的公告"),
        ]:
            assert W.classify_notice(t, s)[0] is True, f"真冻结应命中: {s}"

    def test_convertible_price_revision_not_flagged(self):
        """转股价下修对正股不是黑天鹅（原 `下修` 泛匹配会误报）。"""
        assert W.classify_notice("其他", "昌红科技:关于董事会提议向下修正昌红转债转股价格的公告")[0] is False

    def test_merger_delisting_not_flagged(self):
        """换股吸收合并导致的"终止上市"是重组（通常利好），不是黑天鹅。"""
        s = "东兴证券:关于公司A股股票连续停牌直至终止上市、实施换股吸收合并的提示性公告"
        assert W.classify_notice("终止上市提示公告", s)[0] is False

    def test_controller_change_alone_not_flagged(self):
        """实控人"变更"方向不定（可能是被收购＝利好）→ 只认 被查/失联/留置/平仓。"""
        for s in ["优彩资源:关于公司控股股东、实际控制人拟发生变更的提示性公告",
                  "居然智家:关于实际控制人发生变更暨股东权益变动的进展公告"]:
            assert W.classify_notice("公司关联方基本资料变更", s)[0] is False

    def test_controller_detained_still_flagged(self):
        assert W.classify_notice("其他", "某某公司关于实际控制人被留置的公告")[0] is True
        assert W.classify_notice("其他", "某某公司关于控股股东部分股份存在平仓风险的提示性公告")[0] is True

    def test_star_st_delisting_still_flagged(self):
        for s in ["*ST清越:清越科技关于收到上海证券交易所终止上市事先告知书的公告",
                  "*ST清越:清越科技股票交易异常波动暨将被终止上市风险提示公告"]:
            assert W.classify_notice("终止上市风险提示", s)[0] is True
