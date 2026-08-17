## Context

做T选股（t_build / t_pool）现有的可T质量公式只评估日内可操作性（振幅、流动性、波动形态），没有任何方向/行业维度；回测数据链路已具备日线缓存（stock_daily/）与 tushare 代理（sw_daily 申万一级行业日线已实测可用，31 个 L1 行业）。滚动回测（rolling_build / rolling_scan）已有逐日选股与事件流机制，轮动换仓可挂载在其上。详见 proposal.md - Why。

## Goals / Non-Goals

**Goals:**
- 行业强度作为可配置因子并入做T质量评分，且生产（calc_t_quality）与回测（build_score）共用同一实现与参数默认值
- 行业强势过滤默认开启、可整体关闭；震荡市（relax_mode）下同样生效
- 滚动回测支持可配置的汰弱换强轮动换仓（默认关闭），事件流可见、可审计
- 行业日线预取落盘，回放阶段零网络；缺数降级不阻断任务

**Non-Goals:**
- 不接入 dual-track-sector-selection 的 AI 主线识别与概念资金流（属于主交易/GoldenPit 子系统）
- 本 change 不实现实盘持仓轮动换仓（仅回测），实盘联动留待单独 change
- 不做申万 L2/概念级行业细分，行业粒度固定为申万一级 L1
- 不改动现有可T质量公式本身的口径（quality 仍衡量可操作性，行业强度在其外层合并）

## Decisions

### D1 行业数据：sw_daily + 按交易日缓存
行业行情用 tushare 代理的 sw_daily（申万一级，31 行业/交易日，已实测可得）。缓存按交易日组织为 industry_daily/{trade_date}.json（每文件含当日全行业 pct_change），回放按 trade_date 读取，与现有 stock_daily/{symbol}.json 风格一致。
备选：一次性全区间单文件——被否：回放需按日截面，且增量缓存/断点续传更麻烦。

### D2 行业归属：指数成分映射优先、stock_basic 名称映射兜底
候选→申万 L1 归属用 tushare index_member_all（801xxx.SI 成分）为主；若该接口权限不足，兜底用 stock_basic.industry 名称→申万 L1 名称映射表（手工维护常见别名，如 火力发电→公用事业、化学制药→医药生物、广告包装→传媒）。归属映射在预取阶段一次性构建并落盘（industry_map.json），回放零网络。
备选：只用 stock_basic.industry 直连——被否：其行业粒度非申万 L1，无法与 sw_daily 对齐。

### D3 行业强度标准化：logistic 映射
industry_strength = 1 / (1 + exp(-pct_5d / 1.0))（pct_5d 为近 5 日累计涨幅，单位 %），涨幅 0 → 0.5，+5% → 0.993，-5% → 0.007（实现时修正：原 0.04 使 ±0.16% 即饱和、行业间无区分度，调为 1.0），平滑无截断跳变。满足 spec「≥0 正向、≤0 弱区间」约束，且不依赖全市场截面排名（避免换一批股票分数全变）。
备选：min-max 横截面排名——被否：排名分数会随候选集合漂移，不利于回放稳定与生产一致性。

### D4 评分合并与参数位置
final_score = quality × (1 - w) + industry_strength × w，w = industry_strength_weight（默认 0.3）。参数放入 t_build 的 BUILD_PARAMS_DEFAULT（与 relax_mode 同层），任务创建时可覆盖，frontend 透传。生产 calc_t_quality 读取同一参数源。

### D5 轮动换仓：日线信号、次日开盘执行
换仓判定仅在每日收盘后（用 T 日收盘行业/质量数据，无前视），卖出与换入在 T+1 开盘按现有撮合路径执行；冷却 rotation_cooldown_days=2（交易日）。换仓卖出优先释放「行业转弱/质量转弱」持仓，资金复用买入「行业强度更高且质量达标」的候选。
备选：当日盘中触发——被否：盘中使用日线信号有前视风险且撮合复杂。

### D6 生产侧灰度
生产 calc_t_quality 的行业过滤/因子与回测共用参数默认值（保证同口径），但生产启用与否由配置开关（sector_filter_enabled / industry_strength_weight）控制——默认按回测验证结论设定，先回测确认 08 窗口效果后再在生产打开（部署顺序：后端→回测验证→生产开闸）。

## Risks / Trade-offs

- [行业动量追涨杀跌] 在单边趋势市，行业动量因子可能高位接盘 → 默认权重 0.3 且可关；用 08（轮动）与 05（震荡）双窗口对照验证
- [前视偏差] T 日选股若误用当日行业数据会高估收益 → 选股信号统一用 T-1 及以前行业数据（prefetch 落盘时按 trade_date 对齐，轮动判定用 T 日收盘、执行在 T+1）
- [index_member_all 权限不足] 行业归属映射可能降级到名称映射表 → 实现时先实测权限；映射表缺失的标的默认「不过滤也不加分」，并在 caliber_notes 标注
- [换仓成本侵蚀收益] 频繁换仓产生滑点/手续费 → 冷却期 + rotation_enabled 默认关闭，参数化验证后再默认开
- [缓存体积] 每任务新增 31 行业 × 交易日行数据 → 体积 KB 级可忽略；缓存缺数按 spec 降级跳过

## Migration Plan

1. 后端：t_backtest_data 新增行业预取/缓存/归属映射 → t_build 评分与过滤 → t_backtest_runner 透传参数 → t_backtest 轮动换仓（默认关）
2. 测试：行业强度标准化单测、过滤单测、归属映射单测、轮动换仓单测（含冷却期）
3. 回测验证：08-06~08-14 三档对照（全关 / 仅过滤 / 过滤+轮动）+ 05-18~05-29 回归
4. 部署：worker/backend 一并重建（行业预取在 worker 执行）；frontend 重建发布
5. 生产开闸：验证结果确认后把生产配置开关打开
6. 回滚：关闭 sector_filter_enabled / rotation_enabled 即恢复原行为，无需 DB 迁移

## Open Questions

- index_member_all 已实测可用（2026-08-16：单次返回全市场约 5895 行，含 l1_code/l1_name/ts_code）→ D2 走主路径，行业归属直接由 index_member_all 构建（无需名称映射兜底）
- 生产开闸时机与默认开关状态：需回测对照后与用户确认