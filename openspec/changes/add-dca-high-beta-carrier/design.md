## Context

生产黄金坑板块拆分后，588000/159915 仅作择时指导（guide_only），坑内 DCA 资金一律通过 `_build_buy_legs` 按板块选筹（tech7 池 combo TOP2，`signal_mode=greed`）分配。回测（生产 500 天分位出入场 + 生产 DCA 形态，`data/backtest/_dca_elastic_hist.py`）显示：换高弹性执行载体可放大收益——科创50 信号 8 窗口 588200 +19.23%（5/7）、512480 +18.08%（5/8）、tech7 等权 +14.15%，宽基对照 +11.84%；创业板信号 6 窗口 159949 +16.41%（5/6），宽基 +14.85%。本坑（2026-07 创业板坑，截至 08-10）008984 +8.15%、512480 +7.04%、588200 +6.65%，而生产选出的 AI+5G 弱于这些高弹性标的。生产缺少「固定高弹性执行载体」的可配置选项。

## Goals / Non-Goals

**Goals:**
- 为每个 guide_only 宽基提供 DCA 执行载体配置：`sector_selection`（现状）/ `fixed_combo`（固定高弹性 ETF 组合）/ `broad`（宽基本身）
- 灰度开关 `dca_carrier_enabled`（默认 false）：关闭时下单不变、仅展示目标载体；开启后 `_build_buy_legs` 按载体模式解析
- 载体配置落 PostgreSQL 并可在黄金坑页面配置弹窗修改；回滚只需配置置 false
- 信号链路（入坑/拐点/出场，500 天滚动分位）完全不变

**Non-Goals:**
- 场外基金载体（008984 申购链路）本期不做，仅预留配置结构
- 跌加因子（暴跌加大定投）不并入本次，另立变更
- 不改动非 guide_only 宽基的 PIT_POSITION_SPLIT 机制
- 不为 fixed_combo 增加板块自身二次拐点/连跌退出（本期退出=宽基窗口退出）

## Decisions

**D1: 载体配置复用 `golden_pit_sector_config` KV 表**
- 新增配置项：`dca_carrier_enabled`（bool，默认 false）、`dca_carrier_588000` / `dca_carrier_159915`（value 为 JSON 字符串 `{"mode":"fixed_combo","codes":[{"code":"588200","weight":0.5},{"code":"512480","weight":0.5}]}`）
- 理由：复用现有 KV 表、`/api/v1/golden-pit/sector-config` 读写接口与 60s 缓存机制、前端配置弹窗交互，改动最小
- 备选：新表 `golden_pit_dca_carrier`（结构更规范）→ 需新 API + 新前端分组，本期不做；表结构 JSON 已足够表达标的+权重

**D2: legs 解析挂载 `_build_buy_legs` 分支**
- 在 `_build_buy_legs` 开头（guide_only 分支前）读取 `dca_carrier_enabled` + 对应 `dca_carrier_<fund>`：
  - `mode=fixed_combo`：返回 `[(sector_name, etf_code, daily_amount*weight), ...]`，sector_name 用载体名（如 "科创芯片/半导体"）
  - `mode=broad`：返回 `[("index", etf_code, daily_amount)]`（宽基本身）
  - 其余/未配置：走现有 `sector_selection` 选筹分支
- 下单循环复用现有 per-leg `_place_buy_order`，strategy 编码追加 `/carrier/{mode}` 便于日志与回查
- 理由：改动点收敛在单一函数，不触碰窗口状态机与出场逻辑

**D3: fixed_combo 退出 = 宽基窗口退出**
- `fixed_combo` 不启用板块连跌/二次拐点退出，清仓信号取宽基 `full_exit / stop_profit / fallback_exit`（与 `_dca_elastic_hist.py` 回测口径一致，避免回测-生产偏差）
- `sector_selection` 保留现有板块级退出不变
- 理由：本期目标是验证"换执行载体"的收益，不引入新的卖出逻辑干扰对照

**D4: dry-run 展示**
- `golden-pit/status` 的 `sector_selection` 块新增 `carrier` 字段：`{"enabled": false, "mode": "fixed_combo", "targets": [...], "note": "dry-run 未生效"}`；`enabled=true` 时标注实际生效模式
- 理由：灰度期间人工可对比「选筹结果 vs 目标载体」，无需等真实下单验证

**D5: 默认值与推荐值**
- 代码常量 `DCA_CARRIER_DEFAULTS`：588000/159915 默认 `mode=sector_selection`（灰度关闭=现状零风险）
- 推荐灰度实验值（写入 DB、文档记录）：588000 → `fixed_combo` 588200+512480 等权；159915 → `fixed_combo` 159949（历史唯一显著跑赢宽基的载体；本坑半导体/科创芯片领先属单窗口，不做外推）
- 理由：588200 历史超额最稳（+19.23%）；159915 用 159949 避免拿本坑单窗口外推

## Risks / Trade-offs

- [588200 仅 7 窗口、159949 仅 6 窗口，样本小、结论方向性] → dry-run 观察 1-2 个坑，与选筹结果对比后再开灰度；效果差配置回 `sector_selection`
- [fixed_combo 忽略板块自身弱势（板块连跌不提前卖）] → 与回测口径一致；后续可加载体二次拐点退出（另立变更）
- [生产实时价为未复权价，回测用后复权连续价] → 份额折算日前后真实价值连续，买入/卖出按实时价不受影响；收益评估以回测复权口径为准
- [本坑 008984/512480/588200 大幅领先是单窗口样本] → 默认值不采用 008984（场外且历史平庸），159915 用 159949
- [配置 JSON 无强校验] → 读取时校验 mode/codes/权重和，非法值回退 `sector_selection` 并告警

## Migration Plan

1. 代码：`DCA_CARRIER_DEFAULTS` 常量、`_build_buy_legs` 载体分支、`golden-pit/status` carrier 展示
2. 配置：`golden_pit_sector_config` 插入 `dca_carrier_enabled=false`、`dca_carrier_588000/159915`（JSON 默认 `sector_selection`）
3. 重启 backend，验证 status 出现 carrier 字段且下单行为不变（enabled=false）
4. 灰度：dry-run 观察 1-2 坑 → 将 588000/159915 载体配置改为推荐 fixed_combo → 观察收益与选筹对比
5. 回滚：配置置 `dca_carrier_enabled=false` 即恢复 sector_selection（无需改代码、无需重启）

## Open Questions

- 前端配置弹窗本期是否完整暴露 DCA 载体编辑（模式下拉 + 标的/权重 JSON），还是仅做开关+展示（倾向：开关+模式下拉 + JSON 文本编辑，避免新增复杂组件）
- 159915 灰度用 159949 单标的还是 159949+515400 组合（等权）
- 008984 场外基金载体是否值得后续单独变更（申购执行链路、T+1 确认、限额）
