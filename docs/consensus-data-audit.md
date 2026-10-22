# 共识数据接口审计（2025-03-03 实测）

> datahubco / promax 中继均可达。

## 实测

| API | 行数 | 用途 |
|---|---:|---|
| `top_list` | 93 | 龙虎榜每日明细、净买额 |
| `top_inst` | 930 | 机构/游资席位、净买 |
| `limit_list_d` | 127 | 涨停/跌停、连板、行业 |
| `moneyflow` | 5,117 | 大单/超大单/净流入 |
| `margin_detail` | 4,058 | 两融余额 |
| `daily_basic` | 5,381 | 市值/换手/PE/PB |
| `hsgt_top10` | 20 | 北向 top10（net_amount 为空，只有 amount） |
| `report_rc` | 30,000（翻页上限） | 研报；需按日期/股票过滤 |
| `stk_surv` | 142 | 机构调研 |
| `news` | 1 | 当日新闻过少，不能作为主源 |

结论：

```text
数据不是瓶颈
共识痕迹可以按日重建
```

## 可落地的共识维度

```text
游资：
  top_list.net_amount
  limit_list_d 连板/涨停
  top_inst 非机构席位净买

机构：
  top_inst 机构专用/沪深股通净买
  hsgt_top10.amount
  margin_detail 两融
  report_rc 研报覆盖
  stk_surv 调研

大众：
  新闻/搜索（Tushare news 不足，需另源）

价格/体量：
  daily_basic total_mv/turnover/pe/pb
```

## 数据坑

1. `hsgt_top10.net_amount` 为空 → 只能用 amount，不能叫“北向净买”；
2. `report_rc` 单次翻页上限 3 万 → 必须按 `report_date`/股票过滤；
3. `top_inst.exalter` 需分类：`机构专用/沪股通/深股通` vs 游资营业部；
4. `news` 当日仅 1 条 → 不能当共识主源；
5. 主题映射必须用 PIT 概念快照，不能用今天的概念表回填。

## 已开始

`jobs/build_consensus_panel.py`：

```text
按日抓取 top_list/top_inst/limit_list_d/moneyflow/margin_detail/daily_basic/hsgt_top10
→ data/consensus/raw/<api>/<date>.parquet
→ 合并为 panel.parquet
```

已落 2025-03-03/04/07/10 的 raw；全月面板受接口耗时/限速影响未跑完。

## 下一步

1. 全量 2025-01→2026-10 按日抓取（后台、断点续跑）；
2. 生成 `consensus_daily`：
   - 游资/机构/价格三维；
   - 不做单一总分，保留各维度；
3. 用 2025-03 案例校验：
   - 与狼大 2025Q1 方向/个股对照；
   - 看共识维度能否区分龙头/后排；
4. 再决定是否作为④层先验。
