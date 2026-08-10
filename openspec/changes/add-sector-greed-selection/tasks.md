## 1. 配置层

- [x] 1.1 在 `.env` 新增 `GOLDEN_PIT_SECTOR_SIGNAL_MODE=greed`（默认），并在 `backend/app/config.py` 的 Settings 增加对应 pydantic 字段
- [x] 1.2 在 `golden_pit_config.py` 的 `SECTOR_ETF_POOL` 每板块新增 `greed_code`（arkvol 代表基金：512480/588200/515880/512720/159852/018301/015528/018396/026130/022243），注释说明与场内 ETF 映射关系
- [x] 1.3 新增 `SECTOR_SIGNAL_MODE` 常量（读 `.env GOLDEN_PIT_SECTOR_SIGNAL_MODE`，默认 greed）
- [x] 1.4 `SECTOR_CONFIG_DEFAULTS` 新增 `signal_mode` 配置项（value_type=string，default 取 SECTOR_SIGNAL_MODE，sort_order 置尾），供 DB 种子与配置 API 返回

## 2. 贪婪数据加载与缓存

- [x] 2.1 `golden_pit_sector_service.py` 新增 `_load_sector_greed_map()`：遍历 SECTOR_ETF_POOL 的 `greed_code`，复用 `ArkvolService.fetch_fund_series(code, days=2000)` 拉取贪婪历史
- [x] 2.2 贪婪序列以 `{etf_code: {date: greed}}` 映射构建，经现有 `_cache_get/_cache_set` 缓存（TTL 7200s，与 kline 缓存一致）
- [x] 2.3 单板块接口异常/空数据仅跳过该板块，不阻断其他板块加载

## 3. greed 模式选筹计算

- [x] 3.1 新增 `_compute_signal_greed(pool_key, entry, greed_map, as_of, cfg)`：有效信号 = oversold120 < 0 且当日贪婪可查；复用 `_fetch_etf_kline` 超跌计算
- [x] 3.2 新增 `_rank_combo_greed(valid)`：combo = -(rank(greed 升序) + rank(oversold120 升序))
- [x] 3.3 `select_sectors` 读取 `cfg["signal_mode"]` 分支：greed 走 `_load_sector_greed_map` + 新计算函数并跳过资金流加载；moneyflow 完全走现有 `_compute_signal` + `_rank_combo` 路径
- [x] 3.4 权重归一化、min_valid 判断、TOP N 选取、空组合 `empty_reason` 逻辑复用现有实现；greed 模式全部板块缺失时返回空组合（DCA 跳过买入）

## 4. 配置 API 与状态

- [x] 4.1 `_seed_sector_config_defaults` 增加 `signal_mode` 默认行（保留旧 DB 值，不覆盖已配置行）
- [x] 4.2 `get_sector_config`/`update_sector_config` 支持 string 类型配置读写，黄金坑配置 API 返回 `signal_mode` 字段
- [x] 4.3 `select_sectors` 结果中透出 `signal_mode`，便于状态/报告区分信号维度（不改变返回结构）

## 5. 验证与灰度

- [x] 5.1 单元测试：greed 分支 combo 打分、TOP N 选取、贪婪缺失板块排除、有效信号不足空仓、moneyflow 分支回归不变
- [x] 5.2 dry-run 抽查：`GOLDEN_PIT_SECTOR_SPLIT_ENABLED=false` 下当日选筹与 `scripts/backtest_sector_greed_500d.py` 对应窗口信号口径一致（2026-08-03 科创 / 2026-07-30 创业板应选筹成功）
- [x] 5.3 回滚验证：DB 或 .env 将 `signal_mode` 切回 `moneyflow` 后选筹立即恢复旧逻辑（数据缺失行为与既有实现一致）
- [x] 5.4 更新文档：`golden_pit_config.py` 头部注释与板块拆分文档补充 greed 模式说明、`greed_code` 配置指引
