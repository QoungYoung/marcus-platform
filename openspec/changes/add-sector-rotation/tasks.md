## 1. 行业数据预取与归属（t_backtest_data.py）

- [x] 1.1 实测 tushare index_member_all 权限：若可用则用 801xxx.SI 申万 L1 成分构建行业归属；否则用 stock_basic.industry→申万 L1 名称映射表兜底，并把实测结论写进 design.md Open Questions
- [x] 1.2 新增 prefetch_industry_daily(trade_days, cache_dir)：sw_daily 拉取全部 31 个申万 L1 行业按交易日落盘 industry_daily/{trade_date}.json（含 pct_change），并提供 load_industry_daily(trade_date) 读取（回放零网络）
- [x] 1.3 新增行业归属映射构建：候选 ts_code → 申万 L1 index_code，落盘 industry_map.json；映射缺失的标的标记为「无行业」，后续不过滤不加分
- [x] 1.4 预取入口接入：任务启用自动选股或行业因子时，在 build 前预取区间内全部交易日行业数据+归属映射，缺数按 spec 降级（当日过滤跳过、强度取 0.5、caliber_notes 标注）

## 2. 行业强度因子与过滤（t_build.py / t_pool.py）

- [x] 2.1 新增纯函数 industry_strength(pct_5d) = 1/(1+exp(-pct_5d/0.04)) 及行业近 5 日累计涨幅计算（取 T-1 及以前数据，杜绝前视）；单元测试覆盖 0→0.5、±8% 饱和、缺数据→0.5
- [x] 2.2 BUILD_PARAMS_DEFAULT 新增参数：industry_strength_weight=0.3、sector_filter_enabled=true、sector_filter_min_pct=0.0、rotation_enabled=false、rotation_cooldown_days=2；生产 calc_t_quality 读取同一参数源
- [x] 2.3 build_score 按 final_score = quality×(1-w)+industry_strength×w 合并；权重 0/1 退化行为正确
- [x] 2.4 scan_t_candidates（及历史扫描）应用行业强势过滤：行业近 5 日累计涨幅 ≤ sector_filter_min_pct 且过滤开启 → 剔除并记录 sector_excluded 数据（候选、行业、行业涨幅）；relax_mode 下同样生效
- [x] 2.5 生产 calc_t_quality 接入同一行业因子与过滤（开关受配置控制，默认与回测同参数）；单测验证生产/回测同口径

## 3. 回测轮动换仓（t_backtest.py / t_backtest_runner.py）

- [x] 3.1 每日收盘后换仓判定：rotation_enabled 时遍历持仓，任一弱化条件（行业近 5 日涨幅 ≤ 阈值 / 质量跌破持仓阈值）→ 标记卖出；候选池存在「行业强度更高且质量达标」→ 标记换入
- [x] 3.2 T+1 开盘执行卖出与换入（复用现有撮合路径，卖出资金优先用于换入），轮动调仓计入 fees 与滑点口径
- [x] 3.3 冷却期：同标的距上次换仓 < rotation_cooldown_days 交易日则跳过并记录；单元测试覆盖触发/换入/冷却/缺数降级
- [ ] 3.4 事件流新增并落库：sector_excluded、sector_switch（原因：行业转弱/质量转弱/换入/冷却跳过）；caliber_notes 记录行业数据口径与缺数日期

## 4. API 与前端

- [x] 4.1 backend/app/api/t_backtest.py create 请求新增参数：industry_strength_weight、sector_filter_enabled、sector_filter_min_pct、rotation_enabled、rotation_cooldown_days（透传至任务配置与构建）
- [x] 4.2 frontend/src/api/client.ts create 类型补齐新参数；TBacktestPage 表单加行业过滤/轮动开关与权重输入，任务详情展示行业强度与 sector_excluded/sector_switch 事件标签
- [x] 4.3 重建 frontend/dist 并提交（事件标签与表单随之生效）

## 5. 验证与部署

- [ ] 5.1 08-06~08-14 同窗口三档对照：rotation 全关+过滤关（基线）/ 仅过滤开 / 过滤+轮动开，对比 total_return、胜率、换仓次数与费用
- [ ] 5.2 05-18~05-29 历史窗口回归，确认行业因子在两个窗口的一致性（防止单窗口过拟合）
- [ ] 5.3 提交推送并部署 backend/worker/frontend，核对任务报告、事件流与 caliber_notes
- [ ] 5.4 与用户确认生产开闸：验证结论支持后打开生产 side（calc_t_quality 行业因子/过滤开关）