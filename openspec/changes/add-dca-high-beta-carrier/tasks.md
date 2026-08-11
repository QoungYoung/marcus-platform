## 1. 配置层（后端）

- [x] 1.1 在 `backend/app/services/golden_pit_config.py` 新增 `DCA_CARRIER_DEFAULTS` 常量（588000/159915 默认 `{"mode":"sector_selection"}`）与说明注释
- [x] 1.2 在 `backend/app/services/golden_pit_sector_service.py` 的 `_seed_sector_config_defaults` 增加种子项：`dca_carrier_enabled=false`（bool）、`dca_carrier_588000`/`dca_carrier_159915`（JSON 字符串，默认 sector_selection）
- [x] 1.3 在 `get_sector_config` 增加载体配置解析：读取 `dca_carrier_enabled` 与各指数 JSON，校验 mode/codes/权重和
- [x] 1.4 非法或缺失载体配置回退 `sector_selection` 并 `logger.warning` 记录原因

## 2. DCA 执行链路

- [x] 2.1 `backend/app/services/golden_pit_dca_service.py` `_build_buy_legs` 开头增加载体分支：`dca_carrier_enabled=true` 时按 `mode` 解析（fixed_combo → 固定权重 legs；broad → 宽基本身 leg）
- [x] 2.2 fixed_combo 金额按权重拆分、合计不超 `daily_amount`；单 leg 金额不足一手（100 股）跳过并记日志，金额不转移
- [x] 2.3 载体下单 strategy 编码追加 `/carrier/{mode}` 标记，便于 DCA 日志回查
- [x] 2.4 退出逻辑：fixed_combo 清仓跟随宽基窗口退出信号（full_exit/stop_profit/fallback_exit），不启用板块连跌退出；sector_selection 保持现状

## 3. 状态展示与 API

- [x] 3.1 `golden-pit/status` 的 sector_selection 块新增 `carrier` 字段：enabled/mode/targets（目标标的与权重）/note（dry-run 未生效 或 实际生效）
- [x] 3.2 确认 `/api/v1/golden-pit/sector-config` GET/PUT 无需改动即可覆盖新配置项（复用现有 KV 读写），若 value_type 枚举需扩展则补充

## 4. 前端配置弹窗

- [x] 4.1 `frontend/src/pages/GoldenPitPage.tsx` 配置弹窗新增「DCA 执行载体」分组：开关（dca_carrier_enabled）、模式下拉（sector_selection/fixed_combo/broad）、标的与权重 JSON 文本编辑（带格式校验提示）
- [x] 4.2 保存后刷新 status，展示 carrier 预览（目标载体 vs 实际生效模式）

## 5. 测试与文档

- [x] 5.1 单元测试：载体解析（fixed_combo 权重拆分、非法配置回退、enabled=false 不下单行为不变）
- [x] 5.2 本地验证：enabled=false 时 status 展示 carrier 且 DCA 下单仍走选筹；enabled=true 时 legs 按载体生成
- [x] 5.3 文档 `docs/golden-pit-sector-etf-report.md` 补充弹性载体对比表（科创50/创业板 8+6 窗口 + 本坑快照，引用 `data/backtest/_dca_elastic_hist.py`）
- [x] 5.4 灰度已开启（2026-08-11）：DB 写入 recommended fixed_combo 并置 `dca_carrier_enabled=true`；回滚路径 = 配置置 false：开启 dry-run 观察 1-2 个坑后，将推荐 fixed_combo（588000→588200+512480；159915→159949）写入配置再开灰度，回滚路径 = 配置置 false
