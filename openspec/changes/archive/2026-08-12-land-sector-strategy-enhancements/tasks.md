## 1. 配置与数据层

- [x] 1.1 在 `golden_pit_sector_service.py` 的 `SECTOR_CONFIG_DEFAULTS` 追加 4 个配置项：`hold_until_exit`(bool, false)、`fallback_broad`(bool, false)、`regime_mode`(str, oversold)、`regime_trend_threshold`(number, 5)，sort_order 13-16，含中文 label/description
- [x] 1.2 确认 `get_sector_config` 读取新配置项并正确按 value_type 反序列化（bool/number/str），旧配置项行为不变
- [x] 1.3 将 4 条新配置写入生产 `golden_pit_sector_config` 表（启动同步或 SQL 迁移），保证配置弹窗与后端一致

## 2. 服务层：select_sectors 增强

- [x] 2.1 `select_sectors` 增加 `holdings`（List[str]）与 `mode`（oversold/trend）参数，默认值保持现有行为，向后兼容
- [x] 2.2 实现只截新入：`hold_until_exit=true` 时合并持仓板块（不参与 TOP N 截断），新进入候选按 combo 排序截断，权重归一化并应用 `max_weight` 截断
- [x] 2.3 实现趋势选筹分支：`mode=trend` 时按 20 日动量（`close[d]/close[d-20]-1`）降序取 TOP N，不设超跌门槛、不参与贪婪排序，复用 `_fetch_etf_kline`
- [x] 2.4 新增 `resolve_regime_mode(cfg, tech_status) -> (mode, reason)`：auto 时按 `trend_up_count >= regime_trend_threshold` 解析，显式值直接返回；数据源失败按 oversold 兜底

## 3. DCA 服务层

- [x] 3.1 `_build_buy_legs` 在板块拆分启用且 `hold_until_exit=true` 时，调用 `_get_sector_holdings(fund_code)` 将当前板块持仓传入选筹
- [x] 3.2 实现 `fallback_broad`：`selected` 为空且配置开启时返回宽基本身 ETF 腿（金额=当日坑内金额），摘要标注回退原因；关闭时保持跳过
- [x] 3.3 实现 regime 生效：`regime_mode=bh` 直接返回宽基腿；auto 读取 tech-status（沿用 15 分钟缓存），解析出的模式传入 `select_sectors`
- [x] 3.4 DCA 买入摘要标注实际生效模式（如 `mode=trend（趋势腿激活 N/9）`、`回退宽基`、`板块信号恢复切回板块`），保持现有摘要格式兼容

## 4. 前端

- [x] 4.1 `GoldenPitPage.tsx` 牛熊面板新增"生效模式"展示：`regime_mode`（含 auto 解析结果）与 `trend_up_count`
- [x] 4.2 确认配置弹窗自动渲染 4 个新配置项（value_type 对应输入控件正确），无额外表单代码改动
- [x] 4.3 前端 `npm run build` 编译 dist 并推送

## 5. 验证

- [x] 5.1 dry-run 对齐：`hold_until_exit=true` 输出与回测"只截新入"变体在 5 个历史窗口一致（选筹与收益）
- [x] 5.2 验证 `fallback_broad`：构造选筹为空场景，确认回退宽基腿与摘要标注，信号恢复后切回板块
- [x] 5.3 验证 `regime_mode=auto` 阈值边界：trend_up_count 恰等于/小于阈值时分别切 trend/oversold
- [x] 5.4 回归：全部新配置默认值下（hold_until_exit=false、fallback_broad=false、regime_mode=oversold）选筹输出与当前生产一致
- [x] 5.5 配置持久化：PUT /golden-pit/sector-config 更新后重启进程配置保持；回测脚本（`data/backtest/_rotation_*.py`）补充 hold_until_exit/fallback/mode 参数以对齐结论
