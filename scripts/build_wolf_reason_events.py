# -*- coding: utf-8 -*-
"""build_wolf_reason_events.py — 从语料事件表生成 wolf_reason_events.json（理由/信号/数据需求）

输入: .dsh-tmp/tech_entry_events.json（E01-E15 基础表）
输出: data/wolf_reason_events.json（每次 Wolf 操作的结构化“为什么+需要什么数据”）
用法: python scripts/build_wolf_reason_events.py
"""
import os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV = os.path.join(ROOT, ".dsh-tmp", "tech_entry_events.json")
OUT = os.path.join(ROOT, "data", "wolf_reason_events.json")

# 人工理由/信号/数据需求标注（语料原文见 tech_entry_events.json）
REASON = {
    "E01": {"reason_type": ["主线初建/低位"], "wolf_reason": "开盘买存储芯片DML+AI终端华勤——低位蓝筹初入主线",
            "signals": ["主线资金认可", "个股低位(距高/相对落后)", "存储/AI终端催化"],
            "data_needed": [{"field": "概念级历史资金流", "source": "moneyflow_ind_dc", "cost": "内部已有(2023-09+)"},
                            {"field": "个股低位位置", "source": "qfq日线+position", "cost": "低"},
                            {"field": "主线rank", "source": "fusion_main_line", "cost": "低"}]},
    "E02": {"reason_type": ["试盘/仓位"], "wolf_reason": "主线小仓试盘——只买一点CPO陪同",
            "signals": ["主线内CPO方向确认", "小仓(≤试探)"],
            "data_needed": [{"field": "主线内细分确认", "source": "main_line/rotation细分", "cost": "低"},
                            {"field": "试盘档位规则", "source": "P3 probe", "cost": "已有"}]},
    "E03": {"reason_type": ["指数支撑回补"], "wolf_reason": "指数下来继续买科技/3936-3960回补",
            "signals": ["上证关键点位(3936-3960)", "科技主线资金未退"],
            "data_needed": [{"field": "指数关键位历史", "source": "index_daily", "cost": "低"},
                            {"field": "科技资金流历史", "source": "moneyflow_ind_dc", "cost": "低"}]},
    "E04": {"reason_type": ["工具/ETF切换"], "wolf_reason": "科创100调半导体设备ETF——低点买设备ETF",
            "signals": ["半导体设备方向确认", "ETF相对/折价与持仓切换"],
            "data_needed": [{"field": "ETF日线/份额", "source": "etf行情/tushare fund_share", "cost": "低"},
                            {"field": "半导体设备个股位置", "source": "qfq日线", "cost": "低"}]},
    "E05": {"reason_type": ["日历/业绩", "大盘环境"], "wolf_reason": "年底布局液冷(26年业绩方向)；只加深两只；注意大盘缩量+券商回补缺口支撑",
            "signals": ["12月→次年业绩预告窗口", "液冷业绩预期", "大盘缩量", "券商回补缺口"],
            "data_needed": [{"field": "业绩披露日历", "source": "risk_flags ann字段→日历", "cost": "低(需做)"},
                            {"field": "大盘量能", "source": "index成交量", "cost": "低"},
                            {"field": "券商板块状态", "source": "行业指数", "cost": "低"},
                            {"field": "H2/年底产业节奏", "source": "config日历(人工)", "cost": "低"}]},
    "E06": {"reason_type": ["位置回补/左侧"], "wolf_reason": "4000下跌了开始买回AI硬，搏旭创回水面溢价",
            "signals": ["上证4000关口", "AI硬回撤/拥挤分歧", "旭创回水面"],
            "data_needed": [{"field": "指数关键点位", "source": "index_daily", "cost": "低"},
                            {"field": "个股水位(qfq/距高)", "source": "日线", "cost": "低"},
                            {"field": "拥挤分歧数据", "source": "PIT拥挤/机构持仓", "cost": "中"}]},
    "E07": {"reason_type": ["月度主攻/主线"], "wolf_reason": "本月主要先做半导体——钱一点一点赚",
            "signals": ["月度资金/排名", "半导体为当月主线"],
            "data_needed": [{"field": "月度行业资金", "source": "moneyflow_ind_dc", "cost": "低(2023+)"}]},
    "E08": {"reason_type": ["浪型纪律/方向"], "wolf_reason": "看好半导体设备；3-3确认前ETF不清仓，把握每次低吸",
            "signals": ["3-3未转4浪/行情结束", "ETF底仓不清", "每次低吸节奏"],
            "data_needed": [{"field": "历史wave状态", "source": "wave_agent重放", "cost": "低"},
                            {"field": "ETF持仓/不可清语义", "source": "持仓+T腿", "cost": "低"}]},
    "E09": {"reason_type": ["高切低/对冲"], "wolf_reason": "大涨的切到没涨的封测/地面光伏；石油两手准备对冲科技调整",
            "signals": ["板块内相对涨幅落后", "封测/光伏低吸位", "石油对冲"],
            "data_needed": [{"field": "个股20日相对涨幅", "source": "qfq日线", "cost": "低"},
                            {"field": "资源/石油方向数据", "source": "行业/ETF", "cost": "中"},
                            {"field": "持仓切旧买新", "source": "paper_positions", "cost": "低"}]},
    "E10": {"reason_type": ["产业周期/资金切换"], "wolf_reason": "国算还没到出货周期，趁切换；麦米/CPO全清→国算电源（资金逻辑）",
            "signals": ["旧链已到出货周期/拥挤", "新链未到出货周期", "业绩/产业阶段"],
            "data_needed": [{"field": "产业周期/出货节奏", "source": "config日历(人工)", "cost": "低"},
                            {"field": "链级PIT拥挤", "source": "stock_crowd/象限", "cost": "低-中"},
                            {"field": "龙虎榜/资金", "source": "tushare", "cost": "中"}]},
    "E11": {"reason_type": ["日历/产业节奏"], "wolf_reason": "设备最硬俩新高→调材料；上半年设备下半年材料；业绩月后机构调仓",
            "signals": ["设备新高(H1目标达成)", "H1→H2节奏切换", "业绩月已过"],
            "data_needed": [{"field": "H1/H2产业节奏日历", "source": "config(人工)", "cost": "低"},
                            {"field": "业绩披露逐股日历", "source": "risk_flags ann", "cost": "低(需做)"},
                            {"field": "设备/材料相对位置", "source": "qfq日线", "cost": "低"}]},
    "E12": {"reason_type": ["情绪/恐慌盘口", "核心换链"], "wolf_reason": "超绝割肉盘口之后买核心科技；光45→18内，慢慢低吸半导体",
            "signals": ["割肉盘口(放量恐慌/长下影/洗盘回升)", "光减仓释放资金", "半导体为核心方向"],
            "data_needed": [{"field": "盘口恐慌/割肉特征历史", "source": "5min/分时(需采集)", "cost": "高"},
                            {"field": "光→半导切换序列", "source": "持仓+决策链", "cost": "低"},
                            {"field": "5min个股/指数", "source": "brze(2025-11+已有)", "cost": "已有部分"}]},
    "E13": {"reason_type": ["政策底/机构", "产业计划"], "wolf_reason": "继续加仓国产算力(下半年计划)；GJD政策底→机构调仓一致后市场底",
            "signals": ["GJD护盘/政策底", "机构调仓一致", "下半年产业计划"],
            "data_needed": [{"field": "历史macro_state(GJD/两融/债/美元)", "source": "tushare回填(缺2026-09前)", "cost": "中"},
                            {"field": "政策会议日历", "source": "config(人工)", "cost": "低"},
                            {"field": "机构调仓代理", "source": "公募持仓PIT", "cost": "中"}]},
    "E14": {"reason_type": ["日历/机构/工具"], "wolf_reason": "业绩月后机构调仓下半年；国算交换机穿越；以ETF为主",
            "signals": ["业绩月已过", "H2方向确认", "ETF执行"],
            "data_needed": [{"field": "业绩月后日历", "source": "config", "cost": "低"},
                            {"field": "ETF执行通道", "source": "系统", "cost": "中(需开发)"}]},
    "E15": {"reason_type": ["工具性/仓位"], "wolf_reason": "买回半导体是为了保证T的空间和仓位(工具性回补)",
            "signals": ["T腿标的", "T资格/底仓状态", "回补档位"],
            "data_needed": [{"field": "T腿/底仓状态", "source": "t_conditions/positions", "cost": "低"},
                            {"field": "P3 refill档", "source": "已有", "cost": "低"}]},
}

def main():
    evs = json.load(open(EV, encoding="utf-8"))
    out = []
    for e in evs:
        eid = e["id"]
        r = REASON.get(eid, {})
        out.append({"id": eid, "date": e.get("date"), "phase": e.get("phase"),
                    "wolf_action": e.get("wolf_action"), "type": e.get("type"),
                    "quote": e.get("quote", "")[:500],
                    "reason_type": r.get("reason_type", []),
                    "wolf_reason": r.get("wolf_reason", ""),
                    "observable_signals": r.get("signals", []),
                    "data_needed": r.get("data_needed", []),
                    "theme": e.get("theme"), "rot": e.get("rot")})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", OUT, "events", len(out))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
