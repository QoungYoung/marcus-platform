
---

## v3：高点递减 + 放量 + 接力（2026-10-21）

按语料重写：

```
lower_high = 本次反弹高点 <= 上次反弹高点 * 0.995
vol_dist   = 放量（>1.2×20日均额）且跑输子主题
successor  = 子主题成交额前5里，5日收益明显强于风向标
breadth    = 子主题成员站上MA20比例

alive    = 高点抬高 + 广度跟 + 非放量出货
rotated  = 高点下移 + 有合格接力 + 广度>=0.4
dead     = 高点下移 + 放量出货（无合格接力）
warning  = 其余
```

### 结果

- 403 个 impulse；
- `alive 103`、`warning 256`、`rotated 44`、`dead 0`；
- `dead` 为 0：宽口径子主题里几乎总能找到“成交额前5的接力票”，所以旧风向标弱时被记成 `rotated`。

### 验证（impulse 日，后续子主题等权收益）

| 状态 | n | 5日 | 10日 | 20日 |
|---|---|---|---|---|
| alive | 89 | +0.25% | -1.59% | -10.59% |
| warning | 178 | -0.32% | +0.92% | -1.50% |
| rotated | 44 | -2.43% | -3.02% | -4.40% |
| dead | 0 | - | - | - |

仍不单调：alive 只有 5 日略正，10/20 日最差；rotated 反而最差。

结论：**v3 仍不能作为 P1 买侧闸门。**  
`dead=0` 说明“接力”代理太宽，当前样本里几乎总有人接，等于没有“死”这个状态；需要把“接力”限定为真正的链主/龙头，而不是成交额前5里的任意票。

---

## 用途修正（2026-10-21）

`bellwether_state` 不再作为选股 alpha，而是环境/补涨/高切低闸。代码：

`backend/app/services/wolf_card/bellwether_context.py`

| 状态 | second_line_new | high_cut_low | hold_second_line | reduce_theme |
|---|---|---|---|---|
| alive | ✅ | ✅ | ✅ | ❌ |
| rotated | ✅（带 successor） | ✅ | ✅ | ❌ |
| warning | ❌ | ❌ | ✅ | ❌ |
| dead | ❌ | ❌ | ❌ | ✅ |
| unknown | ❌ | ❌ | ✅ | ❌ |

> 2026-10-21 修正：上述权限按**语料语义**定义，不用收益验证来回改。
> 之前的“5日限制/关闭高切低/rotated 不新买”是基于收益结果的错误做法，已撤回。

注意：

- `core` 选股仍走 `stage × role`，不由风向标决定；
- `second_line_new` 只表示允许做补涨/二线，不是买入信号；
- `dead` 只表示禁止高切低/补涨 + 降低主题暴露，不预测主题必跌；
- `rotated` 必须带 `successor`，不能当 dead；
- 单日跌停、破前低都不够，仍以高点递减+放量+无接力为准。

验证目标同步改为：

1. alive 时做二线/补涨 vs dead 时做二线/补涨；
2. dead 时高切低的后续收益；
3. alive 时持有弱势二线能否等到补涨；
4. rotated 后换新风向标是否延续；
5. unknown/无风向标时一刀切停做是否误杀。

不再用“alive 后子主题 5/10/20 日收益”作为唯一验收。

## 无 impulse 的状态刷新（2026-10-21）

旧版：状态无限 carry，导致 2026-01-28 北方华创“很强”仍沿用 `warning`。

新版：

```
无 impulse：
  最多 carry 3 个交易日
  超过 3 日：
    close >= 上次 impulse 高点 → alive
    close < 上次 impulse 高点*0.95 → warning
    其他 → stale
```

`stale` 权限 = 不新买、不高切低、可持有、不降暴露；不等于 dead。
