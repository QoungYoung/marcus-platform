## Why

黄金坑撤场后的资金轮动缺乏系统化配置：防御组合（银行/红利/黄金/国债/有色）已回测证明能显著提升撤场期收益（轮动+防御 +131% vs 空仓 +75%，2020-12~2026-07），但尚未接入贪婪监测体系；同时坑内仓位未经数据校准，直观上"进坑买高弹性半导体"在完整轮动回测中反而降低收益（纯512480 -5.4%/回撤-59% vs 指数自身 +131%/回撤-17%），需要按数据校准仓位分配。

## What Changes

- **新增防御组合贪婪监测**：红利(510880)/银行(512800)/黄金(518880)/国债(511010)/有色(512400) 接入 ArkVol 贪婪数据（009052/014028/020412/020741/017193）并叠加滚动价格分位信号，纳入 golden-pit 状态机
- **防御组合入场/出场策略**（按 2017-2026 价格分位回测校准）：红利 P20 入坑/P40 撤场（20日+2.45%胜率77%），银行/黄金/国债弱信号（P10-15入/P40-50撤），有色不入信号（入坑后继续跌，仅作配置成分）
- **坑内仓位重分配**：指数自身为主（80%）+ 588200 科创芯片（10%）+ 512480 半导体设备（10%），替代原"全仓指数自身"；完整轮动回测显示该比例收益/回撤比最优（+116.8%/Calmar 0.88，80/10/10 降至 0.73），全仓/大比例半导体方案废弃
- **撤场后资金承接**：撤场信号触发后资金默认转入防御组合（红利/银行/黄金/国债/有色五标的等权，有色不作入坑信号但保留为组合成分），等待下一次进坑
- **DB 配置**：`golden_pit_etf_config` 新增 5 个防御标的行（enabled=true），`golden_pit_snapshots` 开始采集防御标的贪婪快照
- **回测脚本**：`scripts/backtest_golden_pit_defense.py` 复现防御阈值校准与仓位分配回测

## Capabilities

### New Capabilities
- `golden-pit-defense-monitor`: 防御组合（红利/银行/黄金/国债/有色）的贪婪监测与入坑/撤场信号，含 ArkVol 贪婪接入、价格分位信号、防御组合入场出场阈值配置

### Modified Capabilities
- `golden-pit-exit`: 撤场信号触发后，资金轮动目标从"空仓"改为"防御组合承接"，并定义防御组合的再入场条件
- `golden-pit-dca-schedule`: 坑内建仓标的从"指数自身 100%"改为"指数自身 80% + 588200 10% + 512480 10%"的小比例增强配置

## Impact

- `backend/app/services/golden_pit_config.py`: 新增 `DEFENSE_INDICES` 配置（防御标的、ArkVol 映射、入坑/撤场阈值、仓位权重）
- `backend/app/services/golden_pit_service.py`: 防御标的纳入监测循环，撤场建议附带防御承接建议
- `backend/app/services/golden_pit_repository.py`: snapshots 写入支持防御标的 fund_code
- `backend/app/services/golden_pit_report.py`: 报告输出防御组合状态
- `backend/app/services/golden_pit_dca_service.py`: 坑内仓位按新权重分配，撤场后防御承接
- DB: `golden_pit_etf_config` 新增行；`golden_pit_snapshots` 数据扩展
- 新增脚本: `scripts/backtest_golden_pit_defense.py`