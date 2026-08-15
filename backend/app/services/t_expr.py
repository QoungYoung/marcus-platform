# -*- coding: utf-8 -*-
"""做T系统 · 自由条件表达式（Agent 可监控任意数据字段）。

设计目标：Agent 不再被写死的 trigger_kind/固定字段束缚，可组合任意字段编写触发条件。

字段注册表（FIELD_REGISTRY）：TMonitor 每轮采集的快照字段，Agent 可监控全部。
表达式 DSL：t_conditions.expression JSONB 规则树，op 集合受限，无 eval，杜绝注入。
安全：表达式只控制"何时触发"；触发后仍走网关三阶校验 + 可卖底仓 + regime 门 + 时段 + 去抖。

表达式示例：
{
  "and": [
    {"field": "quote.current", "op": "<=", "value": 98},
    {"field": "vol_ratio", "op": ">=", "value": 1.5},
    {"field": "minute.m1.bounce", "op": "==", "value": true},
    {"field": "regime.state", "op": "in", "value": ["ACTIVE", "CAUTIOUS"]}
  ]
}
支持 "or" / "not" 组合（规则树递归求值）。
"""
from typing import Any, Dict, List, Optional, Union

# ── 字段注册表：Agent 可监控的全部字段（TMonitor 采集后填入快照） ──
# 结构: 字段名 -> (类型, 说明, 示例来源)
FIELD_REGISTRY: Dict[str, Dict[str, Any]] = {
    # 腾讯 qt 实时行情（每轮采集）
    "quote.current": ("number", "最新价", "fetch_tencent_quote"),
    "quote.open": ("number", "今开", "fetch_tencent_quote"),
    "quote.high": ("number", "最高", "fetch_tencent_quote"),
    "quote.low": ("number", "最低", "fetch_tencent_quote"),
    "quote.pre_close": ("number", "昨收", "fetch_tencent_quote"),
    "quote.change_pct": ("number", "涨跌幅%", "fetch_tencent_quote"),
    "quote.turnover_rate": ("number", "换手率%", "fetch_tencent_quote"),
    "quote.amplitude": ("number", "振幅%", "fetch_tencent_quote"),
    "quote.vol": ("number", "成交量(手)", "fetch_tencent_quote"),
    "quote.amount": ("number", "成交额(万)", "fetch_tencent_quote"),
    # ── 量价关系派生字段（贴近交易语言，单字段表达复合语义） ──
    "quote.volume_expand": ("bool", "放量（量比≥1.5）", "t_monitor 派生"),
    "quote.volume_shrink": ("bool", "缩量（量比≤0.7）", "t_monitor 派生"),
    "quote.price_up": ("bool", "上涨（涨跌幅>0）", "t_monitor 派生"),
    "quote.price_down": ("bool", "下跌（涨跌幅<0）", "t_monitor 派生"),
    "quote.up_with_volume": ("bool", "放量上涨（价涨∧量比≥1.5）", "t_monitor 派生"),
    "quote.up_with_low_volume": ("bool", "缩量上涨（价涨∧量比≤0.7）", "t_monitor 派生"),
    "quote.down_with_volume": ("bool", "放量下跌（价跌∧量比≥1.5）", "t_monitor 派生"),
    "quote.down_with_low_volume": ("bool", "缩量下跌（价跌∧量比≤0.7）", "t_monitor 派生"),
    "quote.panic_drop": ("bool", "恐慌放量下跌（跌超2%∧量比≥2）", "t_monitor 派生"),
    "quote.near_day_low": ("bool", "接近日内低点（现价≤日低×1.01）", "t_monitor 派生"),
    "quote.stabilised": ("bool", "分时企稳（不再创新低）", "t_monitor 派生"),
    # 盘中量比归一（TMonitor 计算）
    "vol_ratio": ("number", "盘中量比(时段归一)", "t_monitor._calc_volume_ratio"),
    # 分钟线衍生（腾讯 m5/m1，低频采集）
    "minute.m1.low_today": ("number", "当日1分钟最低价", "fetch_minute_bars m1"),
    "minute.m1.last_close": ("number", "1分钟最新收盘", "fetch_minute_bars m1"),
    "minute.m1.bounce": ("bool", "分时企稳(未创新低)", "t_monitor._stabilize_not_new_low"),
    "minute.m5.ma5": ("number", "5分钟线MA5", "fetch_minute_bars m5"),
    "minute.m5.ma10": ("number", "5分钟线MA10", "fetch_minute_bars m5"),
    "minute.m5.ma20": ("number", "5分钟线MA20", "fetch_minute_bars m5"),
    "minute.m5.last_close": ("number", "5分钟最新收盘", "fetch_minute_bars m5"),
    # regime 环境闸门（每轮计算）
    "regime.state": ("string", "环境档位 ACTIVE/CAUTIOUS/HALT", "t_regime.compute_regime"),
    "regime.gate_low_buy": ("string", "低吸闸门 ALLOWED/MANUAL_ONLY/BLOCKED", "t_regime"),
    "regime.gate_high_sell": ("string", "高抛闸门", "t_regime"),
    "regime.interpret_sign": ("number", "量能解读符号 +1/-1/0", "t_regime"),
    # 持仓（t 账户）
    "position.sellable": ("number", "当日可卖底仓(股)", "t_gateway.get_sellable_ledger"),
    "position.volume": ("number", "总持仓(股)", "paper_positions"),
    "position.avg_price": ("number", "持仓成本", "paper_positions"),
    "position.pnl_pct": ("number", "持仓盈亏%", "paper_positions 计算"),
    # 指数实时（regime L2 前提）
    "index.hs300_drop": ("number", "沪深300当日涨跌幅%", "fetch_tencent_quote sh000300"),
    "index.sh_drop": ("number", "上证指数当日涨跌幅%", "fetch_tencent_quote sh000001"),
    "index.sz_drop": ("number", "深证成指当日涨跌幅%", "fetch_tencent_quote sz399001"),
    # ── 技术指标（复用 get_realtime_indicators：KDJ/MACD/RSI/MA，盘中实时估算） ──
    "tech.ma5": ("number", "MA5(日线)", "get_realtime_indicators"),
    "tech.ma10": ("number", "MA10(日线)", "get_realtime_indicators"),
    "tech.ma20": ("number", "MA20(日线)", "get_realtime_indicators"),
    "tech.ma60": ("number", "MA60(日线)", "get_realtime_indicators"),
    "tech.macd_dif": ("number", "MACD DIF", "get_realtime_indicators"),
    "tech.macd_dea": ("number", "MACD DEA", "get_realtime_indicators"),
    "tech.macd_bar": ("number", "MACD 柱(MACD=DIF-DEA)*2", "get_realtime_indicators"),
    "tech.macd_golden_cross": ("bool", "MACD金叉(DIF>DEA)", "get_realtime_indicators 派生"),
    "tech.kdj_k": ("number", "KDJ K", "get_realtime_indicators"),
    "tech.kdj_d": ("number", "KDJ D", "get_realtime_indicators"),
    "tech.kdj_j": ("number", "KDJ J", "get_realtime_indicators"),
    "tech.kdj_golden_cross": ("bool", "KDJ金叉(K>D)", "get_realtime_indicators 派生"),
    "tech.kdj_overbought": ("bool", "KDJ超买(J>100 或 K>80)", "get_realtime_indicators 派生"),
    "tech.rsi_6": ("number", "RSI6", "get_realtime_indicators"),
    "tech.rsi_12": ("number", "RSI12", "get_realtime_indicators"),
    "tech.rsi_24": ("number", "RSI24", "get_realtime_indicators"),
    "tech.rsi_overbought": ("bool", "RSI6超买(≥80)", "get_realtime_indicators 派生"),
    "tech.rsi_oversold": ("bool", "RSI6超卖(≤20)", "get_realtime_indicators 派生"),
    "tech.above_ma5": ("bool", "现价在MA5上方", "get_realtime_indicators 派生"),
    "tech.above_ma20": ("bool", "现价在MA20上方", "get_realtime_indicators 派生"),
}

# 合法操作符（受限集合，防止任意代码）
ALLOWED_OPS = {">", ">=", "<", "<=", "==", "!=", "in", "not_in", "between"}
# 合法组合键
ALLOWED_COMBINATORS = {"and", "or", "not"}

# 表达式求值递归深度上限（防深递归）
_MAX_DEPTH = 16


class ExprError(Exception):
    """表达式结构错误（非法字段/操作符/深度）。"""


def validate_expression(expr: Any, depth: int = 0) -> None:
    """校验表达式结构（写入前调用，非法即抛错）。"""
    if depth > _MAX_DEPTH:
        raise ExprError("表达式嵌套过深")
    if expr is None:
        return  # 空表达式视为不限制（回退默认逻辑）
    if not isinstance(expr, dict):
        raise ExprError("表达式必须是 JSON 对象")
    if "field" in expr:
        # 叶子节点（单条件）：含 field/op/value
        _validate_leaf(expr)
        return
    if len(expr) != 1:
        raise ExprError("组合器每层只能有一个键 (and/or/not)")
    key, val = next(iter(expr.items()))
    if key in ("and", "or"):
        if not isinstance(val, list) or not val:
            raise ExprError(f"{key} 必须是非空数组")
        for sub in val:
            validate_expression(sub, depth + 1)
    elif key == "not":
        validate_expression(val, depth + 1)
    else:
        raise ExprError(f"非法表达式键: {key}（允许 and/or/not 或叶子 field）")


def _validate_leaf(leaf: dict) -> None:
    """校验叶子节点（单条件）。"""
    field = leaf.get("field")
    op = leaf.get("op")
    if not isinstance(field, str) or field not in FIELD_REGISTRY:
        raise ExprError(f"非法字段: {field}（可用字段见 FIELD_REGISTRY）")
    if op not in ALLOWED_OPS:
        raise ExprError(f"非法操作符: {op}（允许: {sorted(ALLOWED_OPS)}）")
    if "value" not in leaf:
        raise ExprError("单条件缺少 value")
    if op == "between" and "value2" not in leaf:
        raise ExprError("between 需要 value2")


def evaluate_expression(expr: Any, snapshot: Dict[str, Any], depth: int = 0) -> bool:
    """求值表达式。snapshot 为 TMonitor 采集的字段快照 {field: value}。

    安全：仅按受限 op 集合比较，不执行任何代码。
    """
    if expr is None:
        return True  # 空表达式 = 不限制（回退默认逻辑由 TMonitor 处理）
    if depth > _MAX_DEPTH:
        return False
    if not isinstance(expr, dict):
        return False
    if "field" in expr:
        # 叶子节点（单条件）
        field = expr.get("field")
        op = expr.get("op")
        expected = expr.get("value")
        actual = _snapshot_get(snapshot, field)
        if actual is None:
            return False  # 字段不可得（数据缺失）→ 不触发（保守）
        try:
            return _compare(actual, op, expected, expr.get("value2"))
        except (TypeError, ValueError):
            return False
    if len(expr) != 1:
        return False
    key, val = next(iter(expr.items()))
    if key == "and":
        return all(evaluate_expression(sub, snapshot, depth + 1) for sub in val)
    if key == "or":
        return any(evaluate_expression(sub, snapshot, depth + 1) for sub in val)
    if key == "not":
        return not evaluate_expression(val, snapshot, depth + 1)
    return False


def _snapshot_get(snapshot: Dict[str, Any], field: str) -> Any:
    """从快照取值（支持点路径）。"""
    cur: Any = snapshot
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _to_num(v: Any) -> float:
    """数值化（容忍字符串）。"""
    if isinstance(v, bool):
        return float(v)
    return float(v)


def _compare(actual: Any, op: str, expected: Any, expected2: Any = None) -> bool:
    if op == "==":
        return _eq(actual, expected)
    if op == "!=":
        return not _eq(actual, expected)
    if op in (">", ">=", "<", "<="):
        a, b = _to_num(actual), _to_num(expected)
        return {
            ">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b,
        }[op]
    if op == "in":
        if not isinstance(expected, list):
            return False
        return any(_eq(actual, e) for e in expected)
    if op == "not_in":
        if not isinstance(expected, list):
            return True
        return not any(_eq(actual, e) for e in expected)
    if op == "between":
        if expected2 is None:
            return False
        a, lo, hi = _to_num(actual), _to_num(expected), _to_num(expected2)
        return lo <= a <= hi
    return False


def _eq(a: Any, b: Any) -> bool:
    """宽松相等：数值比较容忍 int/float/str，字符串精确。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    try:
        return _to_num(a) == _to_num(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def expression_summary(expr: Any) -> str:
    """表达式人类可读摘要（前端/审计显示）。"""
    if not expr:
        return "(无表达式)"
    try:
        return _render(expr)
    except Exception:
        return str(expr)


def _render(node: Any, depth: int = 0) -> str:
    if depth > _MAX_DEPTH or not isinstance(node, dict):
        return "..."
    if "field" in node:
        field = node.get("field", "?")
        op = node.get("op", "?")
        v = node.get("value")
        if op == "between":
            return f"{field} ∈ [{v}, {node.get('value2')}]"
        if op == "in":
            return f"{field} ∈ ({v})"
        if op == "not_in":
            return f"{field} ∉ ({v})"
        return f"{field} {op} {v}"
    if len(node) != 1:
        return "..."
    key, val = next(iter(node.items()))
    if key == "and":
        return "(" + " AND ".join(_render(s, depth + 1) for s in val) + ")"
    if key == "or":
        return "(" + " OR ".join(_render(s, depth + 1) for s in val) + ")"
    if key == "not":
        return "NOT " + _render(val, depth + 1)
    return "..."
