# -*- coding: utf-8 -*-
"""黄金坑报告与预警 — 盘前报告、阈值穿越预警、自然语言解读。"""
from typing import Any, Dict, List, Optional

from app.services.golden_pit_config import (
    PIT_WINDOW_DAYS,
    PERCENTILE_GOLDEN_PIT,
    PERCENTILE_WARNING,
    SIGNAL_QUALITY_LABEL,
    STATUS_MAP,
)
from app.services.golden_pit_repository import load_previous_percentile


def format_morning_report(status: Dict[str, Any]) -> str:
    """生成 QQ 盘前报告 (8:50 AM)。"""
    if status is None:
        status = self.get_status()

    as_of = status["as_of"]
    window = status["golden_pit_window"]
    indices = status["indices"]
    conf = status["triple_confirmation"]
    pred = status["prediction"]

    lines = [f"📊 黄金坑盘前报告 — {as_of}", "━━━━━━━━━━━━━━━━━━━", ""]

    # 按 tier 分组显示
    tier_order = ["core", "satellite", "defense", "semi_boost", "defense_rotation", "watch", "drop"]
    tier_labels = {
        "core": "🏆 核心 (必做)", "satellite": "📡 卫星 (选做)",
        "defense": "🛡 防御 (可选)", "semi_boost": "🔬 半导体增强 (坑内10%)",
        "defense_rotation": "🛡 防御轮动 (撤场承接)", "watch": "👀 观察 (仅预警)", "drop": "❌ 放弃",
    }
    tier_icons = {"golden_pit": "🔴", "warning": "🟠", "normal": "🟢"}

    pit_count = 0
    for tier_name in tier_order:
        tier_indices = [i for i in indices if i.get("tier") == tier_name]
        if not tier_indices:
            continue
        tier_indices.sort(key=lambda x: x["priority"])
        lines.append(tier_labels.get(tier_name, tier_name))

        for idx in tier_indices:
            icon = tier_icons.get(idx["status"], "⚪")
            detail = ""
            if idx["status"] == "golden_pit" and idx.get("entry_date"):
                pit_count += 1
                detail = f" ({idx['entry_date']}入坑，第{idx.get('days_in_pit', '?')}天)"
                if idx.get("absolute_triggered"):
                    detail += " ★双重确认"
            elif idx["status"] == "warning":
                dw = idx.get("days_in_warning", 0)
                if dw > 0:
                    detail = f" (P10第{dw}天"
                    if idx.get("days_to_pit"):
                        detail += f"，预计{idx['eta_date']}入坑"
                    if idx.get("is_fake_signal"):
                        detail += " ⚠假信号风险"
                    detail += ")"
            elif idx.get("decline_rate"):
                detail = f" (日跌{idx['decline_rate']:.3f})"

            # 趋势方向
            trend_icon = {"declining": "↓", "bottoming": "→", "recovering": "↑"}.get(
                idx.get("trend", ""), "")
            trend_label = {"declining": "跌", "bottoming": "底", "recovering": "升"}.get(
                idx.get("trend", ""), "")

            # 信号质量 + 仓位建议
            sq = idx.get("signal_quality", "")
            sq_short = {"strong": "强", "good": "中", "weak": "弱", "inferred": "?"}.get(sq, "")
            ds_tag = "[价]" if idx.get("data_source") == "pi_server_price" else ""
            pos_label = ""
            if idx.get("position_tier_label") and idx.get("tier") not in ("drop", "watch"):
                pos_label = f" → {idx['position_tier_label']}"

            lines.append(
                f"{icon} {idx['index_name']:6s} {idx['greed']:.2f}  "
                f"P{idx['percentile']:.0f} {trend_icon}{trend_label} "
                f"{STATUS_MAP[idx['status']]['label']}{detail}"
                f"  [{sq_short}]{ds_tag}{pos_label}"
            )
        lines.append("")

    lines.append("")

    # ── 板块拆分（guide_only 宽基选筹摘要）──
    sector_sel = status.get("sector_selection")
    guide_active = [
        i for i in indices
        if i.get("guide_only") and i["status"] in ("golden_pit", "warning")
    ]
    if sector_sel is not None and guide_active:
        mode = "执行" if status.get("sector_split_enabled") else "展示(dry-run)"
        lines.append(f"🧭 板块拆分[{mode}]（guide_only 宽基: " +
                     "/".join(i["index_name"] for i in guide_active) + "）")
        selected = sector_sel.get("selected", [])
        if selected:
            for s in selected:
                lines.append(
                    f"   · {s['name']} ({s['sector']}) {s['weight'] * 100:.0f}%  "
                    f"combo={s['combo']} mf5={s['mf5_norm']:.2f} ovs={s['oversold120'] * 100:.1f}%"
                )
        else:
            lines.append(f"   · 等待板块信号（{sector_sel.get('empty_reason', '无信号')}）")
        lines.append("")

    phase = window.get("phase", "idle")
    if phase == "buying":
        rising = window.get("turning_leader_rising", 0)
        lines.append(
            f"📍 买入窗口：{window['leading_index']}拐点确认 "
            f"({window['start_date']}起, 第{window['current_day']}天, 已回升{rising}天)"
        )
        lines.append(f"   拐点确认: {window['turning_count']}个指数  加仓节奏: 50%→75%→100%")
    elif phase == "waiting":
        pit_count = window.get("pit_count", 0)
        warn_count = window.get("warning_count", 0)
        lines.append(
            f"📍 {pit_count}个指数已入黄金坑 ({warn_count}个预警)  "
            f"领先:{window['leading_index']}  |  等待贪婪值回升确认拐点"
        )
    else:
        lines.append("📍 当前无黄金坑信号")

    lines.append("")

    # 三重确认
    l1 = conf["layer1"]
    l2 = conf["layer2"]
    l3 = conf["layer3"]
    lines.append(f"{'☑' if l1['confirmed'] else '☐'} 蛋糕理论: {l1['status']}")
    lines.append(f"{'☑' if l2['confirmed'] else '☐'} 宽基确认: {l2['status']}")
    lines.append(f"{'☑' if l3['confirmed'] else '☐'} 细分板块: {l3['status']}")

    # 全球宏观
    gm = status.get("global_macro", {})
    if gm:
        gate_icon = "🔒" if gm.get("liquidity_gate") == "closed" else "🔓"
        lines.append(f"{gate_icon} 全球宏观: {gm.get('summary', '')}")
        # 资金持续流向
        cf = gm.get("capital_flow", {})
        if cf.get("summary"):
            lines.append(f"💰 资金流向: {cf['summary']}")
        # 背离警告
        divergent = [i for i in indices if i.get("turning_validation") == "divergent"]
        if divergent:
            names = ", ".join(i["index_name"] for i in divergent)
            lines.append(f"⚠️ 全球趋势背离: {names} 仓位已限制在拐点前水平")

    if pred and pred.get("next_index"):
        lines.append(f"💡 预测: {pred['next_index']} 预计 {pred['eta_days']} 天后入坑 ({pred['eta_date']})")

    turning_count = sum(1 for i in indices if i.get("turning_point_confirmed"))
    pre_count = sum(1 for i in indices if i.get("position_tier") == "pre_turn")
    if phase != "idle":
        if turning_count > 0:
            lines.append(f"💡 拐点已确认 ({turning_count}个指数): 快速加仓 50%→75%→100%")
        elif pre_count > 0:
            lines.append(f"💡 拐点前 ({pre_count}个指数): 轻仓累积, 等待贪婪值连续回升确认拐点")

    # 退出信号
    exit_indices = [i for i in indices if i.get("exit_signal")]
    if exit_indices:
        lines.append("")
        lines.append("🚪 退出信号:")
        for ei in exit_indices:
            icon = {"half_exit": "🟡", "full_exit": "🔴", "stop_profit": "🟠"}.get(ei["exit_signal"], "⚪")
            lines.append(f"  {icon} {ei['index_name']}: {ei['exit_reason']}")

    return "\n".join(lines)


def check_threshold_crossings(service, status: Optional[Dict[str, Any]] = None) -> List[str]:
    """检测阈值穿越，返回需要推送的预警消息列表。

    Args:
        status: 可选，传入已有的 status 避免重复 API 调用。不传则自动获取。
    """
    if status is None:
        status = service.get_status()
    indices = status["indices"]
    alerts = []

    # 加载昨日快照用于对比 (percentile 值)
    prev_percentile = load_previous_percentile()

    for idx in indices:
        code = idx["fund_code"]
        current_pct = idx["percentile"]
        prev_pct = prev_percentile.get(code)

        if prev_pct is None:
            continue

        # 各标的自身阈值（防御/半导体与成长指数不同，回测校准）
        entry_pct = idx.get("entry_pct") or PERCENTILE_WARNING
        pit_pct = idx.get("pit_pct") or PERCENTILE_GOLDEN_PIT

        # 检测预警线穿越 (percentile 从 >entry 变为 <=entry)
        if current_pct > entry_pct and prev_pct <= entry_pct:
            continue  # 反弹中，不预警
        if prev_pct > entry_pct and current_pct <= entry_pct:
            ds_tag = " [价格分位]" if idx.get("data_source") in ("pi_server_price", "defense_price") else ""
            alerts.append(
                f"⚠️ {idx['index_name']} 进入预警区 (分位 {idx['percentile']:.0f}%){ds_tag}\n"
                f"   📉 'greed': {idx['greed']:.4f}  "
                f"预计 {idx.get('eta_date', '?')} 进入黄金坑"
            )

        # 检测黄金坑确认 (percentile 从 >pit 变为 <=pit)
        if prev_pct > pit_pct and current_pct <= pit_pct:
            window = status["golden_pit_window"]
            abs_note = " [双重确认]" if idx.get("absolute_triggered") else ""
            ds_tag = " [价格分位]" if idx.get("data_source") in ("pi_server_price", "defense_price") else ""
            alerts.append(
                f"🔴 {idx['index_name']} 进入黄金坑！(分位 {idx['percentile']:.0f}%){abs_note}{ds_tag}\n"
                f"   📍 窗口：{window['start_date']} - {window['exit_date']}（{PIT_WINDOW_DAYS}交易日）\n"
                f"   📍 转折点预计：{window['midpoint_date']}\n"
                f"   📍 信号质量：{SIGNAL_QUALITY_LABEL.get(idx.get('signal_quality', ''), '未知')}\n"
                f"   💡 回测预期：15天 +{idx.get('expected_15d', '?')}% | 20天 +{idx.get('expected_20d', '?')}%"
            )

    return alerts


def build_v2_summary(indices, window, confirmation, prediction) -> str:
    """生成 v2 自然语言解读。"""
    parts = []

    pit_indices = [i for i in indices if i["status"] == "golden_pit"]
    warning_indices = [i for i in indices if i["status"] == "warning"]

    phase = window.get("phase", "idle")
    if phase == "buying":
        rising = window.get("turning_leader_rising", 0)
        parts.append(f"买入窗口已开启: {window['leading_index']}拐点确认，{window['start_date']}起第{window['current_day']}天。")
        parts.append(f"拐点确认{window['turning_count']}个指数，已回升{rising}天，加仓节奏50%→75%→100%。")
        strong = [i["index_name"] for i in pit_indices if i.get("signal_quality") == "strong"]
        if strong:
            parts.append(f"强信号: {', '.join(strong)}（回测Win%≥80%），优先加仓。")
    elif phase == "waiting":
        parts.append(f"黄金坑信号：{window['pit_count']}个指数已入坑/{window['warning_count']}个预警，但贪婪值仍在下跌中。")
        parts.append("黄金坑≠买入窗口。需等待贪婪值连续回升（拐点确认）后，才会开启买入窗口。当前仅轻仓累积(单次≤3%/累计≤15%)。")
    else:
        parts.append("当前无黄金坑信号，各宽基指数情绪正常。")

    if prediction and prediction.get("next_index"):
        parts.append(f"预测: {prediction['next_index']} 预计 {prediction['eta_days']} 天后进入黄金坑。")

    guide_only = [i["index_name"] for i in indices if i.get("guide_only") and i["status"] in ("golden_pit", "warning")]
    if guide_only:
        parts.append(f"板块拆分: {'、'.join(guide_only)} 仅作择时指导，坑内资金按板块 ETF 组合选筹配置（见选筹摘要）。")

    layers_ok = sum(1 for k in ["layer1", "layer2", "layer3"] if confirmation[k]["confirmed"])
    if layers_ok == 3:
        parts.append("三重确认全部达成，黄金坑信号高度可靠。")
    elif layers_ok >= 2:
        parts.append(f"三重确认达成{layers_ok}/3，信号可靠性中等。")

    return "".join(parts)

# ═══════════════════════════════════════════════════════════════
# 向后兼容 v1 API
