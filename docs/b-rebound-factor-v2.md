# B反 多因子 v2

> 2026-10-22；K线 + 量价 + 动量 + 浪型 + 相对强弱。

## 因子

K线：
```text
body / upper_ratio / lower_ratio / close_pos
hammer / doji / bull_engulf / piercing / morning_star
long_lower
```

趋势：
```text
r1/r5/r10/r20/r60
MA5/10/20/60 距离
20/60/120 日回撤
距 20/60 日低
```

量能：
```text
amt5/20
vol5/20
```

动量：
```text
RSI14
MACD
ATR14
布林位置
```

浪型：
```text
wave_level
wave_sub
wave_operation
```

## 初步观察

### 3/24 缩量止跌

```text
lower_ratio 0.689
close_pos 0.933
hammer 1
long_lower 1
```

是标准锤子线。

### 8/3 恐慌日

```text
body -7.36%
close_pos 0.014
lower_ratio 0.014
hammer 0
```

不是锤子线，是收在最低的恐慌长黑。

### 8/4 反转日

```text
body +3.35%
close_pos 0.707
```

不是锤子，是次日反包/反转。

### 11/03 反例

```text
lower_ratio 0.808
close_pos 0.865
```

长下影+高收盘，但后续没有 B反。

## 结论

```text
单看锤子线不够
8/3 不是锤子
8/4 不是锤子
3/24 才是锤子
```

需要：

```text
K线形态 + 量能 + 次日确认 + 浪型
```

## 数据

- `data/core_pit/b_rebound_features_v2.csv`（36 行 × 45 列）
- `jobs/build_b_rebound_features_v2.py`

## 外部参考

- B浪反弹：涨幅有限、量能跟不上、反弹后还有 C 杀  
  https://licai.cofool.com/ask/vipqa_243749_42838133.html
- 锤子线/打桩：长下影要配量，次日过锤子高点才确认；无量容易失败  
  https://wap.stockstar.com/detail/IG2025060300030262
