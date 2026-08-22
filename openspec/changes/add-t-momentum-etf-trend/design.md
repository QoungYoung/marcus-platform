# Design: t 账户科技 ETF 动量趋势

## Context

See proposal.md（动机：补 V反 主升浪盲区；回测：tech7 双周轮动 TOP3 + 贪婪>0.9 门控，2024-2026 年化 +62% 理想口径 / 2026 +44%）。
现有可复用资产：golden_pit_sector_service._compute_signal_momentum（20日动量）、_load_tech_greed_map（arkvol tech-hardware-greed，TTL 缓存）、golden_pit_tech_status._percentile（250日分位）、t_vreb_daily 基金日线落库（fund_daily）、t_build / t_gateway 建仓平仓链路、t_vreb_etf 监控器骨架。

## Goals / Non-Goals

Goals: t 账户可执行的科技 ETF 动量趋势信号（选股 / 门控 / 双周调仓 / 账户隔离 / 灰度）。
Non-Goals: 不做独立止损（回测证明负资产）；不做贪婪数据回填（2025-01 前历史无法获取）；不改黄金坑 sector_selection 行为（只读复用其信号函数）。

## Decisions

1. 信号复用 golden_pit_sector_service 而非重写：_compute_signal_momentum 与 _load_tech_greed_map 已是生产在用（TTL 缓存），直接调用避免双份数据链。备选：独立实现——否决（重复维护两套 fund_daily / arkvol 链路）。
2. 贪婪门控用 tech-hardware-greed 的 250 日分位（复用 golden_pit_tech_status._percentile）：与黄金坑口径一致，>0.9 剔除（回测验证段 2025-01 起；更早自动退化为无门控，见 REQ-MOM-007）。备选：宽基贪婪门控——否决（宽基分位不能表达板块过热）。
3. 双周（10 交易日）调仓、无止损：回测对比（每日 / 周 / 双周 × 止损 / 贪婪门控）显示双周+门控年化最高（+62%）且换仓仅 48 次/年；止损降低年化 20pt。
4. 建仓复用 build_t_position(build_mode='mom_etf')：sizing 复用短线档（30%），风控链（时段 / 封板 / 熔断 / 规模 / T+1）全部沿用，与 vreb_etf 同构。
5. 候选 / 事件落库复用 t_build_scan_results / t_build_events（source='mom_etf'，reason 标记），前端 TAccountPage 加 tab 展示（复用 vreb 区块模式）。

## Risks / Trade-offs

- [贪婪历史仅 2025-01 起，门控验证段短] → 2025-01 前按无门控运行；上线后持续积累，定期复核门控参数
- [回撤 -27% 偏高（动量本性）] → 仓位档 30% + 总仓 60% 上限 + 调仓降频天然限制暴露；实盘以回测打 5~6 折预期
- [每日涨跌波动不干预（无止损），单标的大幅回撤可能拖累组合] → 双周轮动自然换出弱动量标的；若实盘观察单标回撤过大可后续加「掉出 TOP5 即卖」缓冲（参数化，默认关闭）
- [arkvol 数据源中断] → 门控降级为无门控 + 告警；连续失败超阈值自动停调仓（REQ-MOM-015）
- [与 V反 并行共仓] → t 账户总仓 60% 共享上限，两个模块共用 build 网关，天然互斥；候选 source 隔离防重复下单

## Migration Plan

1. 实现 t_mom_etf.py（信号 / 门控 / 调仓 / 监控器）+ t_build.py mode 扩展 + worker / API 注册 + 测试（镜像 t_vreb_etf）；
2. 本地容器 67+ 用例全绿后提交，灰度默认关闭（T_MOM_ETF_ENABLED=0）部署生产；
3. 生产人工触发一次扫描验证候选 / 门控，观察一周后由用户授权开启；
4. 回滚：置 T_MOM_ETF_ENABLED=0 即停（模块不注册），代码保留。

## Open Questions

- 前端是否需要独立 tab 展示动量持仓（可与 V反 共用「做T账户」页）——实现时按 vreb 区块模式顺带添加，不阻塞核心功能。
