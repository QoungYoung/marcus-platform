# -*- coding: utf-8 -*-
"""主题「主类」判定（确定性，替代/前置 dsh 主营校验）。

背景（2026-09-19 用户点出）：
  PG `stock_concept_map` 里每个标的除了一堆"沾边概念"外，还带**行业类概念词**（主类）：
    真半导体   : 兆易创新/中芯国际/瑞芯微/中微公司/紫光国微 → 主类 = 「半导体」
    错配票     : 电子城 → 产业地产·房地产；华泰股份 → 大宗用纸·造纸；科德教育 → 在线教育·教育；
                 日科化学 → 化学制品·基础化工；快克智能/科瑞技术 → 机械设备·自动化设备·工业母机
  狼大按**板块内正宗度/龙头**选票（docs/wolf-buy-parameter-ledger.md:1050），不会因为名字带"电子"就当科技票。
  所以「主类 ∈ 该主题的行业集」就是可复现的判定，不必每次问 LLM。

判定三分支：
  · in     主类 ∩ 主题行业集 ≠ ∅            → 放行（确定）
  · out    主类 ≠ ∅ 且 与之无交集            → 拦（确定：电子城/华泰/科德/日科/快克智能都落这里）
  · unknown 没有主类词（概念全是"融资融券/昨日涨停"这类标签）→ **defer**：交回 dsh 判（兜底）
开关：`WOLF_MAIN_CLASS_RULE`（库内默认关 = 生产逐位不变；回测默认开）。任何异常 fail-open（defer→dsh）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


RULE_ON = str(os.getenv("WOLF_MAIN_CLASS_RULE",
                        "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")

# ── 行业类概念词（主类词表；来源：东财行业/申万二级在 stock_concept_map 中的写法，实测样本校准）──
INDUSTRY_WORDS = {
    # TMT / 科技
    "半导体", "集成电路", "电子元件", "光学光电子", "消费电子", "计算机设备", "软件开发",
    "计算机应用", "通信设备", "通信服务", "互联网服务", "IT服务", "电子化学品", "元件",
    # 制造 / 设备
    "机械设备", "专用设备", "通用设备", "自动化设备", "工控设备", "工业母机", "机器人概念",
    "仪器仪表", "交运设备", "船舶制造", "航天航空", "军工电子", "工程机械",
    # 新能源 / 电力
    "电池", "光伏设备", "风电设备", "电网设备", "电源设备", "能源金属", "电力行业",
    "燃气", "公用事业", "环保行业", "储能设备",
    # 汽车
    "汽车整车", "汽车零部件", "汽车服务", "摩托车",
    # 化工 / 材料
    "化学制品", "基础化工", "化学原料", "化学制药", "化纤行业", "塑料制品", "橡胶制品",
    "农药兽药", "化肥行业", "玻璃玻纤", "水泥建材", "装修建材", "包装材料", "造纸印刷",
    "钢铁行业", "有色金属", "小金属", "贵金属", "煤炭行业", "石油行业", "能源化工",
    # 消费
    "食品饮料", "白酒", "家用电器", "纺织服装", "商业百货", "旅游酒店", "农牧饲渔",
    "农产品加工", "食品加工", "美容护理", "教育", "在线教育", "培训教育", "传媒", "游戏Ⅱ",
    "游戏Ⅲ", "网络游戏", "文化传媒", "体育产业",
    # 医药
    "医药商业", "医疗器械", "生物制品", "中药", "医疗服务", "化学制剂", "原料药",
    # 金融 / 地产 / 基建
    "银行", "证券", "保险", "多元金融", "房地产开发", "房地产服务", "产业地产", "房地产",
    "工程建设", "建筑装饰", "基础建设", "物流行业", "航空机场", "铁路公路", "港口航运",
    # 其它
    "大宗用纸", "造纸", "林业", "渔业", "种植业", "饲料",
}

# ── 主题 → 该主题的行业集（主类命中即"正宗"）。只覆盖系统在用的 14 个主题 ──
THEME_MAIN_CLASS: Dict[str, set] = {
    "半导体/芯片": {"半导体", "集成电路", "电子化学品"},
    "AI/算力/科技": {"半导体", "集成电路", "计算机设备", "软件开发", "计算机应用", "通信设备",
                     "通信服务", "互联网服务", "IT服务", "消费电子", "光学光电子", "电子元件"},
    "机器人/智能制造": {"机械设备", "专用设备", "通用设备", "自动化设备", "工控设备", "工业母机",
                        "机器人概念", "仪器仪表", "工程机械"},
    "新能源/电池": {"电池", "光伏设备", "风电设备", "电网设备", "电源设备", "能源金属", "储能设备"},
    "汽车/智驾": {"汽车整车", "汽车零部件", "汽车服务", "交运设备", "电池", "电源设备"},
    "军工/航天": {"航天航空", "军工电子", "船舶制造", "交运设备", "通信设备"},
    "资源/周期": {"有色金属", "小金属", "贵金属", "钢铁行业", "煤炭行业", "石油行业",
                  "化学原料", "基础化工", "能源化工", "水泥建材", "玻璃玻纤"},
    "金融": {"银行", "证券", "保险", "多元金融"},
    "消费/内需": {"食品饮料", "白酒", "家用电器", "纺织服装", "商业百货", "旅游酒店",
                  "农牧饲渔", "农产品加工", "美容护理", "食品加工"},
    "医药": {"医药商业", "医疗器械", "生物制品", "中药", "医疗服务", "化学制剂", "原料药", "化学制药"},
    "稳增长/基建": {"工程建设", "建筑装饰", "基础建设", "水泥建材", "装修建材", "钢铁行业",
                    "房地产开发", "机械设备", "工程机械"},
    "电力/公用": {"电力行业", "燃气", "公用事业", "环保行业", "电网设备"},
    "传媒/游戏": {"传媒", "游戏Ⅱ", "游戏Ⅲ", "网络游戏", "文化传媒", "互联网服务"},
    "农业": {"农牧饲渔", "农产品加工", "种植业", "饲料", "渔业", "林业", "农药兽药", "化肥行业"},
}

_CACHE: Dict[str, List[str]] = {}
_STATS: Dict[str, int] = {"calls": 0, "in": 0, "out": 0, "unknown": 0, "errors": 0}


def stats() -> Dict[str, Any]:
    return {"on": RULE_ON, **_STATS}


def concepts_of(symbol: str) -> List[str]:
    """该票自身概念（PG）。失败 → []（fail-open）。"""
    s = str(symbol or "").strip().upper()
    if not s:
        return []
    if s in _CACHE:
        return _CACHE[s]
    out: List[str] = []
    try:
        import psycopg2
        code = ("%s.%s" % (s[2:], s[:2])) if (len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ")) else s
        _dsn = os.getenv("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
        conn = psycopg2.connect(_dsn, connect_timeout=4)
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SET statement_timeout=4000")
            cur.execute("SELECT concept_name FROM stock_concept_map WHERE ts_code=%s", (code,))
            out = [str(r[0]) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        _STATS["errors"] += 1
    _CACHE[s] = out
    return out


def main_classes(concepts: Optional[Sequence[str]] = None, symbol: str = "") -> List[str]:
    cs = list(concepts) if concepts is not None else concepts_of(symbol)
    return [c for c in cs if str(c) in INDUSTRY_WORDS]


def decide(symbol: str, theme: str, concepts: Optional[Sequence[str]] = None) -> Tuple[str, str]:
    """→ ("allow"|"reject"|"defer", 说明)。开关关/异常 ⇒ ("defer", ...)。"""
    if not RULE_ON:
        return "defer", "主类规则关(WOLF_MAIN_CLASS_RULE=0)"
    _STATS["calls"] += 1
    try:
        # 调用方可能传空列表表示"没给概念" ⇒ 与 None 同义，去 PG 取（否则会误判成 defer）
        _cs = list(concepts) if concepts else None
        mc = main_classes(_cs, symbol)
        if not mc:
            _STATS["unknown"] += 1
            return "defer", "无主类词（概念全是标签）→ 交 dsh 兜底"
        allow_set = THEME_MAIN_CLASS.get(str(theme))
        if not allow_set:
            _STATS["unknown"] += 1
            return "defer", "主题[%s]未配置主类集 → 交 dsh 兜底" % theme
        hit = sorted(set(mc) & allow_set)
        if hit:
            _STATS["in"] += 1
            return "allow", "主类=%s ∈ 主题[%s]" % ("/".join(hit), theme)
        # ── 账本 §9.461 ✓：主类表**本来会拒**时，再用「**正宗度 × 龙头度**」的**数据分**复核一次 ✓ ──
        #   用户 2026-10-03：「默认打开，别下次回测又关上了我不知道」✓ ⇒ `WOLF_THEME_SCORE_ACCEPT` 默认 1 ✓
        #   生产零影响 ✓：龙头度要 as-of 日线（bars.sqlite）⇒ **生产取不到 ⇒ 分=None ⇒ 判定不变** ✓
        try:
            import theme_leader_score as _tls
            if _tls.enabled():
                _sc, _why = _tls.score_for(symbol, theme, concepts=_cs)
                if _sc is not None and _sc >= _tls.min_score():
                    _STATS["in"] += 1
                    _STATS.setdefault("score_in", 0)
                    _STATS["score_in"] = int(_STATS.get("score_in", 0)) + 1
                    return "allow", ("主类=%s 不属主题[%s]，但**数据分通过** ✓：%s（阈值 %.2f）"
                                     % ("/".join(sorted(set(mc))[:4]), theme, _why, _tls.min_score()))
        except Exception as _e_sil1:
            _silent_alert("theme_main_class.py:153", _e_sil1)
        _STATS["out"] += 1
        return "reject", "主类=%s 不属主题[%s]" % ("/".join(sorted(set(mc))[:4]), theme)
    except Exception as e:
        _STATS["errors"] += 1
        return "defer", "主类判定异常 → 交 dsh: %s" % str(e)[:60]
