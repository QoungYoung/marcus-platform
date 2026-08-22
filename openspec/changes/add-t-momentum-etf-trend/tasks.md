## 1. 服务模块（t_mom_etf.py）

- [x] 1.1 新建 backend/app/services/t_mom_etf.py：模块参数（tech7 池、动量窗口 20、TOP_N=3、调仓周期 10 交易日、贪婪门控 0.9、仓位档 mom_etf、灰度 env T_MOM_ETF_ENABLED）
- [x] 1.2 实现 _mom_signal()：复用 golden_pit_sector_service._compute_signal_momentum 计算 tech7 各标的 20 日动量（fund_daily K 线，TTL 缓存）
- [x] 1.3 实现 _greed_gate()：复用 _load_tech_greed_map + golden_pit_tech_status._percentile 计算 250 日贪婪分位，>0.9 剔除；数据缺失降级无门控（REQ-MOM-005/007）
- [x] 1.4 实现 scan_once()：每日收盘后计算目标组合（TOP3 等权 + 贪婪门控），写入 t_build_scan_results(source='mom_etf')，同日幂等（先清后插）
- [x] 1.5 实现 try_rebalance()：每 10 个交易日触发调仓（卖出非目标/买入新目标），复用 build_t_position(build_mode='mom_etf') 与 gateway_execute 卖出；时段/封板/规模护栏沿用；被拦次日重试（REQ-MOM-008/010）
- [x] 1.6 实现 check_exits()：调仓日自然换出（动量掉出 TOP3 或贪婪>0.9）；无独立止损（REQ-MOM-009）
- [x] 1.7 实现 MomEtfMonitor（镜像 t_vreb_etf：日扫一次 + 交易时段内调仓/出场检查）与 get_status/start/stop

## 2. 系统接线

- [x] 2.1 t_build.py：build_sizing / build_t_position 支持 build_mode='mom_etf'（复用短线档 30%，skip_timing）
- [x] 2.2 worker_main.py：注册 start_mom_etf_monitor（灰度）
- [x] 2.3 api/t_account.py：新增 /t/mom-etf/status|scan|rebalance|exit-check 端点

## 3. 测试

- [x] 3.1 backend/tests/test_t_mom_etf.py：池常量、动量排序 TOP3、贪婪门控剔除/降级、调仓接线（mock 网关）、账户隔离、灰度开关、出场换出逻辑（镜像 t_vreb_etf 测试风格）
- [x] 3.2 本地容器运行 t_mom_etf + t_vrebounce + t_vreb_etf + t_position_building 全量用例全绿

## 4. 文档与部署

- [ ] 4.1 更新 docs/t-vrebounce-factor-analysis.md：动量趋势章节（回测证据、参数、与 V反 分工）
- [ ] 4.2 提交（中文 commit）并部署生产（backend/worker 重建；T_MOM_ETF_ENABLED 默认 0 灰度）
- [ ] 4.3 生产人工触发一次 /t/mom-etf/scan 验证候选与门控输出，观察一周后由用户授权开启（REQ-MOM-014）

## 5. 验收

- [ ] 5.1 生产验证：T_MOM_ETF_ENABLED=1 时监控启动、双周调仓只动 t 账户、候选/事件可查、贪婪数据失败时降级告警（REQ-MOM-011/013/015）
