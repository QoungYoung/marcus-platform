## 1. 配置与数据源

- [x] 1.1 golden_pit_config.py 新增 DEFENSE_INDICES（红利/银行/黄金/国债/有色，含 ArkVol fund_code 映射与价格分位入坑/撤场阈值）
- [x] 1.2 golden_pit_config.py 新增 SEMI_BOOST_INDICES（588200/512480，tech-hardware-greed 贪婪阈值 + 坑内 90/5/5 权重）
- [x] 1.3 arkvol_service.py 新增 tech-hardware-greed 接口端点与获取方法

## 2. 服务监控

- [x] 2.1 golden_pit_service.py 接入 tech-hardware-greed 数据，构建 588200/512480 指数状态（复用 _build_index_info 状态机）
- [x] 2.2 golden_pit_service.py 构建 DEFENSE_INDICES 状态（ArkVol 贪婪展示 + 250 日价格分位信号，复用 _determine_status/_detect_exit_signal）
- [x] 2.3 golden_pit_repository.py 快照同步与历史查询支持防御/半导体标的 fund_code
- [x] 2.4 golden_pit_report.py 状态与报告输出包含防御组合与半导体增强状态

## 3. DCA 仓位重分配

- [x] 3.1 golden_pit_dca_service.py 坑内买入按 90/5/5 拆分（指数自身/588200/512480），增强标的自身未入坑或数据缺失时回退指数自身
- [x] 3.2 golden_pit_dca_service.py 撤场后资金按防御组合五标的等权承接，下一轮成长指数入坑时转回成长仓
- [x] 3.3 持仓明细/退出信号/买入候选列表兼容防御与半导体标的中文名称与 ETF 代码

## 4. 数据库与回测

- [x] 4.1 golden_pit_etf_config 插入防御标的（510880/512800/518880/511010/512400）与半导体增强标的（588200/512480）配置行
- [x] 4.2 scripts/backtest_golden_pit_defense.py 复现防御阈值校准与 90/5/5 仓位分配回测

## 5. 验证

- [x] 5.1 运行回测脚本复现基准结果（防御承接 +131% vs 空仓 +75%，90/5/5 接近纯指数（Calmar 0.88））
- [x] 5.2 启动后端验证 /golden-pit/status 返回防御与半导体标的状态
- [x] 5.3 运行 openspec validate 确认变更有效