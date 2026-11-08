# P1 实施与 agent 使用决策

> 2026-10-27。P1 = L0 月度主线 + L1 赛道周频评分。
> 结论：**P1 主评分不用 agent；agent 只放在两个“判断点”，且默认 shadow。**

---

## 1. P1 范围

| 任务 | 脚本 | 频率 | 输出 |
|---|---|---|---|
| L0 月度主线 | `jobs/mainline_monthly.py` | 月/20 交易日 | `data/mainline/{as_of}/mainline.json` |
| L1 赛道周频 | `jobs/track_weekly.py` | 周/5 交易日 | `data/mainline/{as_of}/track.json` |

P1 不做个股、不做 theme 日频、不接仓位函数；这些留给 P2/P3。

---

## 2. 为什么 P1 不用 agent 做主评分

P1 的分数是**可复现的算术**：

- 赛道 20 日/5 日收益、超额、宽度、leader RS、成交额比；
- 主线的代理指数月度超额；
- 规则：`mainline_confirmed`、`focus/trial/watch/avoid`。

这些用程序算比 agent 稳，且能回测。让 agent 算分数会引入：
- 不可复现；
- 取数/口径漂移；
- 为了“看起来对”改权重。

所以 P1 的 agent 不是“评分器”，是“复核器”。

---

## 3. agent 只放在两个判断点

| 判断点 | 频率 | 输入 | 输出 | 是否自动应用 |
|---|---|---|---|---|
| 月度主线切换 | 每月一次 | AI/算力 vs 半导体 vs 非科技；月度超额、宽度、leader、regime | `confirmed/uncertain`、子主线、反证 | 默认 shadow；人确认后 promote |
| 周频 track top3 歧义 | 每周一次 | top3 的分数、宽度、leader、data_conflict、theme 事实 | `focus/trial/watch/avoid` 复核 | 默认 shadow |

触发条件（周频）：
- top3 分差 < 0.5；或
- 有 `data_conflict`；或
- 机械 top3 与 theme agent 复核的 focus 不一致。

不触发时，直接按机械分数出 `focus/trial/watch/avoid`，不调 agent。

---

## 4. P1 机械流程

```text
trade_cal → d1/d5/d20
  ├─ mainline_monthly: 代理指数 20d 超额 → AI/科技 / 子主线 / regime / confirmed
  └─ track_weekly:
       SW L2 close/amount → ret1/ret5/ret20、amount_ratio
       成员 5d 宽度、leader 5d RS
       score = 0.20*z(ex1)+0.30*z(ex5)+0.20*z(breadth)+0.15*z(leader_rs)+0.15*z(amt)
       主线未 confirmed → focus 降为 trial
```

---

## 5. 2026-10-23 首跑结果

`mainline.json`：

- mainline = AI/科技
- sub_mainline = AI/算力（-2.92%），半导体/芯片（-15.21%）
- `confirmed=false`，`regime=pullback`

`track.json`：

- focus = 空（主线未确认）
- trial = 电气自动化设备、计算机应用、计算机设备
- watch = 半导体、航天装备
- avoid = 光学光电子、航空装备、医药、通信设备、电源设备、元件
- satellite_focus = 农业

**解读**：
- 机械周频看到的是“电气自动化/计算机”强，半导体只是 watch；这与 10/23 单日的机器人/存储/PCB 强并不矛盾——周频窗口包含 9/25-10/23 的科技回撤，track 级别被拖累。
- 主线月度 `pullback`，所以 focus 降为 trial，不新开；这是 P1 的 gate，不是选股结论。
- theme 级别（存储/PCB/CPO）的日频强度要等 P2 `theme_daily.py`；P1 的 track 分数不能直接当买卖信号。

---

## 6. agent 复核的输入/输出

输入（每主题/每赛道一份）：
- 机械分数、ret1/5/20、excess、breadth、leader_rs、amount_ratio；
- 27 主题的 agent 复核核心/梯队（静态）；
- data_conflict / 数据质量；
- 判例（10/23、10/21、09/02、07/02、05/09）。

输出：
```json
{
  "as_of": "20261023",
  "mainline": {"confirmed": false, "regime": "pullback", "reasons": [], "counter_evidence": []},
  "tracks": [
    {"id": "automation", "verdict": "trial", "confidence": 0.7, "reasons": [], "counter_evidence": []}
  ],
  "needs_human": false
}
```

约束：
- 只能改 `verdict` 和给理由，不能改分数/代码；
- 不能新增赛道/主题；
- 不确定就保留机械结果，`needs_human=true`（按用户策略自动确认，只留 data_conflict）。

---

## 7. 验收

- `mainline_monthly.py` / `track_weekly.py` 可直接跑，输出 JSON；
- 2026-10-23 首跑无报错；
- 主线未确认时 focus 必须为空、trial 有值；
- agent 复核只改 verdict，不改分数；
- 5 个判例（10/23、10/21、09/02、07/02、05/09）上，机械 + agent 复核不出现“把 avoid 改成 focus”的越权。

---

## 8. 结论

**P1 主评分不用 agent；agent 只在“月度主线切换”和“周频 top3 歧义”两处做复核，默认 shadow。**
这样既保留 P1 的可复现/可回测，又把 agent 用在它真正擅长的语义判断上。
