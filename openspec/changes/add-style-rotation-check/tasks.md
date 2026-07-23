## 1. SW行业代码动态获取与缓存

- [x] 1.1 在 `market.py` 新增 `_get_sw_style_baskets()` 函数：调用 `pro.index_classify(level='L1', src='SW2021')`，过滤 `is_pub='1'`，按名称关键词映射到 defense/tech/resource 三个篮子，结果缓存到模块级变量（每月刷新）
- [x] 1.2 验证：运行函数，打印三个篮子的行业代码和名称，确认分类合理

## 2. 价格维度近5日对比

- [x] 2.1 新增 `_compute_price_5d_comparison()` 函数：输入三个篮子代码，调用 `pro.index_daily()` 获取近5个交易日数据，每日计算每个篮子的等权平均涨跌幅，判定每日跑赢的篮子，统计连续跑赢天数
- [x] 2.2 处理边界：篮子代码在 `index_daily` 中无数据时跳过并记录 warning

## 3. 资金维度近10日对比

- [x] 3.1 新增 `_compute_flow_10d_comparison()` 函数：调用 `get_sector_flow("concept", top_n=300)` 获取全量概念板块，按科技/防御/资源关键词聚合 `main_net` 和 `pct_change`，每日判定资金偏好方向，统计连续偏好天数
- [x] 3.2 实现关键词匹配优先级（tech > resource > defense），每个概念只归属一个篮子
- [x] 3.3 兼容9:10执行场景：东财降级时走 Tushare `moneyflow_ind_dc`，查询近10个交易日

## 4. 状态机与风格判定

- [x] 4.1 新增 `_compute_style_rotation()` 主函数：整合价格维度和资金维度结果，实现三状态机（DEFENSE/OFFENSE/RESOURCE_HEDGE），双维度确认+连续3天触发，对手跑赢1天退出
- [x] 4.2 实现背离检测：当价格和资金维度矛盾≥5天时输出 `divergence_warning`
- [x] 4.3 生成 `style_regime`、`consecutive_days`、`suggestion` 字段

## 5. 集成到 market-diagnosis 端点

- [x] 5.1 在 `get_market_diagnosis()` 中调用 `_compute_style_rotation()`，将返回结果作为 `style_rotation` 放入 `indicators` dict
- [x] 5.2 更新端点 docstring（五大指标 → 六大指标）
- [x] 5.3 在 `format_message()` 中新增风格轮动展示区块（⑥ 风格轮动）

## 6. 下游消费者接入

- [x] 6.1 修改 `trade_graph.py` 的 `_read_market_regime()`：新增 `_read_style_regime()` 函数，从 `indicators_json` 解析 `style_rotation`，在 AI 提示词中追加风格方向指令
- [x] 6.2 修改 `indicator.py` 的 `_get_market_regime_for_calc()`：新增 `_get_style_regime_for_calc()`，在仓位计算处按 OFFENSE/DEFENSE 调整科技/防御板块仓位上限

## 7. 验证测试

- [x] 7.1 手动调用 `/market/market-diagnosis` 端点，确认返回包含完整的 `style_rotation` 字段
- [x] 7.2 确认 `market_diagnosis` 表中 `indicators_json` 正确保存了 `style_rotation` 数据
- [x] 7.3 确认 `morning_diagnosis.py` 输出包含风格轮动展示
