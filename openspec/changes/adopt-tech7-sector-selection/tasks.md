## 1. 板块池与配置

- [x] 1.1 在 `backend/app/services/golden_pit_config.py` 新增 `TECH_SECTOR_POOL` 常量（7 只：159949 创业板50、512480 半导体、512930 人工智能、515050 5G通信、515400 大数据、515880 通信设备、588200 科创芯片，含 name/etf_code），保留 `SECTOR_ETF_POOL` 供回滚
- [x] 1.2 新增 `POOL_SOURCE` 默认值 `tech7`（pydantic-settings，`.env` 可配 `GOLDEN_PIT_SECTOR_POOL_SOURCE`），在 `golden_pit_config.py` 暴露 `SECTOR_SELECTION_POOL` 常量按 pool_source 解析
- [x] 1.3 在 `golden_pit_sector_service.py` 的 `SECTOR_CONFIG_DEFAULTS` 增加 `pool_source` 配置项（string 类型，默认 `tech7`，label/description 中文），支持 DB `golden_pit_sector_config` 动态覆盖

## 2. 贪婪数据源接入

- [x] 2.1 在 `arkvol_service.py` 确认/完善 `fetch_tech_greed(days)`（`tech-hardware-greed/series`），返回 `data[6位代码] = [{date, greed}]`
- [x] 2.2 在 `golden_pit_sector_service.py` 实现 tech 池贪婪加载（调用 `fetch_tech_greed`，TTL 7200s 缓存，键 `sector_tech_greed_map`），并为 `SECTOR_ETF_POOL` 保留原 `_load_sector_greed_map` 路径
- [x] 2.3 `select_sectors` 按 `pool_source` 选择池与贪婪源：`tech7` → `TECH_SECTOR_POOL` + tech 贪婪；`prod10` → `SECTOR_ETF_POOL` + funds-greed

## 3. 选筹与展示

- [x] 3.1 校验 `_compute_signal_greed` / `_rank_combo_greed` 对 tech7 池通用（有效信号 = oversold120<0 且当日贪婪可查；combo 排序 TOP N；权重归一化+max_weight 截断），不改既有逻辑
- [x] 3.2 `format_selection` 与 `golden-pit/status` 的 `sector_selection` 输出兼容 tech7（板块名/ETF 代码来自新池）
- [x] 3.3 初始化/迁移：向 `golden_pit_sector_config` 写入 `pool_source=tech7`（复用 `_seed_sector_config_defaults`）

## 4. 测试与验证

- [x] 4.1 单元测试：`backend/tests/test_golden_pit_sector_service.py` 增加 tech7 池选筹用例（含贪婪缺失降级、min_valid 空仓、prod10 回滚路径），`pytest` 全绿
- [x] 4.2 本地冒烟：`select_sectors(as_of=最新交易日)` 返回 tech7 候选（如 08-10 应输出有效信号 ≥4 或空仓原因），`golden-pit/status` 的 sector_selection 无异常
- [x] 4.3 回测对齐：用 `scripts/backtest_sector_prod_500d.py` 或 tech7 回测脚本复跑 5 个板块窗口，确认收益与既有结论一致（超额 +5.86%、5/5）

## 5. 文档与交付

- [x] 5.1 更新 `docs/golden-pit-sector-etf-report.md`：tech7 池说明、贪婪源切换、回测对比（生产池 vs tech7 vs 合并池）、已知差异（tech-hardware 2025-01 起、剔除航天航空/588080 原因）
- [x] 5.2 更新 OpenSpec `add-sector-greed-selection` 相关 delta 或归档说明（可选，与 sync-specs 流程衔接）
- [x] 5.3 部署步骤：改配置后重启 backend，验证 `golden-pit/status`；异常时 `pool_source=prod10` 回滚说明写入文档
