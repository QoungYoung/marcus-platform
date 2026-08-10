## Context

当前黄金坑板块拆分选筹由 `golden_pit_sector_service.select_sectors` 实现：combo = -(rank(mf5_norm 降序) + rank(oversold120 升序))，有效信号要求 `mf5_norm > 0` 且 `oversold120 < 0`，min_valid=4、TOP N=2、单板块权重上限 0.5。该信号在拐点确认日常因资金流不足导致选筹失败（回测中大量 `有效信号 0~3 < 4`），且大牛坑中跑输宽基。回测脚本 `scripts/backtest_sector_greed_500d.py` 验证「超跌 + 板块贪婪」（arkvol funds-greed/fund）在 2025-01 后窗口收益更优。本次将 greedy 信号模式接入生产选筹服务，保留 moneyflow 模式可回滚。

## Goals / Non-Goals

**Goals:**
- 新增 `signal_mode` 配置（greed 默认 / moneyflow 可选），DB 配置表可动态切换
- greed 模式：有效信号 = 超跌中 + 板块贪婪可查；combo = -(rank(greed 升序) + rank(oversold120 升序))
- SECTOR_ETF_POOL 每板块新增 `greed_code`（arkvol 代表基金），服务内缓存贪婪历史
- select_sectors 返回结构不变，DCA / 状态 / 报告调用方零改动

**Non-Goals:**
- 不改变退出信号、DCA 金额分配、guide_only 语义
- 不实现回测中的「选筹失败回退宽基 / 回退后切回板块」混合模式（生产维持空仓等待，另行立项）
- 不校准场外代表基金与场内 ETF 的贪婪口径差异
- 不改动前端配置弹窗对 string 类型渲染（如需展示 signal_mode 另立任务）

## Decisions

### 1. signal_mode 配置来源：DB 优先、.env 兜底
`SECTOR_CONFIG_DEFAULTS` 新增 `signal_mode`（value_type=string，default 读取 `.env GOLDEN_PIT_SECTOR_SIGNAL_MODE`，默认 `greed`）。`get_sector_config()` 沿用现有「DB 行覆盖默认」逻辑，黄金坑配置 API 自动返回该字段。
- 备选：仅 .env。否决理由：用户要求配置入 pgsql 并可在黄金坑页面调整；DB 行提供运行时切换能力。

### 2. 贪婪数据加载：复用 ArkvolService，服务内缓存
`sector_svc` 新增 `_load_sector_greed_map()`：遍历 SECTOR_ETF_POOL 的 `greed_code`，调 `ArkvolService.fetch_fund_series(code, days=2000)`，构建 `{etf_code: {date: greed}}`，经现有 `_cache_get/_cache_set` 缓存（TTL 7200s，与 kline 缓存一致）。单次请求失败仅跳过该板块。
- 备选：复用 `GoldenPitService._cached_fund_series`。否决理由：引入服务间耦合；sector_svc 已有自缓存机制。

### 3. combo 计算：按 mode 分支，不动旧路径
`select_sectors` 读取 `cfg["signal_mode"]`：
- `greed`：跳过资金流加载；`_compute_signal_greed`（kline 超跌 + 贪婪值）；`_rank_combo_greed`（greed 升序 rank + oversold 升序 rank）；有效信号 = oversold120<0 且贪婪可查
- `moneyflow`：完全走现有 `_compute_signal` + `_rank_combo` 路径
- 权重归一化、min_valid、TOP N 逻辑共用现有实现

### 4. 数据不足降级
greed 模式任一步骤失败（接口异常、空数据、当日无贪婪值）→ 该板块排除；有效信号数 < min_valid → 空组合，DCA 跳过买入（现有 4.3 行为）。全部板块贪婪缺失时行为等价于旧模式资金流全部缺失。

## Risks / Trade-offs

- [arkvol funds-greed 接口依赖登录态/额度，且 512720/159852 历史仅至 2026-07-09] → 请求失败/空值按板块剔除，不阻断其他板块；接口失败可切回 moneyflow 模式
- [场外代表基金（018301/015528/018396/026130/022243）与场内 ETF 贪婪口径存在差异] → 仅影响板块间排序，不改变机制；后续可用 tech-hardware-greed 场内序列校准
- [机械板块代表基金 026130 历史仅 2026-03 起] → 2026-03 前机械板块在 greed 模式自动排除，属预期数据限制
- [贪婪数据 2025-01 起，更早无历史] → 生产为实时系统自启用日起累积，无历史回填需求；回测对比范围受此限制

## Migration Plan

1. `.env` 增加 `GOLDEN_PIT_SECTOR_SIGNAL_MODE=greed`（默认）；`config.py` 增加对应 pydantic 字段
2. `golden_pit_config.py`：SECTOR_ETF_POOL 每板块加 `greed_code`；新增 `SECTOR_SIGNAL_MODE` 常量
3. `golden_pit_sector_service.py`：贪婪加载缓存、greed 模式计算分支、`signal_mode` 配置项（DB seed）
4. 保持 `GOLDEN_PIT_SECTOR_SPLIT_ENABLED=false`（dry-run）观察 greedy 选筹输出与回测口径一致
5. 回滚：`signal_mode=moneyflow`（DB 行或 .env）或关闭 split 开关，均即时生效

## Open Questions

- 前端黄金坑配置弹窗是否需要在本次支持 string 类型渲染 `signal_mode`（当前弹窗仅 bool/number）
- 是否需要对 greedy 模式增加贪婪硬门槛（如 greed < 阈值），当前仅作为排序维度
