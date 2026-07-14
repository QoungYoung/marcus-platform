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
        f"📊 盘前市场诊断 V2.0 ({trade_date})",
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
        "━" * 24,
        f"综合诊断: {diagnosis.get('label', '?')}",
        f"操作建议: {diagnosis.get('suggestion', '?')}",
    ]

    score = diagnosis.get("score", {})
    if score:
        tv = score.get('total_votes', 6.5)
        lines.append(f"得票: 趋势{score.get('trend', 0)} / 震荡{score.get('oscillation', 0)}")

    return "\n".join(lines)


def main():
    _load_env()

    api_port = os.environ.get("API_PORT", "8000")
    url = f"http://localhost:{api_port}/api/v1/market/market-diagnosis"

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
