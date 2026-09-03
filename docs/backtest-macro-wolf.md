# macro_state v2 开关 × Wolf 宏观表态 A/B

> 生成：2026-09-03 · 脚本 apps/main_line/backtest_macro_wolf.py · 明细 data/crowding_pit/macro_wolf_backtest.json
> 样本：8 条 Wolf 明确宏观/机构表态日期，只测我们 v2 能推导的开关项

## 结果：7/8 = 88%

| ID | 日期 | Wolf 表态 | 我们 flags | 判定 |
|---|---|---|---|---|
| M01 | 2026-01-12 | 两融未降温/30Y清单未触发 | margin_heat + north_in | OK |
| M02 | 01-14 | 融资=热钱/基石 | margin_heat + gjd_withdraw | OK |
| M03 | 01-16 | 两融快增，享受3-3 | margin_heat + gjd_withdraw | OK |
| M04 | 01-20 | 融资盘爆仓，等国家队 | gjd_withdraw + north_in（无 margin_burst） | MISS |
| M05 | 07-23 | 4-4=杀杠杆阶段 | margin_burst + gjd_support | OK |
| M06 | 07-31 | GJD 7月净流入4500E 护盘 | gjd_support + margin_burst | OK |
| M07 | 08-03 | GJD 稳3800 护盘 | gjd_support + margin_burst | OK |
| M08 | 03-02 | 美元避险、非A股崩盘日 | 无崩盘类开关 | OK |

## 唯一失手 M04 的原因（重要）

Wolf 说 01-20"融资盘爆仓状态"，但该日两融 20日变化仍 +8.2%（连续两周上涨后单日恐慌），我们的 margin_burst = 20d_chg <= -5% 抓不到"单日融资净流出/爆仓"。
- 需要加单日两融净买变化（如 margin_net_buy 当日大幅为负 或 单日余额回落）作为 burst 的次级别信号；
- 20d 变化只表示"杀杠杆阶段"，不表示"爆仓当天"。

## 结论

1. 四类开关与 Wolf 宏观表态一致性较好（88%）：margin_heat（热钱）、margin_burst（杀杠杆阶段）、gjd_support（护盘）都能对上语料；
2. 需补爆仓日粒度：daily margin net buy/单日余额骤降，而不是只看 20d；
3. 样本只有 8 条，继续扩充（两融/北向/政策/GJD 表态）再重测。
