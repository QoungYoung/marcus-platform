# 做T信号接入 t_monitor（架构修正, 2026-09-02）

> 用户架构判断：做T是高频日内操作，应接入【做T监控 t_monitor】（30s 轮询自动触发），而非定时交易 agent。

## 1. 接入方式

t_monitor 的 _build_minute_snapshot 新增两个做T信号字段（用已有腾讯/新浪/brze 分钟线 m5 实时计算）：

| 字段 | 含义 | 来源 |
|---|---|---|
| minute.m5.t1_shrink_expand | T1缩转放（正T买点：缩量后放量） | _t_signals_from_m5 |
| minute.m5.t_sell | 分时T出（狼大7-29：放量反弹→第一次分时高点→停量→二次拉升无量不过前高） | _t_signals_from_m5 |

FIELD_REGISTRY 已注册，条件表达式可直接引用。

## 2. 做T条件示例（t_conditions 创建）

**分时T出（自动卖T腿）**：
```json
{"trigger_kind": "high_sell", "armed": 1, "expression": {"field": "minute.m5.t_sell", "op": "==", "value": true}}
```

**T1缩转放（正T买T腿）**：
```json
{"trigger_kind": "low_buy", "armed": 1, "expression": {"field": "minute.m5.t1_shrink_expand", "op": "==", "value": true}}
```

可叠加其他护栏：regime.state in [ACTIVE,CAUTIOUS]、minute.m1.bounce==true（分时企稳）、quote.volume_expand==true（放量）。

## 3. 与狼大做T纪律的映射

- 资格：条件只建在【持仓标的】上（t_monitor 为持仓建条件）——'没有抄底买进去的人没有做T的资格' ✓
- 底仓/T仓分离：T出条件触发卖T腿（high_sell），底仓不卖（由条件范围控制）✓
- 3-5点目标：T出条件触发价由 sell_target_price 控制（可设 3-5%）✓
- 舍得卖：T出条件 t_sell==1 即触发，不犹豫 ✓

## 4. 验证

- 分时T出 5min 回测（184天）：触发后当日剩余91%不再创新高（卖在局部高点）
- T1缩转放 日级：5日+0.90%/20日+1.60%（方向对，样本小）
- 服务器验证：m5 分钟线 60 根可用，T 信号实时计算正常

## 5. 后续

- 条件由管理端/运维按标的创建（持仓标的建 T出+正T 条件）
- T 信号字段可叠加到现有默认做T逻辑（_evaluate_default）或只做表达式条件
- 1min 盘中更精确检测（datahubco/brze rt_min_daily 当日1min 可用）可后续增强