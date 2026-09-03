# P3 三仓档位模型（底仓 / T仓 / 现金 × 浪型档位）— 设计 v0

> 日期：2026-09-03 · 状态：设计 v0 + 纯规则服务（生产接线待确认）
> 关联缺口：docs/wolf-buy-context-gaps.md ①（E01 仓位上限 / E15 保T资格买回）、docs/tech-entry-system-backtest-report.md 结论⑤（Wolf 底仓内低成本吸筹/回补无通道）
> 相关现有资产：wave_agent operation（build/t_only/side/defense/exit）、p2_entry_gate（defense/exit 禁新开）、做T体系（底仓100保护、无底仓不做T、T仓=L仓买腿）

## 1. 要解决的行为缺口

| Wolf 行为 | 事件 | 系统现状 | 缺什么 |
|---|---|---|---|
| t_only 期买回半导体，保住 T 空间和仓位 | E15 08-12 | 浪型 t_only 无新开通道；无持仓时 T 买腿无资格 | 缺“T资格回补档”（无持仓但属 T 宇宙 → 允许小仓回补底仓） |
| defense 期继续加仓国产算力（已有方向底仓） | E13 07-08 | wave gate defense/exit 一律禁新开 → 直接拦 | 缺“已有主线底仓加厚档”（defense 只禁新建，不禁已有方向回补/加厚） |
| 只做 T 不新建（主升 3-4 / 4-4 / B反 高位） | E02/E05/E08 | prompt 说只做T，但代码层只在 defense/exit 硬拦 → t_only 的 auto 新开并不可靠被拦 | 缺 t_only 档位在代码层的“新 base 拒绝 / refill 放行” |
| side 期埋伏相对低位方向 | C1/D2/C5 | rotation gate 已有 rel-low 语义，但仓位大小与 wave 档位无映射 | 缺 side 埋伏小仓上限（≤3%） |
| 现金底线 | E01 “50%仓位看盘很轻松” | calc_position 固定现金底线 25% | 缺按浪型档位动态现金底线（defense/exit 更高） |

## 2. 三仓定义（账户：stock / t 共用同一套档位语义）

- **底仓（base）**：核心持有；不参与日内 T 卖出底线（现行=保留 100 股铁律）；单票底仓占总资产上限 10–15%，组合底仓总量上限受 total_floor_cap（按 regime 40–55%）约束。
- **T仓（active）**：底仓之上的日内回转仓位；买腿受可卖底仓分档 L0–L3 与单笔上限约束；无底仓标的默认无 T 资格（除非经 refill_base 档恢复资格）。
- **现金（cash）**：按 wave 档位设定动态现金底线（25%–50%），决定可动用资金。

## 3. 档位规则表（v0 初值，待 E01–E15/2周观察校准）

| wave operation（代表子浪） | base_mode | 新开 base | 已有底仓加厚(add_base) | T资格回补(refill_base, 当前无持仓但属T宇宙) | T仓(t_refill) | 现金底线 |
|---|---|---|---|---|---|---|
| **build**（3-1/3-3/W底确认/上升浪） | new | ✅ ≤10% | ✅ ≤10%累计 | ✅ ≤8% | ✅ | 25% |
| **t_only**（3-4/4-2/B反/4-4/ABC高位） | refill_only | ❌ | ✅ ≤5%（主升内已有方向） | ✅ ≤8%（Wolf“保T资格”买回） | ✅ ≤5% | 35% |
| **side**（4-3筑底/d2/ABC构筑/3-2回调） | ambush | ✅ ≤3%（仅 rel-low/可埋伏方向） | ✅ ≤3% | ✅ ≤5% | ✅ | 30% |
| **defense**（4-1/4-5/失败5/C杀/down） | hold_refill | ❌ | ✅ ≤3%（仅已有主线底仓/rotation 放行的主线内调仓） | ✅ ≤3% | ✅ | 45% |
| **exit**（3-5末/d5末/双头M顶/衰竭） | reduce_only | ❌ | ❌ | ❌（只允许对已持仓做 T） | ✅（仅 T 不扩 base） | 50% |

> 语义锚点：defense/exit 都“不新开”（与 p2_entry_gate 一致）；区别在已有主线底仓加厚——defense 允许小档（E13 加仓国算），exit 以减为主仅保留 T（E06 的抢 AI硬回水 属需另行复核的左侧动作，v0 不自动放行）。

## 4. 决策服务 API（backend/app/services/position_tier.py）

```python
three_tier_gate(ts_code, wave_state=None, intent="new_base",
                has_base=None, t_universe=False, rel_low=None,
                mainline_dir=False) -> dict
# → {
#   wave: {operation, sub_level},
#   base_mode: "new|refill_only|ambush|hold_refill|reduce_only",
#   intent_allowed: bool,
#   allowed_actions: [...],
#   cap_pct: 3.0~10.0,      # 本笔动作对应档位上限
#   cash_floor_pct: 25~50,
#   tier: "BASE_NEW|BASE_ADD|T_REFILL|AMBUSH|REJECT",
#   reasons: [...]
# }
```

- 纯函数 + 显式注入：wave_state / 持仓状态由调用方（check_entry_filters、calc_position、monitor）传参，方便回测与单测。
- 规则初值放数据配置 `config/p3_position_tiers.json`（本地权威，同步服务器）；DB 可后续覆盖。

## 5. 接线计划（分两步灰度）

**Step A（本设计对应，纯规则层）**
1. `config/p3_position_tiers.json` + `position_tier.py` + 单测。
2. 回测脚本 `apps/main_line/backtest_p3_tiers.py`：对 E01–E15 每事件标注 intent，输出“旧判定 vs 三仓档位判定”，看一致率/放行率提升多少。

**Step B（生产接线，P3_TIER_MODE=0 dry-run 先观察）**
1. `check_entry_filters`：请求新增可选 intent（new_base/refill_base/t_refill/add_base）与 symbol_held/t_universe；Stage3.5 调 three_tier_gate → 响应新增 three_tier_details；dry-run 只记不改，=1 后 intent 不允许 → block。
2. `calc_position`：读 wave_state + intent → 单票上限叠加档位 cap；现金底线按档位（替代固定 25%）。
3. 监控器（candidate/long-term）传 intent=new_base；T 通道按 refill/t_refill 显式走三仓档。
4. `trade_graph` prompt 注入档位摘要（build 可建主线底仓 / t_only 只能回补+做T / side 埋伏 ≤3% / defense 仅已有方向回补）。
5. 2 周观察 p3_tier 命中/放行日志后再把 P3_TIER_MODE 置 1。

## 6. 不做的边界（本轮 v0）
- 不做 ETF 通道放开（P3 其它子项）、不做分步买入条件链、不做 254 后 3 日回补规则（后者可与 refill_base 组合，下一轮再做）。
- defense/exit 的“Wolf 左侧抢反弹”行为（E06/E08）不默认放行——那与 wave gate 风控语义冲突，需单独用户拍板。
