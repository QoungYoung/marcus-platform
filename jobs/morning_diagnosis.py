#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Marcus 盘前市场诊断脚本
执行时间：每个交易日 9:10

功能:
1. 调用 /market/market-diagnosis 端点获取5项指标
2. 格式化为易读的QQ推送消息
3. 输出到 stdout（scheduler 自动捕获并推送到QQ）
"""

import os
import sys
import json
import urllib.request
from datetime import datetime
from pathlib import Path


def _load_env():
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


_WOLF_OP_GUIDE = {
    "build": "可建仓/追主升：主线内低吸埋伏、波段持仓可加仓",
    "t_only": "只做T不新建仓：底仓不动，T仓按正T低吸/分时T出/黄线离场高抛低吸",
    "side": "观望/调仓换股：不追主升、不满仓，等结构确认",
    "defense": "防御不建仓：等待企稳/止跌确认，规避C杀",
    "exit": "兑现降仓：反弹即减、控制回撤，不再开新仓",
}

def _read_wolf_context() -> str:
    """狼大视角：主线/浪型/高低位/个股确认（读已落地状态文件，缺失自动跳过）"""
    import os as _os
    NL = chr(10)
    data_dir = _os.environ.get("DATA_DIR", "data")

    def _load(name):
        p = _os.path.join(data_dir, name)
        if not _os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    parts = []
    ml = _load("main_line_state.json")
    if ml:
        s = "- **主线**：" + str(ml.get("main_line") or "?")
        cands = ml.get("candidates") or []
        if cands:
            s += "（候选：" + "、".join(str(c) for c in cands) + "）"
        cat = ml.get("catalyst") or {}
        top = sorted(((k, v) for k, v in cat.items() if v), key=lambda kv: -kv[1])[:3]
        if top:
            s += " ｜ catalyst " + "、".join(f"{k}:{v}" for k, v in top)
        parts.append(s)

    wv = _load("wave_state.json")
    if wv:
        lvl = str(wv.get("level") or "未知")
        sub = str(wv.get("sub_level") or "")
        op = str(wv.get("operation") or "side")
        conf = wv.get("confidence")
        guide = _WOLF_OP_GUIDE.get(op, "")
        s = "- **浪型**：" + lvl + ("·" + sub if sub else "")
        if conf is not None:
            s += "（置信度" + str(conf) + "）"
        s += " → 操作 **" + op + "**：" + guide
        parts.append(s)

    sc = _load("stock_confirm_result.json")
    if sc:
        items = []
        for cname, v in sc.items():
            if not isinstance(v, dict) or "confirm" not in v:
                continue
            n = v.get("n", 0) or 0
            c = v.get("confirm", 0) or 0
            items.append(f"{cname} {c}/{n}({int(100*c/max(n,1))}%)")
        if items:
            parts.append("- **个股确认**：" + "；".join(items[:4]) + "（主线概念成分股突破/站稳比例）")

    pc = _load("position_class_result.json")
    if pc:
        vals = [v for v in pc.values() if isinstance(v, dict) and v.get("action")]
        buy_list = sorted([v for v in vals if v.get("action") in ("低吸埋伏", "回踩低吸")],
                          key=lambda v: -(v.get("fund_flow", {}).get("strength") or 0))[:4]
        reduce_list = sorted([v for v in vals if v.get("action") in ("减仓/只做T", "防御清仓")],
                             key=lambda v: -(v.get("fund_flow", {}).get("strength") or 0))[:4]
        if buy_list or reduce_list:
            s = "- **高低位**："
            if buy_list:
                s += "低位可埋伏：" + "、".join(str(v.get("name", "?")) for v in buy_list)
            if buy_list and reduce_list:
                s += " ｜ "
            if reduce_list:
                s += "高位只做T/减：" + "、".join(str(v.get("name", "?")) for v in reduce_list)
            parts.append(s)

    ms=_load("macro_state.json")
    if ms:
        y=(ms.get("yields") or {}); us=(y.get("us") or {}); dxy=(ms.get("dxy") or {})
        m=(ms.get("market") or {}); sw=(ms.get("macro_switches") or {}); flags=sw.get("flags") or []
        s="- **宏观/机构**：US30="+str((us.get("30年")))+" DXY="+str(dxy.get("value"))
        if flags: s+=" ｜ 开关:"+(",".join(flags))
        parts.append(s)

    if not parts:
        return ""
    return "🐺 狼大视角（信号层）" + NL + NL.join(parts)


def _wolf_operation_advice() -> str:
    """浪型 operation -> 操作纪律（狼大优先）；缺 wave_state 返回空"""
    import os as _os
    try:
        with open(_os.path.join(_os.environ.get("DATA_DIR", "data"), "wave_state.json"), encoding="utf-8") as f:
            wv = json.load(f)
        op = str(wv.get("operation") or "side")
        guide = _WOLF_OP_GUIDE.get(op, "")
        lvl = str(wv.get("level") or "未知")
        sub = str(wv.get("sub_level") or "")
        return "操作纪律（狼大优先）: **" + op + "** " + guide + "（浪型 " + lvl + ("·" + sub if sub else "") + "）"
    except Exception:
        return ""


def format_message(data: dict) -> str:
    """将 market-diagnosis 返回数据格式化为QQ消息"""
    indicators = data.get("indicators", {})
    diagnosis = data.get("diagnosis", {})
    details = data.get("details", [])
    trade_date = data.get("trade_date", "")

    ampl = indicators.get("amplitude", {})
    consec = indicators.get("consecutive", {})
    sector = indicators.get("sector_rotation", {})
    limit_r = indicators.get("limit_ratio", {})
    ma5 = indicators.get("ma5_direction", {})

    # ① 振幅来源
    amp_source = ampl.get('source', '')
    amp_note = f" [{amp_source}]" if amp_source else ""

    lines = [
        f"📊 盘前市场诊断 V2.1 🐺 ({trade_date})",
        "━" * 24,
        f"① 平均振幅: {ampl.get('value', '?')}% → {ampl.get('signal', '?')}{amp_note}",
        f"② 连续涨跌: 最多连{consec.get('max_any', '?')}天 → {consec.get('signal', '?')}",
        f"③ 板块轮动: {sector.get('label', '?')} (速度{sector.get('speed', 0):.0%}) → {sector.get('signal', '?')}",
    ]

    # 显示最近几天轮动的板块
    history = sector.get("history", [])
    if history:
        recent = history[-1]
        top_names = recent.get("top3_names", [])
        if top_names:
            lines.append(f"   今日前3: {' / '.join(top_names[:3])}")

    lines += [
        f"④ 涨跌停比: {limit_r.get('limit_up', '?')}↑/{limit_r.get('limit_down', '?')}↓ = {limit_r.get('ratio', '?'):.1f}:1 → {limit_r.get('signal', '?')}",
        f"⑤ MA5方向: {ma5.get('direction', '?')} ({ma5.get('angle_deg', 0):+.1f}°) → {ma5.get('signal', '?')} [{ma5.get('weight', '降权')}]",
    ]

    # ⑥ 风格轮动检查
    style = indicators.get("style_rotation", {})
    if style:
        regime = style.get("style_regime", "NEUTRAL")
        style_days = style.get("consecutive_days", 0)
        style_suggestion = style.get("suggestion", "")
        divergence = style.get("divergence_warning")

        regime_icons = {
            "OFFENSE": "⚔️ 进攻模式",
            "DEFENSE": "🛡️ 防御模式",
            "RESOURCE_HEDGE": "🥇 资源避险",
            "NEUTRAL": "⚖️ 均衡",
        }
        regime_label = regime_icons.get(regime, f"❓ {regime}")

        if regime != "NEUTRAL":
            lines.append(f"⑥ 风格轮动: {regime_label} (持续{style_days}天)")
            if style_suggestion:
                lines.append(f"   → {style_suggestion}")
        else:
            lines.append(f"⑥ 风格轮动: {regime_label}")

        if divergence:
            lines.append(f"   {divergence}")

        # 显示篮子对比
        price_5d = style.get("price_5d", {})
        if price_5d:
            lines.append(
                f"   近5日涨跌: 防御{price_5d.get('defense_avg_return', 0):+.2f}%  "
                f"科技{price_5d.get('tech_avg_return', 0):+.2f}%  "
                f"资源{price_5d.get('resource_avg_return', 0):+.2f}%"
            )

    # 🐺 狼大视角（信号层）：浪型/主线/确认链/高低位 + 操作纪律优先
    wolf = _read_wolf_context()
    if wolf:
        lines.append("━" * 24)
        lines.append(wolf)
    advice = _wolf_operation_advice()
    if advice:
        lines.append(advice)

    return "\n".join(lines)


def api_base_url() -> str:
    """获取 API 基础地址。

    优先使用 MARCUS_API_URL（docker 部署下 worker 容器用它指向 backend 服务），
    否则回退到本机 localhost（裸机双进程部署）。
    """
    url = os.environ.get("MARCUS_API_URL", "").strip().rstrip("/")
    if url:
        return url
    api_port = os.environ.get("API_PORT", "8000")
    return f"http://localhost:{api_port}/api/v1"


def main():
    _load_env()

    url = f"{api_base_url()}/market/market-diagnosis"

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except Exception as e:
        print(f"[morning_diagnosis] 调用诊断端点失败: {e}", file=sys.stderr)
        sys.exit(1)

    message = format_message(data)
    print(message)


if __name__ == "__main__":
    main()
