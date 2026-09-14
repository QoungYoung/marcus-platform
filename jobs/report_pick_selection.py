# -*- coding: utf-8 -*-
"""report_pick_selection.py — 把 eval_pick_selection.py 的产物写成 docs/wolf-pick-selection-eval.md。

用法: .venv/bin/python jobs/report_pick_selection.py
输入: .dsh-tmp/buyside/eval_h{5,10,20}.json（全主题）、eval_pool.json（方向层池内）、eval_trades.json（生产成交核对）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV = os.path.join(ROOT, ".dsh-tmp", "buyside")
OUT = os.path.join(ROOT, "docs", "wolf-pick-selection-eval.md")

ARMS = ["t1", "t12", "lowmid", "cand", "wait", "basket", "market", "rand", "legacy"]
ARM_CN = {"t1": "pick_v2 tier1（实际布腿，≤2）", "t12": "pick_v2 tier1+tier2（≤4）",
          "lowmid": "过位置闸的全部（lowmid）", "cand": "候选池（组内前2∩容量分位50%）",
          "wait": "等待池 top6（leader 榜）", "basket": "同主题等权篮子（beta；超额恒 0）",
          "market": "全市场等权（同窗口）", "rand": "同池内随机（蒙特卡洛 200 次）",
          "legacy": "原 DB 扫描序（legacy confirm_pick）"}


def load(name):
    p = os.path.join(EV, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def f(v, keys=("n", "mean", "median", "win", "block_t")):
    if not v or not v.get("n"):
        return "—"
    out = []
    for k in keys:
        x = v.get(k)
        if x is None:
            out.append("—")
        elif k == "n":
            out.append(str(x))
        else:
            out.append("%.3f" % x if abs(x) < 100 else "%.1f" % x)
    return " | ".join(out)


def arms_table(r, title):
    lines = ["| 臂 | 只数 | theme-day | 绝对均值% | 中位% | 胜率 | 同主题超额% | 超额块状t |",
             "|---|---|---|---|---|---|---|---|"]
    cnt = (r.get("arm_stock_counts") or {})
    for a in ARMS:
        v = r["arms"][a]
        if a == "basket":
            ex = "0（基准）"
            exbt = "—"
        else:
            ex = "%.3f" % v["excess"]["mean"] if v["excess"].get("n") else "—"
            exbt = v["excess"].get("block_t")
            exbt = "%.2f" % exbt if exbt is not None else "—"
        val = v["value"]
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            ARM_CN[a], cnt.get(a) if cnt.get(a) is not None else "—", val.get("n", "—"),
            "%.3f" % val["mean"] if val.get("n") else "—",
            "%.3f" % val["median"] if val.get("n") else "—",
            "%.3f" % val["win"] if val.get("win") is not None else "—", ex, exbt))
    return "**%s**\n\n" % title + "\n".join(lines) + "\n"


def gate_table(r):
    CN = {"x_pos_LOW": "位置档 LOW", "x_pos_MID": "位置档 MID", "x_pos_HIGH": "位置档 HIGH",
          "x_r20pos": "r20 ≥ 0（现闸门）", "x_r20neg": "r20 < 0（被闸掉）",
          "x_rspos": "rs ≥ 0（P0-2 选择层闸）", "x_rsneg": "rs < 0（被闸掉）",
          "x_near": "距前一日低 ≤5%（位置闸）", "x_far": "距前一日低 >5%（被闸掉）",
          "x_top3": "leader 全榜 top3", "x_bot50": "leader 后半 50%"}
    lines = ["| 条件 | theme-day | 同主题超额均值% | 中位% | 胜率 | 超额块状t |", "|---|---|---|---|---|---|"]
    for k, v in r["gates"].items():
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            CN.get(k, k), v.get("n", "—"),
            "%.3f" % v["mean"] if v.get("n") else "—",
            "%.3f" % v["median"] if v.get("n") else "—",
            "%.3f" % v["win"] if v.get("win") is not None else "—",
            "%.2f" % v["block_t"] if v.get("block_t") is not None else "—"))
    return "\n".join(lines) + "\n"


def ic_table(r):
    CN = {"ic_leader": "leader（三因子等权）", "ic_r60": "r60 分位", "ic_amt20": "amt20 分位",
          "ic_lim": "涨停次数分位", "ic_rs": "rs（个股 r20 − 主题 r20）", "ic_r20": "r20",
          "ic_leader_cand": "leader（仅候选池内）", "ic_leader_lowmid": "leader（仅过闸池内）"}
    lines = ["| 因子 | theme-day | IC 均值 | IC 中位 | 为正比例 | 块状t |", "|---|---|---|---|---|---|"]
    for k, v in r["ic"].items():
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            CN.get(k, k), v.get("n", "—"),
            "%.4f" % v["mean"] if v.get("n") else "—",
            "%.4f" % v["median"] if v.get("n") else "—",
            r["ic_pos_rate"].get(k, "—"),
            "%.2f" % v["block_t"] if v.get("block_t") is not None else "—"))
    return "\n".join(lines) + "\n"


def decile_table(rs):
    lines = ["| leader 十分位 | 5 日超额% | 10 日超额% | 20 日超额% |", "|---|---|---|---|"]
    for k in map(str, range(10)):
        row = [k]
        for r in rs:
            row.append("%.3f" % r["leader_deciles"][k] if r and k in r["leader_deciles"] else "—")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def theme_table(r):
    lines = ["| 主题 | theme-day | t1 超额均值% | t1 胜率 | t1 块状t | legacy 超额% | legacy 块状t |",
             "|---|---|---|---|---|---|---|"]
    rows = sorted(r["by_theme"].items(), key=lambda kv: -(kv[1]["t1"].get("mean") or -99))
    for th, v in rows:
        t1, lg = v["t1"], v["legacy"]
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            th, v["n_theme_days"],
            "%.3f" % t1["mean"] if t1.get("n") else "—",
            "%.3f" % t1["win"] if t1.get("win") is not None else "—",
            "%.2f" % t1["block_t"] if t1.get("block_t") is not None else "—",
            "%.3f" % lg["mean"] if lg.get("n") else "—",
            "%.2f" % lg["block_t"] if lg.get("block_t") is not None else "—"))
    return "\n".join(lines) + "\n"


def seg_table(r5, r10, r20):
    lines = ["| 段 | 臂 | 5 日超额% | 5 日块状t | 10 日超额% | 20 日超额% |", "|---|---|---|---|---|---|"]
    for tag in ("seg_H1", "seg_H2"):
        for a in ("t1", "t12", "lowmid", "legacy"):
            row = [tag.replace("seg_", "") + "（切点 " + r5[tag]["split_at"] + "）" if a == "t1" else "", ARM_CN[a]]
            for r in (r5, r10, r20):
                v = (r.get(tag) or {}).get(a) or {}
                row.append("%.3f" % v["mean"] if v.get("n") else "—")
                if r is r5:
                    row.append("%.2f" % v["block_t"] if v.get("block_t") is not None else "—")
            lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def main():
    r5, r10, r20 = load("eval_h5.json"), load("eval_h10.json"), load("eval_h20.json")
    pool, trades = load("eval_pool.json"), load("eval_trades.json")
    if not r5:
        print("缺 eval_h5.json，先跑 eval_pick_selection.py")
        return 1
    L = []
    A = L.append
    A("# 选择层（confirm_pick v2.1 / pick_v2）离线验收：它把「好票」排到前面了吗？\n")
    A("> 生成：2026-09-14 · 脚本 `jobs/eval_pick_selection.py` · 产物 `.dsh-tmp/buyside/eval_h{5,10,20}.json`"
      " + `eval_pool.json` + `eval_trades.json`\n"
      "> 口径：`docs/leg-metrics-spec.md`（两把尺子、同主题同日基线、块状 t）；**n<100 只作探索**。\n")
    A("## 0 一句话结论\n")
    A("- **选择层的排序是负向的**：leader 分（r60/amt20/涨停 三个分位等权）与未来 5/10/20 日**同主题超额**"
      "显著负相关（IC −0.058/−0.074/−0.087，块状 t −1.98/−2.23/−2.46），十分位单调向下（最低分位 +0.1~0.23% → 最高分位 −0.43~−0.84%）。\n"
      "- **实际选出来的票 ≈ 同池随机**：t1 超额 −0.98%（块状 t −1.42），与同池随机之差 +0.009%（块状 t 0.50）→ 选择层零增量。\n"
      "- **换掉旧扫描序没有变好**：legacy（原 DB 扫描序）超额 ≈ 0（块状 t −0.14），10/20 日反而优于 pick_v2。\n"
      "- **三个闸门里两个是负贡献**：`r20≥0`、`rs≥0` 净负；只有「距前一日低 ≤5%」是明显正贡献。位置档 LOW 好于 MID/HIGH。\n"
      "- **生产侧：pick_v2 上线 6 个交易日（09-09→09-14）没有一天真正用它的选票布腿**（09-09/10 抛错回落 legacy；"
      "09-11/14 被主题门 `theme_buyable` 挡住），实盘 13 笔去重买入**0 笔**落在它的 lowmid/t1/t2。\n")
    A("## 1 口径与样本\n")
    A("| 项 | 值 |\n|---|---|")
    A("| 评估窗口 | %s → %s（%d 个交易日）|" % (r5["window"][0], r5["window"][1], r5["n_days"]))
    A("| 样本 | %d 个「主题×日」（13 主题全跑）；池内限定版 %d 个 |" % (r5["n_theme_days"], (pool or {}).get("n_theme_days", 0)))
    A("| 入场/出场 | 次日(D+1)收盘买入 → 持 5/10/20 个交易日收盘卖出 |")
    A("| 超额 | 个股收益 − **同主题同日等权篮子**（同窗口）；另报全市场等权 |")
    A("| 统计 | 胜率/均值/中位/**ISO 周块状 t**（重叠样本）；H1/H2 按 theme-day 中位日切 |")
    A("| 254 口径 | 触发价 = low(D)×1.005，D+1 盘中 low ≤ 触发价即成交（成交率与成交后收益一起报）|")
    A("| 数据 | 生产 PG `mkt_bars_daily` 2024-11-01→2026-09-11（本地 parquet）；主题成分 = 方向层 `load_universe()` |\n")
    A("**自检（与生产代码对拍）**：把 `pick_v2` 的数据入口换成同一份本地行情 + 09-11 的 PIT 确认域快照，"
      "同一 as-of 跑两边：农业 4/4 只、稳增长/基建 4/4 只逐只一致；AI/算力/科技仅在「风向标」并列（leader 差 <1e-15）"
      "导致的边界上不同（见 §7）。向量化 position 复刻与 `position_class` 一致率 300/300。\n")
    A("## 2 各臂对照（主表）\n")
    A(arms_table(r5, "持 5 个交易日"))
    A(arms_table(r10, "持 10 个交易日"))
    A(arms_table(r20, "持 20 个交易日"))
    A("> 读法：`t1` 是 pick_v2 真正会布腿的票；`cand` 是候选池全体；`lowmid` 是过位置闸全体；"
      "`legacy` 是 2026-09-09 之前线上那版（DB 扫描序取前 2 个 LOW/MID）。`basket` 的绝对收益是全主题等权、"
      "其超额恒 0；`market` 给的是「全市场 − 主题篮子」的差，用来判断主题 beta 本身。\n")
    A("### 2.1 254 回踩买点口径（同一批票，换入场方式）\n")
    A("| 臂 | 挂单成交率 | 成交后收益均值% | 中位% | 胜率 | 块状t |\n|---|---|---|---|---|---|")
    for a, v in (r5.get("arm_254") or {}).items():
        vv = v["value"]
        A("| %s | %s | %s | %s | %s | %s |" % (ARM_CN.get(a, a), v["fill_rate"],
                                              "%.3f" % vv["mean"] if vv.get("n") else "—",
                                              "%.3f" % vv["median"] if vv.get("n") else "—",
                                              "%.3f" % vv["win"] if vv.get("win") is not None else "—",
                                              "%.2f" % vv["block_t"] if vv.get("block_t") is not None else "—"))
    A("\n> 对比 §2 同臂的「次日收盘买入」收益：t1 −1.425% → 254 挂单 −2.236%（5 日）。"
      "**回踩挂单是逆向选择**：只有继续下跌才成交，成交样本天然更弱。这是买点层的问题，与选股能力分开拍板。\n")
    A("## 3 闸门逐条体检（全成分口径，不选股）\n")
    A(gate_table(r5))
    A(gate_table(r10))
    A(gate_table(r20))
    A("## 4 排序能力（IC 与十分位）\n")
    A(ic_table(r5))
    A(ic_table(r20))
    A(decile_table([r5, r10, r20]))
    A("## 5 分主题 / 分段\n")
    A(theme_table(r5))
    A(seg_table(r5, r10, r20))
    A("## 6 方向层池内限定（生产产量口径）\n")
    if pool:
        A(arms_table(pool, "只在方向层池内主题上选（持 5 日）"))
        A(gate_table(pool))
        A(ic_table(pool))
    A("## 7 生产现状核查（改代码之前必须先看这一节）\n")
    A("### 7.1 pick_v2 在生产里到底跑没跑（证据：`logs/rotation_switch_arm/*.json`）\n")
    A("| 日期 | CONFIRMED_POOL | pick_v2 结果 | 实际布的买腿 |\n|---|---|---|---|")
    A("| 09-09 | ['农业'] | **无任何 v2 输出**（成功会打 `[WOLF_PICK_C]`、失败会打 `WOLF_PICK_V2_ERR`，都没有）| SZ301035 润丰股份、SZ002069 獐子岛（= redesign 文档里的「旧 v1 布腿」）|")
    A("| 09-10 09:20 | ['农业'] | **异常**：`HTTP Error 307: Temporary Redirect` | SZ000568 泸州老窖、SH603231、SH603879 |")
    A("| 09-10 14:11 | ['农业'] | **异常**：`cannot access local variable '_key'` | SH600300、SH600737、SH603231 |")
    A("| 09-11 | 未打印（主题门挡） | 未执行（路径 B 未进入） | 无买腿（`buy_legs []`）|")
    A("| 09-14 | 未打印（主题门挡） | 未执行 | SZ002156、SH600584、SH603823、SZ002409（路径 A `pick_buy`）|\n")
    A("- 09-11/09-14 的 `SKIP_THEME_NOT_BUYABLE`：半导体/芯片、新能源/电池 **两个池内主题都被结构门挡掉**"
      "（`stage=suspect/not_confirmed`）→ 路径 B 根本没进，选票全部来自路径 A（`pick_buy`，另一套口径，"
      "**没有** r20/rs/距前低 这三个闸）。\n"
      "- 09-09/09-10 的 `WOLF_PICK_V2_ERR` 会被 `confirm_pick` **静默回落**到 legacy 扫描序（只打一行 stderr），"
      "这正是「选择层看着上线了、实际没生效」的机制。\n"
      "- 附带：`[rotation] 删票黑名单读取失败: No module named 'app'` → G3 删票过滤在路径 A 实际未生效；"
      "`BOARD_FILTER removed 1 个无权限板块买腿` → 创业板/科创板买腿会被账户权限砍掉"
      "（v2.1 展示用的神农 300189 就在创业板）。\n")
    A("### 7.2 实盘买入 vs 选择层（以真实成交为入场）\n")
    if trades and trades.get("production_trades"):
        ts = trades["production_trades"]["trades"]
        buys = []
        seen = set()
        for t in ts:
            if t["direction"] not in ("买入", "buy"):
                continue
            k = (t["symbol"], t["date"])
            if k in seen:
                continue
            seen.add(k)
            buys.append(t)
        n_in = sum(1 for t in buys if any(v["cand"] for v in t["in_pool"].values()))
        n_t1 = sum(1 for t in buys if any(v["t1"] for v in t["in_pool"].values()))
        A("- 窗口 08-28→09-11 去重买入 **%d 笔**：落在 pick_v2 **候选池**的 **%d 笔**，"
          "落在 **tier1/tier2（真正会布腿）** 的 **%d 笔**。" % (len(buys), n_in, n_t1))
        A("- 其余买入在其主题的 leader 榜上是**中位排名**（示例：`rank 679/679`、`512/679`、`352/679`），"
          "且多数标的是 ETF（588170/512480）或 T 账户腿 —— 说明**实盘买入不来自选择层**。\n")
        A("| 账户 | 日期 | 标的 | 主题（首个命中） | seed leader | 主题内排名 | 5 日收益% |\n|---|---|---|---|---|---|---|")
        for t in buys:
            prev = {k.split("|")[1]: v for k, v in t["in_pool"].items() if k.startswith("asof_prev")}
            if prev:
                th0 = max(prev, key=lambda k: prev[k]["leader"])
                lead, rk = prev[th0]["leader"], prev[th0]["rank_of"]
            else:
                th0, lead, rk = (t.get("themes") or ["—"])[0], None, None
            A("| %s | %s | %s | %s | %s | %s | %s |" % (t["account"], t["date"], t["symbol"], th0,
                                                        lead, rk, t.get("fwd5")))
    A("\n## 8 结论：现状 → 缺口 → 建议动作（待拍板，**本页不改任何买入侧规则**）\n")
    A("| # | 现状（数字+样本） | 性质 | 建议动作 |\n|---|---|---|---|")
    A("| 1 | pick_v2 上线 6 个交易日（09-09→09-14）**没有可证的一天用它的选票布腿**（09-10 抛错回落 legacy、"
      "09-11/14 未进入路径 B、09-09 无任何 v2 输出）；实盘 13 笔去重买入 0 笔在它的 tier1/t2、仅 2 笔在候选池 | 接线失效（可回退） | "
      "先补**可见性**：pick_v2 报错/未执行必须显式告警，不得静默回落 legacy；再谈调参 |")
    A("| 2 | leader 分与未来超额**负相关**（IC −0.058/−0.074/−0.087，块状 t −1.98/−2.23/−2.46；n=2119 theme-day） | 因子方向错（「我们的代理」 vs 他的「辨识度」） | "
      "把「老龙头榜」的用途从**选票**降级为**环境/风向标指标**；选票排序口径需重新设计并离线验收 |")
    A("| 3 | t1 超额 −0.98%（5 日）/ −2.37%（10 日）；与同池随机差 +0.009%（块状 t 0.50） | 选择层零增量 | "
      "在①②未修好前，**不要**把仓位/资金往选择层集中；保留 legacy 作为对照基线 |")
    A("| 4 | `rs≥0` 闸：pos −0.205% vs neg +0.055%；`r20≥0` 闸：pos −0.369% vs neg −0.055% | 闸门净负 | "
      "（拍板后）各加一个开关默认关掉，离线复算两段不劣再上；定向单测 |")
    A("| 5 | 距前一日低 ≤5% 闸：−0.039% vs >5% −0.551%；位置档 LOW −0.132% vs MID −0.484% | 有效闸门 | 保留；"
      "可评估把 LOW/MID 收窄为 LOW（需单独验收）|")
    A("| 6 | 254 挂单成交率 74%，成交后 −2.24% 差于次日收盘入场 −1.43% | 买点逆向选择 | 单独立项（买点层），"
      "先做「成交率 vs 成交后收益」的分档，不混进选股结论 |")
    A("| 7 | 低位埋伏回合级 n=10（阶段 0 基线）太小 | 样本不足 | 把回合样本扩到可下结论量级（回合级口径，别用腿级）|")
    A("\n## 9 已知局限（随结论一起读）\n")
    A("1. **主题成分是当前快照**（生产 `stock_pool.db` 2026-08-03 版）→ 有轻微生存/前视偏差；"
      "方向层、gate 的历史验收同样如此，口径一致。\n"
      "2. **确认链候选域不可 PIT 回放**：`stock_confirm_result.json` 每日被覆盖，`data/_archive/` 只有 09-11 一份，"
      "PG `daily_artifacts` 也只有一天 → 本页验收的是**规则**（leader 排序 + 三闸 + 容量分位），不是生产的实际候选域。\n"
      "3. **持有窗口**：5/10/20 日三个口径方向一致，但都不是「低位埋伏 20–60 日」的完整口径（blueprint §6）；"
      "要回答「低位埋伏为什么亏」还需回合级扩样。\n"
      "4. **未建模**：涨跌停不可成交、停牌（因子已按可用序列修正）、费用、ETF（本轮不含 ETF 腿）。\n"
      "5. **风向标并列**：leader 完全并列时（差 <1e-15）谁当风向标会影响当日是否布腿；本页结果按现有排序键，"
      "并在自检里标出这处边界（AI/算力/科技 09-11）。\n"
      "6. 本页**不含**任何生产改动；所有建议动作都需用户拍板后才动代码（一个开关 + 定向单测 + 独立 commit + 生产验证）。\n")
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("wrote", OUT, os.path.getsize(OUT), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
