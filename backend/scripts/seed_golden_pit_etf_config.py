# -*- coding: utf-8 -*-
"""黄金坑 ETF 配置初始化 / 更新脚本。

将 6 个 A 股宽基指数 → ETF 映射写入 golden_pit_etf_config 表。
同时生成 trades.db 的 sector_config INSERT 语句供手动同步。

用法:
  cd backend
  python -m scripts.seed_golden_pit_etf_config
"""

import sys
import os

# 确保 backend 在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.golden_pit_etf_config import GoldenPitETFConfig

# ── 6 个 A 股宽基指数 → ETF 映射 ──
# fund_code: ArkVol 贪婪值跟踪的指数代码（即 ETF 代码）
# etf_code: 交易用的代码（上海 SH 前缀 / 深圳 SZ 前缀）
# strategy: 基于回测结论的最优策略
#   - 科创50 / 上证50: 绝对阈值触发，uniform_10（前10日等权重）综合最好
#   - 创业板指: P10触发可用，uniform_7（弹性大，窗口合适）
#   - 中证500 / 沪深300 / 中证1000: P10 触发，信号弱，小仓试探

ETF_CONFIGS = [
    {
        "fund_code": "510050",
        "index_name": "上证50",
        "etf_code": "SH510050",
        "etf_name": "华夏上证50ETF",
        "priority": 6,
        "strategy": "uniform_10",
        "daily_amount": 5000.0,
        "max_total_amount": 50000.0,
        "require_absolute_threshold": True,
        "min_days_in_pit": 0,
        "notes": "绝对阈值(0.35)触发。大盘蓝筹，波动最小，信号可靠性高。",
    },
    {
        "fund_code": "510300",
        "index_name": "沪深300",
        "etf_code": "SH510300",
        "etf_name": "华泰柏瑞沪深300ETF",
        "priority": 5,
        "strategy": "uniform_10",
        "daily_amount": 3000.0,
        "max_total_amount": 30000.0,
        "require_absolute_threshold": False,  # 历史从未触及 0.35，P10 触发
        "min_days_in_pit": 0,
        "notes": "P10 相对阈值触发。历史从未跌破 0.35(min=0.3798)，信号较弱，小仓试探。",
    },
    {
        "fund_code": "510500",
        "index_name": "中证500",
        "etf_code": "SH510500",
        "etf_name": "南方中证500ETF",
        "priority": 4,
        "strategy": "uniform_10",
        "daily_amount": 3000.0,
        "max_total_amount": 30000.0,
        "require_absolute_threshold": False,
        "min_days_in_pit": 0,
        "notes": "P10 相对阈值触发。历史从未跌破 0.35(min=0.3817)，信号较弱，小仓试探。",
    },
    {
        "fund_code": "588000",
        "index_name": "科创50",
        "etf_code": "SH588000",
        "etf_name": "华夏科创50ETF",
        "priority": 3,
        "strategy": "uniform_10",
        "daily_amount": 5000.0,
        "max_total_amount": 50000.0,
        "require_absolute_threshold": True,
        "min_days_in_pit": 0,
        "notes": "绝对阈值(0.35)触发。弹性最大，历史回测 15日 AvgRet +30%，胜率 100%。黄金坑策略核心标的。",
    },
    {
        "fund_code": "159845",
        "index_name": "中证1000",
        "etf_code": "SZ159845",
        "etf_name": "华夏中证1000ETF",
        "priority": 2,
        "strategy": "uniform_7",
        "daily_amount": 2000.0,
        "max_total_amount": 20000.0,
        "require_absolute_threshold": False,
        "min_days_in_pit": 2,
        "notes": "P10 相对阈值触发。历史回测收益最差，仅小仓试探。建议等至少 2 个指数入坑后再参与。",
    },
    {
        "fund_code": "159915",
        "index_name": "创业板指",
        "etf_code": "SZ159915",
        "etf_name": "易方达创业板ETF",
        "priority": 1,
        "strategy": "uniform_7",
        "daily_amount": 4000.0,
        "max_total_amount": 40000.0,
        "require_absolute_threshold": False,
        "min_days_in_pit": 1,
        "notes": "P10 相对阈值触发。转折点效应明显（第7天买入远好于第1天），前7日定投最合适。",
    },
    {
        "fund_code": "513400",
        "index_name": "道琼斯指数",
        "etf_code": "SH513400",
        "etf_name": "国泰道琼斯ETF",
        "priority": 8,
        "strategy": "uniform_5",
        "daily_amount": 4000.0,
        "max_total_amount": 40000.0,
        "require_absolute_threshold": False,
        "min_days_in_pit": 0,
        "notes": "P10 相对阈值触发。高胜率(87.5%)，20天均值+3.49%，信号少但质量极高，类似科创50级别。",
    },
    {
        "fund_code": "513600",
        "index_name": "恒生指数",
        "etf_code": "SH513600",
        "etf_name": "南方恒生指数ETF",
        "priority": 9,
        "strategy": "uniform_10",
        "daily_amount": 3000.0,
        "max_total_amount": 30000.0,
        "require_absolute_threshold": False,
        "min_days_in_pit": 0,
        "notes": "P10 相对阈值触发。胜率72.7%，20天均值+1.30%，类似沪深300级别，防御配置。",
    },
]


def seed():
    """写入 / 更新 ETF 配置。"""
    init_db()
    db = SessionLocal()
    try:
        for cfg in ETF_CONFIGS:
            existing = (
                db.query(GoldenPitETFConfig)
                .filter(GoldenPitETFConfig.fund_code == cfg["fund_code"])
                .first()
            )
            if existing:
                # 更新已有记录
                for key, value in cfg.items():
                    setattr(existing, key, value)
                print(f"[UPDATE] {cfg['fund_code']} {cfg['index_name']}")
            else:
                db.add(GoldenPitETFConfig(**cfg))
                print(f"[INSERT] {cfg['fund_code']} {cfg['index_name']}")

        db.commit()
        print("\nDone. 共 6 条 ETF 配置已写入 golden_pit_etf_config 表。")

        # 打印当前配置
        print("\n" + "=" * 80)
        print("当前 ETF 配置:")
        print("=" * 80)
        rows = db.query(GoldenPitETFConfig).order_by(GoldenPitETFConfig.priority).all()
        for r in rows:
            print(f"  {r.etf_code:<12s} {r.index_name:<8s} {r.etf_name:<20s} "
                  f"strategy={r.strategy:<12s} daily={r.daily_amount:.0f} "
                  f"max={r.max_total_amount:.0f} "
                  f"abs_only={r.require_absolute_threshold} enabled={r.enabled}")

        # ── 生成 trades.db sector_config 同步 SQL ──
        print("\n" + "=" * 80)
        print("trades.db sector_config 同步 SQL (在服务器上执行):")
        print("=" * 80)
        for cfg in ETF_CONFIGS:
            sector_key = f"golden_pit_{cfg['fund_code']}"
            sql = (
                f"INSERT OR REPLACE INTO sector_config "
                f"(sector_key, name, indices, etfs, weight, stocks, updated_at, etf_codes) "
                f"VALUES ("
                f"'{sector_key}', "
                f"'{cfg['index_name']}黄金坑', "
                f"'[\"{cfg['index_name']}\"]', "
                f"'[\"{cfg['etf_name']}\"]', "
                f"0.5, "
                f"'[]', "
                f"datetime('now'), "
                f"'[\"{cfg['etf_code']}\"]'"
                f");"
            )
            print(f"  {sql}")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
