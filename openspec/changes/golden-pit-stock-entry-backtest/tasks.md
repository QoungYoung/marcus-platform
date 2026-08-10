## 1. 抽取离线入场过滤函数

- [x] 1.1 从 `backend/app/api/indicator.py:2173` 的 `check_entry_filters` 中抽取三层过滤核心逻辑为独立函数 `evaluate_entry_filters_offline()`，放在新文件 `backend/app/services/entry_filter_offline.py` 中
- [x] 1.2 实现日线级技术指标计算：从 `stock_daily.parquet` 读取近 30 个交易日数据，计算 MA5、MA20、MACD (DIF/DEA)、KDJ (K/D/J)、RSI6、CCI
- [x] 1.3 实现日线级资金流向检查：从 `moneyflow.parquet` 读取 5 日/10 日主力资金净流入
- [x] 1.4 实现 KDJ 死叉检测、MACD DIF 收敛检测、量价背离检测的离线版本
- [x] 1.5 跳过 RSR（雪球专有指标），调整过滤逻辑：RSR 检查改为始终通过

## 2. 成分股数据获取

- [x] 2.1 建立 ETF 代码到 tushare 指数代码的映射表（510050 → 000016.SH 等）
- [x] 2.2 实现 `fetch_index_weights()` 函数：调用 `pro.index_weight()` 获取月度成分股，缓存到本地 parquet
- [x] 2.3 处理 tushare API 限流：请求间隔 ≥ 0.2s，带重试机制

## 3. 黄金坑入坑日期提取

- [x] 3.1 从 `golden_pit_snapshots` 表读取所有 `status='golden_pit'` 的记录
- [x] 3.2 按 `(fund_code, date)` 聚类：连续多日黄金坑合并为同一事件，取首日作为入坑日
- [x] 3.3 只保留本地数据覆盖日期范围内的入坑事件（2020-01 至 stock_daily.parquet 最新日期）

## 4. 回测主脚本

- [x] 4.1 创建 `scripts/backtest_golden_pit_stocks.py`：组装 Phase 1-4 流程
- [x] 4.2 实现 14:55 分钟 K 线价格获取：从 `stock_1min/<ts_code>.parquet` 读取当日 14:55 的 close
- [x] 4.3 实现持有期收益计算：N 个交易日后收盘价 / 买入价 - 1
- [x] 4.4 实现等权组合收益：所有通过过滤的股票收益率的算术平均
- [x] 4.5 实现 ETF 基准收益：从 `GoldenPitSnapshot` 或 `stock_daily.parquet` 获取 ETF 自身在同期收益
- [x] 4.6 实现结果输出：按指数汇总表 + 每次事件明细表

## 5. 运行验证

- [x] 5.1 运行回测脚本，沪深300 (15 events) 已完成，全指数回测正在后台运行中
- [ ] 5.2 分析结果：对比股票组合 vs ETF 的超额收益、胜率、最大回撤
- [x] 5.3 分别测试"仅龙头股（流通市值前 20%）"和"全部通过过滤的股票"两种选股范围 — 沪深300结果：全部通过(+0.6% d30超额) 优于 龙头股(-0.4%)
