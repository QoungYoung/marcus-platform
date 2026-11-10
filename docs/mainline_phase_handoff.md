# 主线阶段 / 反弹定性 交接文档

> 新会话范围：**只做主线层**。
> **不要改 A/B/C 的选股逻辑，不要选个股，不要给具体股票代码。**
> 本文只交接主线层的现状、缺口、目标、数据契约和验收。

## 0. 一句话任务

建立“主线阶段”判断，回答：

```text
当前主线是谁？
主线是下跌、超跌反弹、反弹初期、反弹加速、主升、高潮还是退潮？
这波反弹是不是主线的超跌反弹？
反弹做完要不要回主线？
```

不回答：

```text
买哪只股票
谁是龙头
金安还是华正
```

这些属于下游选股层，本会话不碰。

## 1. 为什么要做

现有系统能判断：

```text
有没有主线
主线/赛道/主题强弱
能不能开新仓
```

但不能判断：

```text
主线现在是下跌、反弹还是主升
这波 PCB 是半导体主线的超跌反弹，还是新题材外溢
反弹结束后资金是否回主线
```

缺少这个字段，下游会把：

```text
主线超跌反弹
```

误当成：

```text
新题材外溢
```

导致错误的仓位和持有周期。

## 2. 现有主线层在解决什么

### 2.1 L0 月度主线

`jobs/mainline_monthly.py`

输出 `mainline.json`：

```text
mainline / sub_mainline
excess20
mainline_exists
mainline_active
tradeable
status: confirmed | pullback | weak
regime: up | pullback | weak
```

解决：

```text
有没有主线
主线是否还活着
能不能开新仓
```

### 2.2 L1 赛道周频

`jobs/track_weekly.py`

输出 `track.json`：

```text
focus / trial / watch / avoid
excess20 / breadth / leader_rs / amount_ratio
```

解决：

```text
主线下面哪些赛道强
赛道轮动
```

### 2.3 L2 主题日频

`jobs/theme_daily.py`

输出 `theme.json`：

```text
focus / trial / watch / avoid
主题 breadth / 涨停 / 龙头 / 超额
mainline_regime
```

解决：

```text
赛道下面哪些主题强
```

### 2.4 数据层

`jobs/mainline_common.py`

```text
mkt_bars_daily 本地 PG 日线
index_close / index_amount
daily_snapshot
member_map
```

解决：

```text
主线计算的数据底座
```

### 2.5 交易门

`backend/app/services/mainline_read.py`
`backend/app/services/trade_graph.py`

使用：

```text
mainline_active
tradeable
regime
status
```

规则：

```text
mainline_active=false / tradeable=false / regime=defense
→ 下游不新开底仓
```

本会话只负责把“主线阶段”算对，不负责改下游怎么用。

## 3. 现有缺口

### 3.1 没有阶段

缺少：

```text
rebound_early
rebound_accel
mainline_oversold_rebound
return_to_mainline
```

### 3.2 没有子链

缺少：

```text
半导体设备
半导体材料
芯片设计
先进封装
PCB
覆铜板
电子布
PCB 制造
PCB 设备
```

### 3.3 没有筹码/拥挤度

缺少：

```text
机构持仓
ETF 占比
大股东控盘
流通盘
拥挤度
```

### 3.4 没有带动性

缺少：

```text
龙头是否带动 wind/mid
板块 breadth 是否跟随
强而不带板块
```

## 4. 本会话要交付什么

### 4.1 主线状态契约

建议 PG 表：

```text
mainline_state
  trade_date
  mainline
  sub_mainline
  mainline_phase
  mainline_oversold_rebound
  source_mode
  rebound_target
  return_to_mainline
  evidence jsonb
  updated_at
```

字段：

```text
mainline_phase:
  down
  rebound_early
  rebound_accel
  uptrend
  euphoria
  distribution

source_mode:
  core        主线核心
  rebound     主线超跌反弹/超额
  spillover   新题材外溢

return_to_mainline: bool
```

### 4.2 阶段判断规则

方向性规则，不预设固定阈值：

```text
down
  主线/指数仍在下跌
  龙头未止跌

rebound_early
  主线超跌后首次放量修复
  先修复的强票强于最超跌票
  龙头未确认新主升

rebound_accel
  超跌票弹性大于先修复票
  板块 breadth 扩散
  但仍不是新主线

uptrend
  主线核心/龙头创新高
  梯队完整
  成交额持续

euphoria
  高位加速/炸板/涨停潮

distribution
  龙头死/梯队断/放量滞涨
```

规则负责：

```text
不把反弹判成新主线
```

定性证据负责：

```text
反弹初期还是加速
```

### 4.3 子链映射

至少覆盖：

```text
半导体
  ├─ 半导体设备
  ├─ 半导体材料
  ├─ 芯片设计
  └─ 先进封装

AI 硬件
  ├─ PCB
  ├─ 覆铜板
  ├─ 电子布
  ├─ PCB 制造
  └─ PCB 设备
```

本会话只做子链定义/归属，不做子链内选股。

### 4.4 筹码/拥挤度

能取的数据：

```text
stock_pool.market_cap
mkt_bars_daily.total_mv
fina_mainbz / 主营
news.db / 公告
```

暂不可取的明确写 `unknown`，不要猜。

## 5. 验收

### 5.1 11/09 主线状态

期望能输出：

```text
mainline = AI/科技
sub_mainline = 半导体
mainline_phase = rebound_early 或 rebound_accel
mainline_oversold_rebound = true
source_mode = rebound
rebound_target = PCB / 覆铜板
return_to_mainline = true
```

### 5.2 不允许的输出

```text
把 PCB 判成新主线
把反弹判成 spillover
没有证据却输出 rebound_accel
用固定阈值硬判
```

### 5.3 回归

只回归主线状态本身：

```text
11/06
11/09
```

不跑 A/B/C 选股，不改 10/28 选股基线。

## 6. 数据与代码

### PG

```text
mkt_bars_daily
theme_membership
theme_membership_core
theme_candidate_pipeline
theme_sleeve_daily
```

### 关键代码

```text
jobs/mainline_monthly.py
jobs/track_weekly.py
jobs/theme_daily.py
jobs/mainline_common.py
backend/app/services/mainline_read.py
backend/app/services/trade_graph.py
```

### 不修改

```text
jobs/theme_ab_worker.py
jobs/theme_ab_apply.py
A/B/C prompt
选股/龙头逻辑
```

## 7. 约束

```text
1. 状态只走 PG，不新增 data/*.json 状态
2. 不硬编码个股
3. 不输出“买哪只”
4. 不把抗跌当龙头
5. 不把反弹超额当新主线
6. 规则负责不选错，定性负责阶段
7. 部署链：本地 git push srv main → 线上 git pull self main
```

## 8. 建议顺序

```text
1. 主线状态契约 + PG 表
2. L0/L1/L2 写入 mainline_phase / source_mode
3. 子链映射
4. 11/06、11/09 主线状态回放
5. 交给下游消费，但本会话不改下游
```

## 9. 未决问题

```text
1. 反弹初期/加速用哪些数据，是否允许 agent 定性？
2. 子链只做映射，还是也做子链强度？
3. 筹码/拥挤度先做 observable 的市值/流通盘，还是等数据？
4. 主线状态由 L0/L1/L2 哪个写入，还是独立 job？
```

新会话先读本文和 `NOW.md`，只改主线层。
