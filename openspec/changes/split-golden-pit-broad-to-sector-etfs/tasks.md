## 1. 配置层

- [x] 1.1 在 `golden_pit_config.py` 新增 `SECTOR_ETF_POOL` 配置，含已回测 10 只映射（半导体→512480、科创芯片→588200、通信设备→515880、计算机→512720、软件→159852、消费电子→159732、新能源动力系统→515030、生物医药→159929、机械→159886、军工→512660）
- [x] 1.2 为 588000/159915 增加 `guide_only=true` 标记，注释说明宽基仅作择时指导
- [x] 1.3 新增选筹参数：`SECTOR_TOP_N`（默认 2）、`SECTOR_MAX_WEIGHT`（默认 0.5）、combo 权重（`COMBO_W_OVS`/`COMBO_W_MF` 默认 0.5/0.5）、`GOLDEN_PIT_SECTOR_SPLIT_ENABLED` 灰度开关（默认 false）
- [x] 1.4 保留 `PIT_POSITION_SPLIT`/`SEMI_BOOST_INDICES` 定义并在注释标注仅对非 guide_only 路径生效

## 2. 选筹服务

- [x] 2.1 新建 `golden_pit_sector_service.py`：拉取中信二级板块资金流（`moneyflow_ind_dc`）与板块代表 ETF 行情（tushare `fund_daily`），缓存与现有服务一致
- [x] 2.2 实现 `oversold120`：板块指数相对 120 日滚动窗口的百分位（数据不足 120 日视为无效）
- [x] 2.3 实现 `mf5_norm`：最近 5 日资金流累计的归一化分数
- [x] 2.4 实现 `combo` 打分：`w_ovs × 超跌分 + w_mf × 资金流分`，按分数降序取 TOP N
- [x] 2.5 实现排除逻辑：无 ETF 映射板块、数据缺失板块、分数未过门槛板块均不可选中；全不满足时返回空组合
- [x] 2.6 输出结构化选筹结果（板块名、ETF 代码、combo 分数、归一化权重），供 DCA/报告/状态共用

## 3. 状态与报告

- [x] 3.1 `/golden-pit/status` 对 588000/159915 增加 `guide_only=true` 字段与板块组合摘要（dry-run 阶段展示选筹但不下单）
- [x] 3.2 定投报告/晨报展示当日选筹结果（板块、ETF、权重），组合为空时提示"等待板块信号"
- [x] 3.3 状态接口与报告补充 `GOLDEN_PIT_SECTOR_SPLIT_ENABLED` 生效标识，便于灰度期间区分展示/执行

## 4. DCA 执行改造

- [x] 4.1 `golden_pit_dca_service.py` 中 guide_only 指数的 index leg 金额置 0，板块 legs 接入选筹服务结果
- [x] 4.2 板块金额计算：`daily_amount × sector_weight`，权重按 combo 分数归一化并对 `SECTOR_MAX_WEIGHT` 截断（超额按其余板块分数比例再分配）
- [x] 4.3 当日选筹为空时跳过买入且 `schedule_day` 不递增
- [x] 4.4 非 guide_only 指数路径保持 `PIT_POSITION_SPLIT` 90/5/5 拆分逻辑不变
- [x] 4.5 DCA 日志对板块 legs 记录 strategy 含板块代码（沿用 `_encode_strategy` 扩展），便于对账

## 5. 退出改造

- [x] 5.1 guide_only 宽基 `full_exit`/`stop_profit` 映射为组合级清仓（清空对应板块 ETF 持仓，不对宽基下单）
- [x] 5.2 板块 ETF 独立 `down_turn` 退出（连续 `exit_down_days` 回落清仓该板块）
- [x] 5.3 板块持仓 `exit_fallback_days` 兜底适配，记录 strategy 为板块代码 + 退出类型

## 6. 验证与灰度

- [x] 6.1 单元测试：combo 打分、TOP N 选取、权重归一化、单板块上限截断、空组合跳过、guide_only 不下宽基单
- [x] 6.2 dry-run 抽查：当日选筹结果与 `data/backtest/pit_sector_etf.json` 对应窗口信号口径一致
- [x] 6.3 灰度开关验证：`GOLDEN_PIT_SECTOR_SPLIT_ENABLED=false` 时恢复宽基直接买入路径（回滚路径）
- [x] 6.4 更新文档：`docs/golden-pit-sector-etf-report.md` 补充上线说明，`golden_pit_config.py` 头部注释更新配置说明
