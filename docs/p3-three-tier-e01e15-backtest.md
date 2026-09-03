# P3 三仓档位 × E01–E15 事件回测（纯规则 v0）

> 生成：2026-09-03 · 脚本 apps/main_line/backtest_p3_tiers.py · 数据 data/p3_tier_backtest.json
> 判定：config/p3_position_tiers.json + backend/app/services/position_tier.py（只做浪型档位判定，不含技术/拥挤/rotation 执行门）
> 口径：wave op 取 .dsh-tmp/tech_entry_system_rows.json 重放结果；has_base/t_universe 为语义假设（回补/加仓≈已有底仓或属做T宇宙），不是对账单。

## 1. 总览

| | 旧系统（建仓=新开，只认 build/部分 rotation） | 旧+P3 三仓档位 |
|---|---|---|
| pass（本来就放行） | 4（E01/E03/E04/E07） | 4 |
| block → 得到 P3 通道 | — | **+4（E05/E08/E12/E13）** |
| partial → 得到 P3 通道 | — | **+5（E09/E10/E11/E14/E15）** |
| 仍无通道（设计保留） | — | E02 / E06 |

## 2. 逐事件结果

| ID | Wolf 行为 | wave | 假设 | 旧系统 | P3 通道 | 说明 |
|---|---|---|---|---|---|---|
| E01 | 初入低位科技(新建) | build | 无底仓 | ✅放行 | ✅new_base≤10% | 不变 |
| E02 | 只买一点 CPO(小仓试盘) | t_only | 无底仓 | ❌ | ❌ | **缺口保留**：t_only 新建无通道（若加试盘 probe≤3% 档可再议） |
| E03 | 继续买科技(回补) | build | T宇宙 | ✅放行 | ✅refill≤8% | 不变 |
| E04 | 半导体设备 ETF 换入 | build | 有底仓 | ✅放行 | ✅add≤10% | 不变 |
| E05 | 加深两只液冷 | t_only | 有底仓 | ❌不会新建 | ✅add≤5% | **新增：已有底仓加厚档** |
| E06 | 4000下买回AI硬 | exit | 无底仓 | ❌ | ❌ | **设计保留**：exit+无底仓=左侧抢反弹，不自动放行 |
| E07 | 主做半导体 | build | 有底仓 | ✅放行 | ✅add≤10% | 不变 |
| E08 | 半导体设备低吸(ETF不清仓) | exit | 有底仓 | ❌ | ✅t_refill≤5% | 新增：作为底仓上 T仓低吸放行（不是加厚底仓） |
| E09 | 高切低(封测等) | exit | 有底仓/rel-low | ⚠️partial | ✅t_refill≤5% | 换股主体仍需 rotation B3/卖旧联动，三仓只给 T 档 |
| E10 | 海外链→国算 | defense | 有底仓 | ⚠️partial(B3) | ✅add≤3% | defense 已有主线底仓小档加厚（E13 同类） |
| E11 | 设备→材料 | exit | 有底仓/rel-low | ⚠️partial | ✅t_refill≤5% | 同上：切换主体仍需 B3，三仓不自动新建材料底仓 |
| E12 | 光→半导体低吸 | defense | 无底仓/T宇宙 | ❌ | ✅refill≤3% | 无底仓但属 T 宇宙 → T资格回补小档 |
| E13 | 继续加仓国产算力 | defense | 有底仓 | ❌ | ✅add≤3% | **核心修正：defense 不禁已有主线底仓加厚** |
| E14 | 4-3调仓国算/ETF | t_only | 有底仓 | ⚠️partial | ✅add≤5% + refill≤8% | 已有方向回补/加厚可用 |
| E15 | 买回半导体保T空间 | t_only | 无底仓/T宇宙 | ⚠️partial | ✅refill≤8% | **核心修正：T资格回补档（Wolf E15）** |

## 3. 结论

1. **旧系统缺的两个结构性通道现在有档位**：defense/t_only 期的已有底仓加厚（E05/E10/E13/E14）与 T资格回补（E12/E15）；E01-E15 中 block 的 6 个有 4 个获得通道、partial 的 5 个全部获得至少 T/回补通道。
2. **仍不放行的两个是设计决策**：E02（t_only 里新建试盘，无底仓无 T 宇宙）和 E06（exit+无底仓回补=左侧抢反弹）——若要覆盖需另立试盘 probe 档并与 wave gate 风控分歧做用户拍板。
3. 三仓档位只解决这个浪型允不允许这笔动作/多大仓，不等于放行：E08-E11 等切换/T 动作仍需继续过 check_entry_filters（技术/拥挤/资金）与 rotation B3（卖旧换新），且 t_refill 是 T仓上限 ≤5%，不是加厚底仓。
4. 假设敏感性：has_base=True 对 defense/t_only 的 add 通道是关键；生产接线时 has_base 直接从 paper_positions 读，t_universe 从 stock 账户活跃 Wolf T 腿（t_conditions）读。

## 4. 下一步（Step B 接线）
1. check_entry_filters 增加 intent/has_base/t_universe 输入 + three_tier_details（P3_TIER_MODE=0 dry-run）。
2. calc_position 按档位 cap 与动态现金底线约束（build 25% → exit 50%）。
3. 监控器：new_base 由候选/长期池显式传；T 通道按 t_universe 调 refill/t_refill。
4. trade_graph prompt 注入三仓档摘要。
5. 观察 2 周后定 P3_TIER_MODE=1。