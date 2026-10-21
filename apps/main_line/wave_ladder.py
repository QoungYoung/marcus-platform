# -*- coding: utf-8 -*-
"""wave_ladder.py — 他的**浪型假设链 + 自动降级**（2026-10-15 原话落地为影子）。

狼大原话（帖 47288722，2026-10-15 10:17，lou 1224）：
  · 「之前的判断是一级一级的降低的 —— 首先判断了整波为**调整浪**，可能性有
     **失败4-5 / 成功4-5 / 下跌1浪**这三种，**当每一种达不到的时候自动降级为下一种**。
     这就是技术在股市里的作用：通过**硬性条件**调整**操作策略、预期管理、仓位管理**。」
原话（同日 09:56，lou 1221，降级实例）：
  · 「今天收盘后**如果没有收回跳空跌幅** ⇒ 整波浪型从**主升阶段4浪调整**转为**主跌1浪**；
    后续预期从**5浪上升**转为**大B浪反弹**；操作策略转为**自救**；持仓精简为个别算力和部分题材。」
原话（2026-08-05 17:09）：
  · 「4-5 对应就是高位科技的 C 杀，**可能 4-5 是失败5**」⇒ 「失败」= 不创新高的截断 5 浪。

**档位顺序**（2026-10-15 用户拍板，按时间推进而非他字面顺序）：
    4浪调整 → 成功4-5（整理结束·创出新高）→ 失败4-5（冲高不过前高后回落）→ 下跌1浪（破位）
  · **单向**：`rung = max(上一档, 当日判定档)` —— 他的链条只降不升 ✓；
    要“回档”（如 08-05 他改口「肯定按 4 接 5 做」）必须人工 `reset`，不自动回退 ✓。
  · **操作/预期**（每档对他实际姿态的对照，见各档 `expect`/`operation`）：
    4浪调整 → 吃反抽、以安全带走利润为主（`t_only`；他 07-31~08-03 只做半导体 ETF ✓）
    成功4-5 → 调整结束、主升末段在走（`side`；他 08-05「按 4 接 5 做」✓）
    失败4-5 → 反抽夭折、不新建仓（`defense`；他 10-09~10-13 避海外链、只吃差价 ✓）
    下跌1浪 → **不新建仓（`defense`，tier 目标 30%）＋ 存量自救（`mode=自救`）** —— 他 10-15「操作策略转为
              自救」＋ 2025-09-09「熊市不做，什么都不做」（原表 down→defense；详见 docs/wave-operation-table.md §2）

口径（带 ⛔ 的是我们的代理，他没给量化；**教材依据**见本文件「结构位（教材铁律②）」一节，出处
中山证券投资者教育《波浪理论》https://www.zszq.com/tzzyd/article/292 —— 该页原文即“两条基本数浪规则”）:
  · **未收回跳空** = 当日 `high < 前收`（真跳空）∧ `close < 前收`（跌幅未收回）——他的原话条件 ✓；
  · **创新高** = `close >= 前 60 日收盘最高`（成功4-5 的硬证据 ⛔ 窗口自定）；
  · **主跌1浪的破位**（2026-10-15 换教材口径）= 收盘 < **主升 1 浪顶**（铁律②「第四个浪的底部不可以低于
    第一个浪的浪顶」；实测 3174.27/2024-05-20，他引用的 3674.40 是再前一个主级 H ⇒ 可用
    `WOLF_WAVE_LADDER_W1` 钉住）∨ **跳空且破 20 日低位** ∨ 关键位；**单日跳空只降一档**到「失败4-5」
    （实测：2026-08-19 一个 0.73% 跳空直接判主跌1浪 ⇒ 9~10 月误判两个月 ✗）；
  · **失败4-5** = 近 20 日反弹**摸到前高附近**（高点 ≥ `NEAR_MIN`×前 60 日最高，⛔ 0.95）
    ∧ 未创 60 日新高 ∧ 自该高点回落 ≥ `FADE_MIN`(⛔ 3%)
    —— “摸到前高附近”这一条是 2026-10-15 实测补的：**少了它，深调里任何一次 3% 反弹
       都会把档位误判成「失败4-5」**（首测 2026-07-31 就误报，而他当时说的是「调整浪」✗）；
  · 数据：`data/index_daily_<code>.json`（东财日线，`[date, close, high, amount]` ✓ 已实测），
    缺失回落 `data/指数数据/index_daily/<code>.csv`；**取不到一律返回 None（fail-open）** ✓；
  · 操作/仓位口径复用 `wave_alloc.WAVE_ALLOC`（同一张表，避免两处各写一份 ✓）。

离线验收（`python jobs/eval_wave_ladder.py`，2026 全年 as-of 回放，RESET=5%：8 次降级 / 5 次重定浪 / 无非法升档 ✓）：
  · 2026-03-23 [3] 下跌1浪 —— 他 03-23 在防、03-25「减回 50% 内」✓
  · 2026-04-09 [3]（锁着）—— 他 04-09「白线在上 肯定没有买点 ⇒ 减回 50-55%」✓
  · 2026-07-31 [0] 4浪调整（`t_only`）—— 他「整波为调整浪」✓
  · 2026-08-05 [1] 成功4-5（`side`，「止跌确认：20日低点 3764.15 冻结13日 ∧ 距低+3.0% ∧ 站上MA20」）
    —— 他说「看到上面守了 3800 ⇒ 肯定按 4 接 5 做」并在半导体吃 20 个点 ✓✓
  · 2026-10-09 ~ 10-14 [3] 下跌1浪（`defense` ＋ `mode=自救`）—— 他「避海外链、只吃差价」→ 10-15「转为自救」✓
  · **2026-08「偏保守」已修**（2026-10-15 第二轮）：rung1 不再只认「创 60 日新高」，改为**止跌确认**
    = 20 日低点**冻结 ≥5 日** ∧ **距低 ≥3%** ∧ **站上 MA20** ⇒ 2026-08-05（他说「按 4 接 5 做」的
    同一天）判出 [1] 成功4-5/`side` ✓。两窗口实测可分：8 月距低 +3.0~5.4% ∧ 低点自 07-17 冻结；
    10 月距低 ≤2.3% ∧ 低点一路刷新（3823→3811→3797→3767）✓。
    **残余风险 ⚠️**：rung1 会**放行建仓**（`side`）⇒ 若误报的代价大于误漏，请收紧 `OFF_LOW`/`LOW_AGE`。
    2026-08-19「一跳空直接判主跌1浪」的偏差**已修**（单日跳空只降一档）✓。

开关：`WOLF_WAVE_LADDER`（默认 **0** ⇒ **影子**：只落盘 + 只读，不改任何交易行为）
      `WOLF_WAVE_LADDER_FLOOR`（默认 0 ⇒ 不启用；>0 时额外作为“关键位”，跌破即主跌1浪）
自检：`python apps/main_line/wave_ladder.py`
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ★ DATA_DIR 可能是"设了但为空"（生产 backend 容器即如此：DATA_DIR= ✗）⇒ 空串必须回落 "data" ✓
#   （os.environ.get("DATA_DIR", "data") 对空串**不会**取默认值 ✗，会把所有路径解析成 "./xxx" ）
DATA = (os.environ.get("DATA_DIR") or "").strip() or "data"
def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


# ★ 2026-10-16（等级模型 E1）：三个窗口改成**可用环境变量覆盖** ⇒ 同一套链能按"度"重标跑在不同周期上 ✓
#   （默认值一字未改 ⇒ 生产行为不变 ✓）。周线做"大级别"时的原则性取值见 jobs/eval_degree_weekly.py：
#   RALLY_WIN≈26 周（半年）/ HI_WIN≈104 周（两年）/ ORIGIN_BARS≈200 周（≈4 年，与日线 900 日 ≈3.5 年同量级 ✓）
MIN_BARS = int(_envf("WOLF_WAVE_LADDER_MIN_BARS", 80))   # 少于这个根数不给结论（fail-open）
HI_WIN = int(_envf("WOLF_WAVE_LADDER_HI", 60))           # 新高/破位窗口
RALLY_WIN = int(_envf("WOLF_WAVE_LADDER_RALLY", 20))     # 反弹窗口
RALLY_MIN = 0.03                   # ⛔ 代理：认定“冲出过一段”
FADE_MIN = 0.03                    # ⛔ 代理：认定“已经回落”
NEAR_MIN = _envf("WOLF_WAVE_LADDER_NEAR", 0.98)    # ⛔ 代理：认定“摸到前高附近”
GAP_MIN = _envf("WOLF_WAVE_LADDER_GAP_MIN", 0.005)  # ⛔ 代理：真跳空的最小幅度（2026 全年只有 5 天 >0.5%）
RESET_RALLY = _envf("WOLF_WAVE_LADDER_RESET", 0.05)  # ⛔ 代理：反抽 ≥5% ⇒ 空头结构被破坏 ⇒ 重定浪型
# ↑ 2026-10-15 扫 8%/6%/5%：8% ⇒ 7-17 判夭折后**锁死到 10-14**（他 8 月在买半导体 ✗）；
#   5% ⇒ 07-31/08-05 回到「4浪调整(试探)」、08-19 才转防守 ✓ 与他的公开姿态一致 ✓
OFF_LOW = _envf("WOLF_WAVE_LADDER_OFF_LOW", 0.03)  # rung1 止跌确认：距 20 日低点反弹 ≥3%（⛔ 代理）
LOW_AGE = int(_envf("WOLF_WAVE_LADDER_LOW_AGE", 5))   # rung1 止跌确认：20 日低点已冻结 ≥5 个交易日
# ↑ 两窗口实测（2026-10-15）：窗口A（他 08-05「按4接5做」并吃半导体）距低 +3.0~5.4% ∧ 低点自 07-17 冻结；
#   窗口B（他 10 月上「调整/反抽、避海外链」）距低 ≤2.3% ∧ 20 日低点一路被刷新（3823→3811→3797→3767）⇒ 分得开 ✓
ZIGZAG_PCT = _envf("WOLF_WAVE_LADDER_ZIGZAG", 0.10)     # 主级 zigzag 阈值（10% ⇒ 上证一年几次拐点）
PCT_FINE = _envf("WOLF_WAVE_LADDER_ZIGZAG_FINE", 0.05)  # 细级 zigzag（只出预警位，不触发降级）
ORIGIN_BARS = int(_envf("WOLF_WAVE_LADDER_ORIGIN_BARS", 900))  # 主升起点回溯窗口（≈3.5 年）
LOW60_TRIGGER = os.getenv("WOLF_WAVE_LADDER_LOW60", "0").strip() in ("1", "true", "yes")
# ↑ 2026-10-15 起**默认关**：60 日新低是早期拍的代理；换教材「铁律②」后它退为参考量，=1 可 A/B 回旧行为 ✓

RUNGS: List[Dict[str, str]] = [
    {"key": "w4_adj", "name": "4浪调整",
     "expect": "调整未完，等 4-5 收尾（只吃反抽）", "operation": "t_only",
     "hold": "未创新高 ∧ 未摸前高后夭折 ∧ 未破 60 日收盘低位 ∧ 无未收回跳空"},
    {"key": "w45_ok", "name": "成功4-5",
     "expect": "调整末腿走完 ⇒ 主升末段（5浪上升）", "operation": "side",
     "hold": "收盘创 60 日新高 ∨ **止跌确认**（低点冻结 ≥5 日 ∧ 距低 ≥3% ∧ 站上 MA20）"},
    {"key": "w45_fail", "name": "失败4-5",
     "expect": "反抽夭折（冲高不过前高后回落）", "operation": "defense",
     "hold": "近20日摸到前高附近 ∧ 未创60日新高 ∧ 已回落达标"},
    {"key": "down1", "name": "下跌1浪",
     "expect": "大B浪反弹（自救窗口：借反弹出，不新建仓）", "operation": "defense",
     "hold": "未收回跳空 ∨ 收盘 < 主升 1 浪顶（铁律②失效）∨ 跌破关键位"},
]
RUNG_INDEX = {r["key"]: i for i, r in enumerate(RUNGS)}

# ────────────── 叠加档：主升 3-3 / 主升回调 3-2（2026-10-15 补，用户拍板 B）──────────────
# 为什么是「叠加」而不是插进 RUNGS：4 浪族那 4 档是**单向链**（只降不升 + 重定浪）✓；
# 而 3-x 在时间上位于整条链**之前**（3-2 → 3-3 → 3-4 → 3-5 → 4-1..4-5 → 5 或 down）⇒ 硬插会破坏单向性 ✗。
# 口径来自语料（见 docs/wave-operation-table.md §4）：
#   · 3-2→3-3 确认：「**站稳 4006 三天**…**三大指数同时化解顶背离**…直接跳 3-3」（2025-10-29）
#     ⇒ 量化：**连续 ≥3 日收盘 > 前 20 日最高** ∧ 量能不缩（三大指数同步我们本地只有上证 ⇒ 记为向量化 TODO ⛔）
#   · 3-2 回调：「3-2 后半段最重要的操作策略就是，**保持 50%** 仓位」（2025-10-09）⇒ operation `side`（原表 3-2=side ✓）
MAIN_UP = {"key": "mainup_33", "name": "主升3-3", "level": "d3", "sub_level": "3-3", "operation": "build",
           "expect": "主升中段（最强段：建仓/追/产业链）"}
MAIN_UP_PB = {"key": "mainup_32", "name": "主升回调3-2", "level": "d3", "sub_level": "3-2", "operation": "side",
               "expect": "主升中的回调（原表：观望/调仓换股；语料：保持约 50% 仓位）"}
MAIN_UP_STREAK = int(_envf("WOLF_WAVE_LADDER_UP_STREAK", 3))   # 「站稳三天」
MAIN_UP_VOL = _envf("WOLF_WAVE_LADDER_UP_VOL", 1.0)            # 量能不缩：当日额 ≥ 20 日均额 × 该系数
MAIN_UP_PULLBACK = _envf("WOLF_WAVE_LADDER_UP_PB", 0.03)       # 主升中的回调幅度门槛（⛔ 代理）

# ────────────── 补档 B（2026-10-15）：4-3 筑底 / W底确认 ──────────────
# 依据（原表 + 他原话）：
#   · 4-3 筑底 = `side`「可走 4-4 但别预期太高，**调仓换股分批接主线**，轻仓」（wave_agent.py:320）
#     判据：4 浪调整档内 + 20 日低点已冻结 ≥LOW_AGE + 仍在低位(距低 ≤5%) + **缩量**(20 日均额 ≤ 60 日均额×0.8)
#     ＋未破低（`close > lo20`）—— 就是他的「地量+缩量止跌=下跌动能衰竭→底部线索(可轻仓 side)」✓
#   · W底确认 = `build`（原表「W底(确认建仓)」+ d1「反转大1浪(底部确认后建仓)」）
#     判据：**两个相近低点（≤3%）+ 中间颈线** + 今日**放量收上颈线** —— 他 10-15 原话「**需放量突破颈线才确认重仓 build**」✓
#     命中 ⇒ **重定浪**（底部确认推翻下跌结构 ✓）+ level=d1/sub=W底/op=build。
# ────────────── 补档 C（2026-10-15）：防顶 d5/双头 ＋ B反/ABC/下跌浪4反弹 ＋ 4-1 回调初 ＋ C杀 语义 ──────────────
# 全部照原表 operation（wave_agent.py:322-327，不自己编 ✓）：
#   d5 末段/衰竭 ⇒ **exit**（兑现降仓防顶）｜双头/M顶 ⇒ **exit**（见顶防跌）｜下跌浪4反弹 ⇒ **exit**（确认4必有5，先卖）
#   B反 ⇒ **t_only**（典型诱多别抄底）｜ABC 期间 ⇒ **t_only**（不参与、等结束）｜4-1 回调初 ⇒ **defense**（观望降仓）
# 判据出处：他 2025-05-15「银保证券出**长上影+放量**，然后**大盘缩量** —— 这就是顶部结构形成的重要条件」✓；
#   原表「上涨末段(3-5/4-5/d5/双头M顶)：**缩量滞涨+量价背离**→顶部(exit/defense)」✓；
#   他 2026-06-30「缩圈到最后大票带指数上、情绪小票反而不如大票 = 加速完成」✓。
TOP_SHADOW = _envf("WOLF_WAVE_LADDER_TOP_SHADOW", 0.008)   # 长上影：(high-close)/close 门槛
TOP_SHRINK = _envf("WOLF_WAVE_LADDER_TOP_SHRINK", 0.85)    # 缩量新高：当日额 / 20 日均额 上限
TOP_STALL = _envf("WOLF_WAVE_LADDER_TOP_STALL", 0.01)      # 滞涨：近 5 日累计涨幅上限
DT_NEAR = _envf("WOLF_WAVE_LADDER_DT_NEAR", 0.03)          # 双头：两高点相近度
DT_WIN = int(_envf("WOLF_WAVE_LADDER_DT_WIN", 160))        # 双头：回看窗口
B_REBOUND_MIN = _envf("WOLF_WAVE_LADDER_BREB", 0.03)       # B反：自低点反弹幅度门槛
SHARP_DROP = _envf("WOLF_WAVE_LADDER_SHARP", 0.02)         # C杀：单日跌幅门槛
SHARP_VOL = _envf("WOLF_WAVE_LADDER_SHARPVOL", 1.2)        # C杀：放量倍数（相对 20 日均额）

TOP_SELL = {"key": "top_sell", "name": "末段/顶部", "level": "d5", "sub_level": "末段", "operation": "exit",
            "expect": "上涨末段（缩量滞涨/量价背离/长上影）⇒ 兑现降仓、防顶（原表 d5 = exit）"}
DOUBLE_TOP = {"key": "dtop", "name": "双头/M顶", "level": "d5", "sub_level": "双头", "operation": "exit",
              "expect": "双头未创新高 ⇒ 见顶防跌（原表：做不做双头都撤、不做 T）"}
B_REBOUND = {"key": "breb", "name": "B反", "level": "down", "sub_level": "B反", "operation": "t_only",
             "expect": "下跌中的反弹（典型诱多别抄底）⇒ 只做T、不追（原表 B反 = t_only）"}
ABC_DOWN = {"key": "abc", "name": "ABC", "level": "down", "sub_level": "ABC", "operation": "t_only",
            "expect": "完整三段 A跌B反C杀（不参与、等结束）⇒ 只做T（原表 ABC = t_only）"}
LEG4_SELL = {"key": "leg4", "name": "下跌浪4反弹", "level": "down", "sub_level": "下跌4反弹", "operation": "exit",
             "expect": "下跌浪的 4 反弹（确认 4 必有 5）⇒ 先卖（原表 = exit）"}
BASE41_OFF = _envf("WOLF_WAVE_LADDER_41_OFF", 0.05)     # 4-1：距 60 日前高上限（⛔ 自设）
BASE41 = {"key": "basis_41", "name": "4-1回调初", "level": "d4", "sub_level": "4-1", "operation": "defense",
          "expect": "4-1 回调初 ⇒ 观望降仓（原表 4-1 = defense）"}

BASE43_OFF = _envf("WOLF_WAVE_LADDER_43_OFF", 0.05)     # 4-3：距 20 日低点上限
# ★ 2026-10-20 用户拍板：缩量门槛 0.8 → **1.0**。
#   原 0.8 是自设代理 ⛔，代价是 2016 年后 4-3 档 **一天都出不来** ✗（实测：0.8→0 天 / 0.9→17 天 / 1.0→53 天）；
#   而原表与他的定性口径是「4-3 筑底 = side（可分批接）」「地量+缩量止跌」⇒ 只要**量能在缩**（≤ 过去 60 日均量）即符合 ✓，
#   不需要"缩到 0.8 倍"这种强口径 ✗。放宽后 4-3 出 side，**属生产放松**（买类从"拦"变"需人工确认"）⇒ 已经用户确认 ✓。
BASE43_VOL = _envf("WOLF_WAVE_LADDER_43_VOL", 1.0)       # 4-3：缩量门槛（20 日均额 / 60 日均额，≤1.0 = 量能在缩 ✓）
WB_NEAR = _envf("WOLF_WAVE_LADDER_WB_NEAR", 0.03)        # W底：两低点相近度
WB_VOL = _envf("WOLF_WAVE_LADDER_WB_VOL", 1.2)           # W底：突破颈线的放量倍数
WB_WIN = int(_envf("WOLF_WAVE_LADDER_WB_WIN", 180))      # W底：形态回看窗口（交易日）

BASE_43 = {"key": "basis_43", "name": "4-3筑底", "level": "d4", "sub_level": "4-3", "operation": "side",
           "expect": "4-3 筑底：缩量止跌、可分批接主线（原表：调仓换股分批接主线、轻仓）"}
W_BOTTOM = {"key": "w_bottom", "name": "W底确认", "level": "d1", "sub_level": "W底", "operation": "build",
            "expect": "W底确认（放量过颈线）⇒ 底部反转，建仓（原表：W底确认=建仓 / d1=底部确认后建仓）"}
_SYNC3: Optional[bool] = None   # features() 每轮写入：三指数同步状态（None=数据不全 ⇒ 退回单指数）
_ROWS_CACHE: Dict[Any, Any] = {}   # (DATA, code) → (rows, 读入时刻)：带 TTL，防长驻进程冻住 ✗
_ROWS_TTL = float((os.environ.get("WOLF_WAVE_LADDER_ROWS_TTL") or "").strip() or 60)   # 秒
# ★ 2026-10-15 修正（用户拍板 A）：下跌1浪拆成**两件事** ——
#   「**不进**」= operation `defense`（禁新建仓；tier 目标 30% ⇐ 原表 down→defense ＋ 他「熊市不做，什么都不做」）
#   「**怎么出**」= mode `自救`（存量借反弹/放量分批减；他 2026-10-15：「要出来光靠硬割不行 需要创造条件才能割肉」）
#   原来压成一个 exit（invest 0.70）既比原表松、又丢了自救的执行语义 ✗。依据：docs/wave-operation-table.md §2。
MODE_OF_RUNG = {"down1": "自救"}
MODE_NOTE = {"自救": "禁新建仓；存量只借反弹/放量减仓或做T（不硬割、不新增）"}

# ─────────────── 统一标签（2026-10-18，只加字段；不改 level/sub_level/operation）───────────────
# 背景：`w45_fail` 写成 `level=d5 + sub_level=失败5`，读 level 的消费方会误当“大5浪衰竭→exit”；
# 实际是“4-5 失败→defense”。这里只加 family/stage/status/label_note，旧字段一字不改。
FAMILY_OF_LEVEL = {"d1": "1", "d2": "2", "d3": "3", "d4": "4", "d5": "5", "down": "down"}


def _family_of(level: str, sub: str, key: str) -> str:
    """浪族：1/3/4/5/down。4-5 的成功/失败都属于 4 浪族，不是 5。"""
    if key in ("w45_ok", "w45_fail"):
        return "4"
    if key in ("dtop", "top_sell"):
        return "5"
    if key == "down1" or str(level).lower() == "down":
        return "down"
    s = str(sub or "")
    if s in ("失败5", "4-5") or s.startswith("4-"):
        return "4"
    if s in ("末段", "双头", "衰竭", "M顶"):
        return "5"
    return FAMILY_OF_LEVEL.get(str(level or "").lower(), "?")


def _stage_of(level: str, sub: str, key: str, mus: Optional[Dict[str, Any]] = None) -> str:
    """阶段：4-1..4-5 / 3-2/3-3 / 5-末段 / 双头 / down-1 / C杀。"""
    if key == "w45_ok":
        return "4-5"
    if key == "w45_fail":
        return "4-5"
    if key == "dtop":
        return "双头"
    if key == "top_sell":
        return "5-末段"
    if key == "down1":
        return "C杀" if str(sub or "") == "C杀" else "down-1"
    s = str((mus or {}).get("sub_level") or sub or "")
    for sep in ("→", "->"):
        if sep in s:
            s = s.split(sep)[0]
    if s == "失败5":
        return "4-5"
    if s:
        return s
    return str(level or "")


def _status_of(key: str, op: str, sub: str) -> str:
    """状态：forming（进行中）/ confirmed（已确认）/ failed（失败）。"""
    if key == "w45_fail":
        return "failed"
    if key in ("w45_ok", "down1", "dtop", "top_sell", "w_bottom", "mainup_33"):
        return "confirmed"
    if key in ("w4_adj", "mainup_32", "basis_43", "basis_41", "breb", "abc", "leg4"):
        return "forming"
    if "失败" in str(sub or "") or str(op or "").lower() == "exit":
        return "failed"
    return "forming"


def _label_note_of(key: str, sub: str = "") -> str:
    if not key and str(sub or "") == "失败5":
        return "4-5失败：反抽夭折；不是大5浪衰竭；operation=defense"
    if not key and str(sub or "") == "末段":
        return "大5浪末段/衰竭：operation=exit"
    return {
        "w45_fail": "4-5失败：反抽夭折；不是大5浪衰竭；operation=defense",
        "w45_ok": "4-5成功：调整末腿走完；不是大5浪末段；operation=side",
        "top_sell": "大5浪末段/衰竭：operation=exit",
        "dtop": "双头/M顶：operation=exit",
        "down1": "下跌1浪：禁新建仓 + 存量自救（mode=自救）",
        "w4_adj": "4浪调整：只吃反抽；子档见 stage",
    }.get(key, "")

# 仓位/操作口径复用 wave_alloc（缺依赖时退回本地副本，值保持一致 ✓）
try:  # pragma: no cover - 依赖环境
    from wave_alloc import WAVE_ALLOC as _ALLOC  # type: ignore
except Exception:  # pragma: no cover
    _ALLOC = {"build": (0.40, 0.35, 0.25, 1.0), "t_only": (0.70, 0.20, 0.10, 1.0),
              "side": (0.60, 0.25, 0.15, 1.0), "defense": (0.60, 0.25, 0.15, 0.30),
              "exit": (0.60, 0.25, 0.15, 0.70)}


def _streak_above(hist: Sequence[Tuple[str, float, float, float]], win: int) -> int:
    """连续收盘 > 前 `win` 日最高收盘 的天数（他的「站稳三天」的量化）。"""
    n = 0
    for i in range(len(hist) - 1, max(win, 0), -1):
        ref = max(r[1] for r in hist[i - win:i])
        if hist[i][1] > ref:
            n += 1
        else:
            break
    return n


SYNC_CODES = ("000001.SH", "399001.SZ", "399006.SZ")   # 上证 / 深证成指 / 创业板指 —— 他说的「三大指数」


def sync3(asof: str, look: int = 3) -> Optional[bool]:
    """「**三大指数同时**化解顶背离」（他 2025-10-29）——近 `look` 日内是否出现过三指数**同日**收 > 各自前 20 日最高。

    数据：`data/index_daily_<code>.json`（`jobs/sync_index_daily_multi.py` 落盘；日线走东财 ✓，
    datahubco 的 index_daily 实测只到 2026-08-03 ✗ 故不作主源）。任一指数缺数据 ⇒ 返回 None（fail-open ✓，
    上层退回"只看上证"单指数口径并在 features 里标注 ✗ 不静默改语义）。"""
    series = {}
    for code in SYNC_CODES:
        rows = load_rows(code)
        hist = [r for r in rows if r[0] <= str(asof)[:8]]
        if len(hist) < 25:
            return None
        series[code] = hist
    for k in range(max(look, 1)):
        ok = True
        for code, hist in series.items():
            seg = hist[:len(hist) - k]
            if len(seg) < 21:
                return None
            if not (seg[-1][1] > max(r[1] for r in seg[-21:-1])):
                ok = False
                break
        if ok:
            return True
    return False


def w_bottom_marks(pv: Sequence[Any], close: float, vol_ratio20: float, since_date: str = "") -> Optional[Dict[str, Any]]:
    """W底**几何**：{"l1": {date,value}, "l2": {...}, "neck": float, "confirmed": bool}（供图上标注 ✓）。

    与 `_w_bottom` 同一判据（后者只取 bool/颈线 ✓，单一来源不重复实现 ✓）。无形态 ⇒ None。
    """
    lows = [p for p in pv if str(p[3]).upper() == "L" and (not since_date or str(p[1]) >= since_date)][-2:]
    if len(lows) < 2:
        return None
    d1, d2 = str(lows[0][1]), str(lows[1][1])
    v1, v2 = lows[0][2], lows[1][2]
    if not v1 or abs(v2 / v1 - 1) > WB_NEAR:
        return None
    mids = [p for p in pv if d1 < str(p[1]) < d2 and str(p[3]).upper() == "H"]
    if not mids:
        return None
    neck = max(p[2] for p in mids)
    return {"l1": {"date": d1, "value": round(v1, 2)}, "l2": {"date": d2, "value": round(v2, 2)},
            "neck": round(neck, 2), "confirmed": bool(close > neck and vol_ratio20 >= WB_VOL)}


def _w_bottom(pv: Sequence[Any], close: float, vol_ratio20: float, since_date: str = "") -> Tuple[bool, float]:
    """W底确认：最近两个**相近**低点（差 ≤WB_NEAR）＋ 中间颈线（两低之间的最高 H）＋ 今日**放量收上颈线**。

    只用 `pivots(rows, asof)` 的**已确认拐点** ⇒ 无未来函数 ✓（前缀截断不变性单测覆盖 ✓）。
    返回 (是否确认, 颈线值)；不确认时颈线为 0。出处：原表「W底(确认建仓)」＋ 他 10-15「需放量突破颈线才确认重仓 build」✓
    """
    # 拐点元组 = (确认日, 拐点日, 值, 类型) —— 形态判定按**拐点日**排序，另要求形态足够近 ✓（无未来函数：只取已确认拐点 ✓）
    lows = [p for p in pv if str(p[3]).upper() == "L" and (not since_date or str(p[1]) >= since_date)][-2:]
    if len(lows) < 2:
        return False, 0.0
    d1, d2 = str(lows[0][1]), str(lows[1][1])
    v1, v2 = lows[0][2], lows[1][2]
    if not v1 or abs(v2 / v1 - 1) > WB_NEAR:
        return False, 0.0
    mids = [p for p in pv if d1 < str(p[1]) < d2 and str(p[3]).upper() == "H"]
    if not mids:
        return False, 0.0
    neck = max(p[2] for p in mids)
    if close > neck and vol_ratio20 >= WB_VOL:
        return True, neck
    return False, neck


def double_top_marks(pv: Sequence[Any], hi_excl: float, since_date: str = "") -> Optional[Dict[str, Any]]:
    """双头**几何**：{"h1": {date,value}, "h2": {...}, "neck": float, "confirmed": bool}（供图上标注 ✓）。

    颈线 = 两高点之间的最低 L（跌破它才是双头成立的方向 ✓）。无形态 ⇒ None。
    """
    highs = [p for p in pv if str(p[3]).upper() == "H" and (not since_date or str(p[1]) >= since_date)][-2:]
    if len(highs) < 2:
        return None
    d1, d2 = str(highs[0][1]), str(highs[1][1])
    v1, v2 = highs[0][2], highs[1][2]
    if not v1 or abs(v2 / v1 - 1) > DT_NEAR:
        return None
    lws = [p for p in pv if d1 < str(p[1]) < d2 and str(p[3]).upper() == "L"]
    neck = min([p[2] for p in lws]) if lws else 0.0
    conf = bool(v2 <= v1 and hi_excl <= v1 * (1 + DT_NEAR))
    return {"h1": {"date": d1, "value": round(v1, 2)}, "h2": {"date": d2, "value": round(v2, 2)},
            "neck": round(neck, 2), "confirmed": conf}


def _double_top(pv: Sequence[Any], hi20: float, since_date: str = "") -> bool:
    """双头/M顶：最近两个**相近**高点（≤DT_NEAR）∧ 第二个高点**未创新高**（≤ 第一个）⇒ 见顶防跌 ✓。

    只用已确认拐点 ⇒ 无未来函数 ✓。原表：「双头/M顶(见顶防跌)」+「做不做双头都撤，不做 T」✓
    """
    highs = [p for p in pv if str(p[3]).upper() == "H" and (not since_date or str(p[1]) >= since_date)][-2:]
    if len(highs) < 2:
        return False
    v1, v2 = highs[0][2], highs[1][2]
    if not v1:
        return False
    return bool(abs(v2 / v1 - 1) <= DT_NEAR and v2 <= v1 and hi20 <= v1 * (1 + DT_NEAR))


def _up33_in_window(hist: Sequence[Tuple[str, float, float, float]], win: int) -> bool:
    """近 `win` 日内是否出现过「连续 ≥MAIN_UP_STREAK 日站稳前 20 日高点」（= 3-3 曾确认 ✓）。"""
    for i in range(max(win, 1), len(hist)):
        if _streak_above(hist[:i + 1], RALLY_WIN) >= MAIN_UP_STREAK:
            return True
    return False


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：至少留痕，**绝不抛** ✓。"""
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


# ─────────────────────────── 开关 ───────────────────────────
def enabled() -> bool:
    return os.getenv("WOLF_WAVE_LADDER", "0").strip() in ("1", "true", "yes")


def floor_level() -> float:
    try:
        return float(os.getenv("WOLF_WAVE_LADDER_FLOOR", "0") or 0)
    except ValueError:
        return 0.0


def index_code() -> str:
    return os.getenv("WOLF_WAVE_LADDER_INDEX", "000001.SH").strip() or "000001.SH"


def _env_pin_w1() -> float:
    """`WOLF_WAVE_LADDER_W1` > 0 ⇒ 人工钉住「1 浪顶」（如钉他引用的 3674.40 做 A/B）。"""
    try:
        v = float(os.getenv("WOLF_WAVE_LADDER_W1", "0") or 0)
        return v if v > 0 else 0.0
    except ValueError:
        return 0.0


# ─────────────────────────── 数据层 ───────────────────────────
def _json_path(code: str) -> Optional[str]:
    name = "index_daily_%s.json" % code.replace(".", "_")
    for cand in (os.path.join(DATA, name), os.path.join("data", name)):
        if os.path.exists(cand):
            return cand
    return None


def _csv_path(code: str) -> Optional[str]:
    name = "%s.csv" % code
    for cand in (os.path.join(DATA, "指数数据", "index_daily", name),
                 os.path.join("data", "指数数据", "index_daily", name)):
        if os.path.exists(cand):
            return cand
    return None


def _load_json(code: str) -> List[Tuple[str, float, float, float]]:
    out: List[Tuple[str, float, float, float]] = []
    p = _json_path(code)
    if p:
        try:
            rows = (json.load(open(p, encoding="utf-8")) or {}).get("rows") or []
            for r in rows:
                try:
                    out.append((str(r[0])[:8], float(r[1]), float(r[2]), float(r[3] or 0)))
                except (TypeError, ValueError, IndexError) as _e:
                    _silent_alert("wave_ladder.py:json_row", _e)
        except Exception as _e:
            _silent_alert("wave_ladder.py:json_load", _e)
    return out


def _load_csv(code: str) -> List[Tuple[str, float, float, float]]:
    out: List[Tuple[str, float, float, float]] = []
    p = _csv_path(code)
    if p:
        try:
            import csv as _csv
            with open(p, encoding="utf-8") as fh:
                for c in _csv.DictReader(fh):
                    try:
                        d = str(c.get("trade_date"))[:10].replace("-", "")
                        cl = float(c.get("close"))
                        hi = float(c.get("high") or cl)
                        am = float(c.get("amount") or 0)
                        out.append((d, cl, hi, am))
                    except (TypeError, ValueError) as _e:
                        _silent_alert("wave_ladder.py:csv_row", _e)
        except Exception as _e:
            _silent_alert("wave_ladder.py:csv_load", _e)
    return out


def load_rows(code: Optional[str] = None) -> List[Tuple[str, float, float, float]]:
    """带缓存的行情读取（walk 要跑几千天 × 多个指数，不加缓存太慢 ✓）。

    ★ 2026-10-20 修：缓存**必须带 TTL** —— 首版只按 (DATA, code) 永久缓存 ✗，在**长驻进程**里
    （backend 的 uvicorn）会把数据永久冻在进程第一次读取的那一刻 ⇒ 卡片色带停在 10-15，
    10-16/10-19 显示「无浪型」（用户 2026-10-20 实测反馈 ✗）。
    注：**生产决策读的是文件 wave_state.json，不受影响** ✓；只有「接口里实时 walk」这条路会被冻住 ✗。
    TTL 由 WOLF_WAVE_LADDER_ROWS_TTL 控制（秒，默认 60）。
    """
    _ck = (DATA, code or index_code())
    hit = _ROWS_CACHE.get(_ck)
    if hit is not None and (time.time() - hit[1]) < _ROWS_TTL:
        return hit[0]
    _rows = _load_rows_uncached(code)
    _ROWS_CACHE[_ck] = (_rows, time.time())
    return _rows


def clear_rows_cache() -> None:
    _ROWS_CACHE.clear()


def _load_rows_uncached(code: Optional[str] = None) -> List[Tuple[str, float, float, float]]:
    """→ [(date8, close, high, amount)] 升序；**两源合并**（CSV 2016+ 长历史 ∪ JSON 近一年，
    同日以 JSON 为准）；取不到返回 []（fail-open）。
    长历史是**铁律② 的前提**：JSON 只回溯到 2024-11 ⇒ 看不到 2024-10-08 那个 1 浪顶（3674）。"""
    code = code or index_code()
    merged: Dict[str, Tuple[str, float, float, float]] = {}
    for r in _load_csv(code):
        merged[r[0]] = r
    for r in _load_json(code):
        merged[r[0]] = r
    return [merged[k] for k in sorted(merged)]


# ────────────── 结构位（教材「铁律②」，2026-10-15 接入） ──────────────
# 出处：中山证券投资者教育《波浪理论》https://www.zszq.com/tzzyd/article/292
#   「数浪的两条基本规则：……（二）**第四个浪的底部，不可以低于第一个浪的浪顶**」
#   「（一）第三浪永远不允许是第一至第五浪中最短的一个浪」；补充规则一＝**交替规则**。
# 意义：这就是“4浪调整”被证伪的**客观失效线** —— 比我们早期拍的“60 日新低”有依据 ✓。
def pivots(rows: Sequence[Tuple[str, float, float, float]], asof: str,
           pct: Optional[float] = None) -> List[Tuple[str, str, float, str]]:
    """**因果** zigzag 拐点（只用 ≤ asof 的数据；极值反向 ≥ pct 才确认 ⇒ 无未来函数 ✓）。
    → [(confirm_date, pivot_date, value, type)]；H 取 high、L 取 close。"""
    pct = ZIGZAG_PCT if pct is None else pct
    hist = [r for r in rows if r[0] <= str(asof)[:8]]
    seg: List[Tuple[str, str, float, str]] = []
    if len(hist) < 2:
        return seg
    ext_i, ext_v, ext_t = 0, hist[0][1], "?"
    for i in range(1, len(hist)):
        c, h = hist[i][1], hist[i][2]
        if ext_t == "?":
            if c <= ext_v * (1 - pct):
                ext_i, ext_v, ext_t = i, c, "L"
            elif c >= ext_v * (1 + pct):
                ext_i, ext_v, ext_t = i, c, "H"
            continue
        if ext_t == "H":
            if h > ext_v:
                ext_i, ext_v = i, h
            elif c <= ext_v * (1 - pct):
                seg.append((hist[i][0], hist[ext_i][0], ext_v, "H"))
                ext_i, ext_v, ext_t = i, c, "L"
        else:
            if c < ext_v:
                ext_i, ext_v = i, c
            elif c >= ext_v * (1 + pct):
                seg.append((hist[i][0], hist[ext_i][0], ext_v, "L"))
                ext_i, ext_v, ext_t = i, c, "H"
    return seg


DEGREES = [("D1", _envf("WOLF_WAVE_LADDER_D1", 0.20)), ("D2", ZIGZAG_PCT), ("D3", PCT_FINE)]
# ↑ 他的「级别」/「浪中有浪」＝ 同一序列在**不同阈值**下的拐点层级（教材：波浪等级的划分）。
SUB_LABELS = ["4-1", "4-2", "4-3", "4-4", "4-5"]
SUB_FALLING = _envf("WOLF_WAVE_LADDER_SUB_FALLING", 0.02)   # 认定「下一段已在进行」的最小回落


def degree_pivots(rows: Sequence[Tuple[str, float, float, float]],
                  asof: str) -> Dict[str, List[Tuple[str, str, float, str]]]:
    """三级拐点：D1 主级(20%) / D2 中继(10%) / D3 次级(5%) —— 对应他的「级别」。"""
    return {name: pivots(rows, asof, pct) for name, pct in DEGREES}


def sub_legs(rows: Sequence[Tuple[str, float, float, float]], asof: str,
             st: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把**主级顶之后的次级拐点**按他的编号标成 4-1…4-5（4-1＝第一段下跌、4-2＝反弹…）。

    实测（000001.SH，as-of 2026-10-14，主级顶 2026-05-14 之后）：
      4-1 = 3959.34(06-08 跌) / 4-2 = 4163.10(06-22 弹) / 4-3 = 3764.15(07-17 跌) / 4-4 = 3990.30(08-18 弹)，
      其后回落 ⇒ 当前为 **4-5 形成中**（＝他 10-15 说“下跌1浪”前的那一段）。
    他自己也是这个编号：08-04「一旦资金测试成功 **4-4** 就能走出来了」、08-11「如果不是 3900 的话那就直接
    开启 **4-5**？」⇒ 编号口径一致 ✓（4-5 走完 = 调整末腿到位 ⇒ 他的「成功4-5」，即本模块 rung1 ✓）。"""
    st = st or structural_levels(rows, asof)
    pk, pk_d = st["prior_peak"], st["prior_peak_date"]
    out: Dict[str, Any] = {"stage": "", "forming": "", "legs": [], "peak": pk, "peak_date": pk_d}
    if not pk:
        return out
    after = [p for p in pivots(rows, asof, PCT_FINE) if p[1] > pk_d][:len(SUB_LABELS)]
    for i, p in enumerate(after):
        out["legs"].append({"label": SUB_LABELS[i], "kind": "down" if p[3] == "L" else "up",
                            "date": p[1], "confirm": p[0], "value": p[2]})
    if out["legs"]:
        last = out["legs"][-1]
        out["stage"] = last["label"]
        nxt = len(out["legs"])
        close = rows[-1][1] if rows else 0.0
        if last["kind"] == "up" and nxt < len(SUB_LABELS) and close < last["value"] * (1 - SUB_FALLING):
            out["forming"] = SUB_LABELS[nxt]
    return out


def structural_levels(rows: Sequence[Tuple[str, float, float, float]], asof: str) -> Dict[str, Any]:
    """铁律② 需要的结构位（全部**因果取** ✓，取不到就 None ⇒ 上层 fail-open）：
      · origin       = 回溯 `ORIGIN_BARS` 内**最低的主级 L** ＝ 本轮主升起点；
      · wave1_top    = origin 之后**第一个主级 H** ＝「1 浪顶」⇒ **4 浪底的理论失效线**（触发降级）；
      · wave1_top_minor = 同起点之后第一个**细级** H（降一级的 1 浪顶，只作预警，不触发）；
      · prior_peak   = 最近一个主级 H（＝主级前高）；prev_peak = 再前一个主级 H（次级参考位）。
    实测（000001.SH，as-of 2026-10-14）：origin 2704.09(2024-09-13) → 主级 H 3674.40(2024-10-08)、
      prior_peak 4242.57(2026-05-13)、prev_peak 3674.40、wave1_top_minor 4029.50(2025-11-13)；
      收盘 3767.23 **仍在 3674.40 之上** ⇒ 铁律② 未破（他 10-15 的降级来自**跳空**那条规则 ✓）。"""
    z = pivots(rows, asof)
    out: Dict[str, Any] = {"origin": None, "origin_date": "", "wave1_top": None, "wave1_top_date": "",
                           "wave1_top_minor": None, "wave1_top_minor_date": "", "prior_peak": None,
                           "prior_peak_date": "", "prev_peak": None, "prev_peak_date": "", "pivots": len(z)}
    hs = [p for p in z if p[3] == "H"]
    if hs:
        out["prior_peak"], out["prior_peak_date"] = hs[-1][2], hs[-1][1]
        if len(hs) > 1:
            out["prev_peak"], out["prev_peak_date"] = hs[-2][2], hs[-2][1]
    hist = [r for r in rows if r[0] <= str(asof)[:8]]
    lo_date = hist[-ORIGIN_BARS][0] if len(hist) > ORIGIN_BARS else ""
    ls = [p for p in z if p[3] == "L" and (not lo_date or p[1] >= lo_date)]
    if ls:
        o = min(ls, key=lambda p: p[2])
        out["origin"], out["origin_date"] = o[2], o[1]
        after = [p for p in z if p[3] == "H" and p[1] > o[1]]
        if after:
            out["wave1_top"], out["wave1_top_date"] = after[0][2], after[0][1]
        afterf = [p for p in pivots(rows, asof, PCT_FINE) if p[3] == "H" and p[1] > o[1]]
        if afterf:
            out["wave1_top_minor"], out["wave1_top_minor_date"] = afterf[0][2], afterf[0][1]
    return out


# ─────────────────────────── 特征 ───────────────────────────
def features(rows: Sequence[Tuple[str, float, float, float]], asof: str) -> Optional[Dict[str, Any]]:
    """as-of 日的硬条件快照（**只用 ≤ asof 的数据** ✓）。"""
    hist = [r for r in rows if r[0] <= str(asof)[:8]]
    if len(hist) < MIN_BARS:
        return None
    d, close, high, amount = hist[-1]
    prev_close = hist[-2][1]
    win = hist[-(HI_WIN + 1):-1]                     # 前 60 根（不含当日）
    hi60_prev = max(r[1] for r in win)
    lo60_prev = min(r[1] for r in win)
    w = hist[-RALLY_WIN:]
    lo20 = min(r[1] for r in w)
    hi20 = max(r[1] for r in w)
    amt_prev = hist[-2][3]
    st = structural_levels(rows, asof)
    sub = sub_legs(rows, asof, st)
    global _SYNC3
    _SYNC3 = sync3(asof)          # None=数据不全 ⇒ 退回单指数口径（features 里可查 ✓）
    w1 = _env_pin_w1() or st["wave1_top"]
    _vr20 = (sum(r[3] for r in hist[-21:-1]) / 20) if len(hist) >= 21 and sum(r[3] for r in hist[-21:-1]) else 0.0
    # 注意：st["pivots"] 是**拐点个数**（int ✗），形态判定要用拐点列表 ⇒ 直接调 pivots()（同一因果函数 ✓）
    _pv = pivots(rows, asof)
    _since_wb = hist[-WB_WIN][0] if len(hist) >= WB_WIN else ""
    _wb_marks = w_bottom_marks(_pv, close, (amount / _vr20) if _vr20 else 0.0, _since_wb)
    _wb_ok, _wb_neck = bool(_wb_marks and _wb_marks["confirmed"]), (_wb_marks or {}).get("neck", 0.0)
    _dt_marks = double_top_marks(_pv, max(r[1] for r in hist[-21:-1]) if len(hist) >= 21 else 0.0,
                                hist[-DT_WIN][0] if len(hist) >= DT_WIN else "")
    _marks = None
    if _wb_ok and _wb_marks:
        _marks = {"kind": "w_bottom", "neck": _wb_marks["neck"], "confirmed": True,
                  "points": [{"date": _wb_marks["l1"]["date"], "value": _wb_marks["l1"]["value"]},
                             {"date": _wb_marks["l2"]["date"], "value": _wb_marks["l2"]["value"]}]}
    elif _dt_marks and _dt_marks["confirmed"]:
        _marks = {"kind": "dtop", "neck": _dt_marks["neck"], "confirmed": True,
                  "points": [{"date": _dt_marks["h1"]["date"], "value": _dt_marks["h1"]["value"]},
                             {"date": _dt_marks["h2"]["date"], "value": _dt_marks["h2"]["value"]}]}
    return {
        "date": d, "close": close, "high": high, "prev_close": prev_close, "amount": amount,
        "wave1_top": w1, "wave1_top_date": st["wave1_top_date"], "wave1_top_minor": st["wave1_top_minor"],
        "prior_peak": st["prior_peak"], "prior_peak_date": st["prior_peak_date"],
        "prev_peak": st["prev_peak"], "origin": st["origin"],
        "origin_date": st["origin_date"], "pivots": st["pivots"],
        "sub_stage": sub["stage"], "sub_forming": sub["forming"], "sub_legs": sub["legs"],
        # ── 主升叠加档的量价特征（3-3/3-2 判据）──
        "hi20_prev": max(r[1] for r in w), "streak_above_hi20": _streak_above(hist, RALLY_WIN),
        # ★ 不含当日的 20 日最高（"突破"类比较必须用它：hi20 含当日 ⇒ close > hi20 恒假 ✗ 实测踩过）
        "hi20_excl": max(r[1] for r in hist[-21:-1]) if len(hist) >= 21 else 0.0,
        "vol_ratio20": (round(amount / (sum(r[3] for r in hist[-21:-1]) / 20), 2)
                        if len(hist) >= 21 and sum(r[3] for r in hist[-21:-1]) else 0.0),
        "sync3": _SYNC3,          # True 三指数同步创 20 日新高 / False 不同步 / None 数据不全（退回单指数）
        "main_up_up": bool(_streak_above(hist, RALLY_WIN) >= MAIN_UP_STREAK
                           and (sum(r[3] for r in hist[-21:-1]) / 20) and amount >= MAIN_UP_VOL * (sum(r[3] for r in hist[-21:-1]) / 20)
                           and (_SYNC3 is not False)),
        "main_up_pb": bool(_up33_in_window(hist, HI_WIN)
                           and hi20 and close <= hi20 * (1 - MAIN_UP_PULLBACK) and not bool(close < lo60_prev)),
        # ★ 2026-10-15 修：**首次下穿**才算事件。原来写成「close < 1浪顶」这种**状态** ⇒ 指数一旦
        #   站到 1浪顶之下，档位就被钉死在主跌1浪（实测：2019-05~2020-07 共 285 天，期间指数还在涨；
        #   2022-07~2024-02 共 391 天）—— 与之前修过的「MA60 状态」「20 日内新破」同一类错误 ✗。
        "break_wave1": bool(w1 and close < w1 and prev_close >= w1),
        "hi60_prev": hi60_prev, "lo60_prev": lo60_prev, "hi20": hi20, "lo20": lo20,
        "ma60": round(sum(r[1] for r in hist[-61:-1]) / 60, 2) if len(hist) >= 61 else 0.0,
        "below_ma60": bool(len(hist) >= 61 and close < sum(r[1] for r in hist[-61:-1]) / 60),
        "new_high60": bool(close > hi60_prev),   # 严格突破：等于前高不算“创新高” ✓
        "break_low60": bool(close < lo60_prev),
        "gap_pct": round((prev_close - high) / prev_close, 4) if prev_close else 0.0,
        "gap_unrecovered": bool((prev_close - high) / prev_close >= GAP_MIN and close < prev_close),
        "gap_break": bool((prev_close - high) / prev_close >= GAP_MIN and close < prev_close
                          and len(hist) >= 61 and close < sum(r[1] for r in hist[-61:-1]) / 60),
        "break_floor": bool(floor_level() and close < floor_level()),
        "break_low20": bool(close < min(r[1] for r in hist[-21:-1])),
        "ma20": round(sum(r[1] for r in w) / len(w), 2) if w else 0.0,
        "above_ma20": bool(w and close > sum(r[1] for r in w) / len(w)),
        "low_age": int(len(w) - 1 - min(range(len(w)), key=lambda i: w[i][1])) if w else 0,
        "off_low20": round(close / lo20 - 1, 4) if lo20 else 0.0,
        "bottom_ok": bool(w and len(w) >= 20
                          and (len(w) - 1 - min(range(len(w)), key=lambda i: w[i][1])) >= LOW_AGE
                          and (close / lo20 - 1) >= OFF_LOW
                          and close > sum(r[1] for r in w) / len(w)),
        "gap_break20": bool((prev_close - high) / prev_close >= GAP_MIN and close < prev_close
                            and close < min(r[1] for r in hist[-21:-1])),
        "rally_amp": round(hi20 / lo20 - 1, 4) if lo20 else 0.0,
        "rally_new_high": bool(hi20 > hi60_prev),
        "hi20_ratio": round(hi20 / hi60_prev, 4) if hi60_prev else 0.0,
        "near_prev_high": bool(hi60_prev and hi20 >= NEAR_MIN * hi60_prev),
        "fade": round(1 - close / hi20, 4) if hi20 else 0.0,
        "shrink": bool(amt_prev and amount < amt_prev),
        # ── 补档 B 的量价特征 ──
        "lo60_age": (len(hist[-HI_WIN:]) - 1 - min(range(len(hist[-HI_WIN:])), key=lambda i: hist[-HI_WIN:][i][1])
                     if len(hist) >= HI_WIN else 0),
        "off_lo60": round(close / lo60_prev - 1, 4) if lo60_prev else 0.0,
        "vol_ratio60": (round((sum(r[3] for r in hist[-21:-1]) / 20) / (sum(r[3] for r in hist[-61:-1]) / 60), 2)
                        if len(hist) >= 61 and sum(r[3] for r in hist[-61:-1]) else 0.0),
        "basis_43": bool(len(w) >= 20
                         and (len(w) - 1 - min(range(len(w)), key=lambda i: w[i][1])) >= LOW_AGE
                         and lo20 and (close / lo20 - 1) <= BASE43_OFF
                         and close > lo20
                         and (not bool(close > hi60_prev))
                         and (sum(r[3] for r in hist[-21:-1]) / 20) <= BASE43_VOL * (sum(r[3] for r in hist[-61:-1]) / 60)
                         if len(hist) >= 61 and sum(r[3] for r in hist[-61:-1]) else False),
        "w_bottom": _wb_ok,
        "w_bottom_neck": _wb_neck,
        "marks": _marks,          # 图上标注用（W底/双头的两个端点 + 颈线 ✓，无形态则 None）
        # ── 补档 C 的顶部/下跌族特征 ──
        "shrink_high": bool(close > (hist[-21:-1] and max(r[1] for r in hist[-21:-1])) and _vr20
                            and amount <= TOP_SHRINK * _vr20),
        "upper_shadow": bool(high >= (hist[-21:-1] and max(r[1] for r in hist[-21:-1])) and close
                             and (high - close) / close >= TOP_SHADOW),
        "stall": bool(len(w) >= 6 and close >= w[-1][1] * (1 - 0.02) and (close / w[-6][1] - 1) <= TOP_STALL),
        "top_zone": bool(close > hi60_prev * 0.97),      # 在高位区间（距 60 日前高 3% 内）才谈顶部
        "double_top": _double_top(_pv, max(r[1] for r in hist[-21:-1]) if len(hist) >= 21 else hi20,
                                 hist[-DT_WIN][0] if len(hist) >= DT_WIN else ""),
        "b_rebound": bool(lo20 and (close / lo20 - 1) >= B_REBOUND_MIN and close < hi20 and close < hi60_prev),
        "abc_done": bool(sum(1 for p in _pv[-4:] if str(p[3]).upper() == "L") >= 2 and close < hi60_prev),
        "basis_41": bool((close / hi60_prev - 1) >= -BASE41_OFF and len(w) >= 6
                         and (close / w[-6][1] - 1) <= -0.02 and not bool(close > hi60_prev)),
        "sharp_drop": bool(prev_close and (close / prev_close - 1) <= -SHARP_DROP and _vr20 and amount >= SHARP_VOL * _vr20),
    }


def stage_of(f: Dict[str, Any]) -> int:
    """当日判定档（只看当日硬条件，不带上一次）→ 0..3。

    rung3（主跌1浪）**只认结构性破位**：收盘 < 主升 1 浪顶（铁律②）∨ **跳空且破 20 日低位** ∨ 关键位；
    单日跳空**只降一档**到 rung2（反抽结束 ⇒ 防守）——2026-08-19 实测教训：一跳空直接判主跌1浪，
    会把 9~10 月（他在「调整/反抽」✓）误判两个月 ✗；60 日新低退为参考量（`WOLF_WAVE_LADDER_LOW60=1` 才参与）。"""
    if (f["break_wave1"] or f["break_floor"] or f["gap_break20"]
            or (LOW60_TRIGGER and f["break_low60"])):
        return RUNG_INDEX["down1"]
    # ★ 2026-10-15 收紧：失败4-5 必须**真把结构跌坏**（回落时取掉了前 20 日最低），
    #   否则区间里任何一次 3% 回踩都会被贴成「失败5/defense」（实测：旧口径下 rung2 当日
    #   的「近20日高/前60日高」有 76% ≥0.99 ⇒ “摸前高”这条几乎恒真，等于没有过滤 ✗）。
    if (f["gap_unrecovered"]
            or ((not f["rally_new_high"]) and f["near_prev_high"]
                and f["rally_amp"] >= RALLY_MIN and f["fade"] >= FADE_MIN and f["break_low20"])):
        return RUNG_INDEX["w45_fail"]
    if f["new_high60"] or f["bottom_ok"]:
        return RUNG_INDEX["w45_ok"]
    return RUNG_INDEX["w4_adj"]


def fixture_sharp(fi: Dict[str, Any]) -> bool:
    """放量急杀当日（C杀 的标记口径 ✓）。"""
    return bool((fi or {}).get("sharp_drop"))


def _reasons(f: Dict[str, Any], idx: int) -> str:
    hits = []
    if f["gap_unrecovered"]:
        hits.append("跳空未收回(高%s<前收%s)" % (round(f["high"], 2), round(f["prev_close"], 2)))
    if f["break_wave1"]:
        hits.append("收盘%s < 主升1浪顶%s（铁律②失效，起点%s）" % (
            round(f["close"], 2), round(f["wave1_top"], 2), f["origin_date"] or "-"))
    if LOW60_TRIGGER and f["break_low60"]:
        hits.append("收盘%s破前60日低位%s" % (round(f["close"], 2), round(f["lo60_prev"], 2)))
    if f["break_floor"]:
        hits.append("跌破关键位%s" % floor_level())
    if f.get("w_bottom"):
        hits.append("W底确认（放量%s倍收上颈线%s ⇒ 底部反转，原表 build）" % (
            f.get("vol_ratio20"), round(f.get("w_bottom_neck") or 0, 2)))
    elif idx >= RUNG_INDEX["w45_fail"] and f.get("b_rebound"):
        hits.append("下跌结构中的反弹（B反：典型诱多别抄底 ⇒ 只降不抄底，原表 B反=t_only 但本档 defense 更严 ✓）")
    elif f.get("basis_43"):
        hits.append("4-3 筑底（低点冻结%d日 ∧ 距低%+.1f%% ∧ 缩量%.2f ⇒ 可分批接主线，原表 side）" % (
            f.get("low_age", 0), 100 * (f.get("off_low20") or 0), f.get("vol_ratio60") or 0))
    if hits:
        return "；".join(hits)
    if idx == RUNG_INDEX["w45_fail"]:
        return "近20日摸到前高%s的%s%%后回落%s%%（未创新高）" % (
            round(f["hi60_prev"], 2), round(f["hi20_ratio"] * 100, 1), round(f["fade"] * 100, 1))
    sub = ("（%s%s 段）" % (f.get("sub_stage") or "-", "→" + f["sub_forming"] if f.get("sub_forming") else ""))
    if idx == RUNG_INDEX["w45_ok"]:
        if f["new_high60"]:
            return "收盘%s创60日新高(前高%s)" % (round(f["close"], 2), round(f["hi60_prev"], 2))
        return "止跌确认：20日低点%s 冻结%d日 ∧ 距低+%s%% ∧ 站上MA20(%s)%s" % (
            round(f["lo20"], 2), f["low_age"], round(f["off_low20"] * 100, 1), round(f["ma20"], 2), sub)
    return "无破位无新高（前高%s，近20日最高%s=%s%%）⇒ 仍在调整区间%s" % (
        round(f["hi60_prev"], 2), round(f["hi20"], 2), round(f["hi20_ratio"] * 100, 1), sub)


# ─────────────────────────── 判定 ───────────────────────────
def evaluate(rows: Sequence[Tuple[str, float, float, float]], asof: str,
             prev: Optional[int] = None, latch_start: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """as-of 判定 + **单向锁**（只降不升）：`rung = max(prev, 当日档)` ✓。

    释放（**重定浪型**，两条硬条件，都对应他本人行为）：
      ① 当日创 60 日新高 —— 结构转上；
      ② 自锁账起点（`latch_start`）以来低点反抽 ≥ `RESET_RALLY` —— 空头结构被破坏。
    **没有这两条，一次破位会把档位永久锁死在主跌1浪** ✗（实测：2026-06-08 一跳空锁死后，
    7/31~10/14 全部误报主跌1浪，而他 8/05 在「按 4 接 5 做」、10/09 在吃反抽 ✓）。
    """
    f = features(rows, asof)
    if f is None:
        return None
    raw = stage_of(f)
    reset, why = False, ""
    wb = bool(f.get("w_bottom"))
    if wb:
        # 补档 B：W底确认（放量过颈线）= **底部结构事件** ⇒ 重定浪（推翻下跌结构 ✓，他 10-15 原话口径 ✓）
        reset, why = True, "W底确认（放量过颈线%s）⇒ 重定浪型" % (round(f.get("w_bottom_neck") or 0, 2))
    elif prev is not None and int(prev) > raw:
        if f["new_high60"]:
            reset, why = True, "创60日新高 ⇒ 重定浪型"
        elif latch_start:
            seg = [r[1] for r in rows if latch_start <= r[0] <= str(asof)[:8]]
            hi, lo = (max(seg), min(seg)) if seg else (0.0, 0.0)
            if lo and hi / lo - 1 >= RESET_RALLY:
                reset, why = True, "自低点反抽%.1f%% ⇒ 重定浪型" % ((hi / lo - 1) * 100)
    idx = raw if (reset or prev is None) else max(int(prev), raw)
    # 补档 B 的档位改写：W底 ⇒ d1/W底/build（已重定浪 ✓）；4-3 筑底 ⇒ d4/4-3/side（**仅 4 浪调整档内** ✓）
    ov = None
    _top_hits = sum(1 for k in ("shrink_high", "upper_shadow", "stall", "double_top") if f.get(k))
    if wb:
        ov = W_BOTTOM                                        # 底部确认（最高优先 ✓）
    elif idx <= RUNG_INDEX["w45_ok"] and f.get("top_zone") and _top_hits >= 2:
        ov = DOUBLE_TOP if f.get("double_top") else TOP_SELL  # 防顶：d5/双头 ⇒ exit（原表 ✓）
    elif idx == RUNG_INDEX["w4_adj"] and f.get("basis_41"):
        ov = BASE41                                          # 4-1 回调初 ⇒ defense
    elif idx == RUNG_INDEX["w4_adj"] and f.get("basis_43"):
        ov = BASE_43                                         # 4-3 筑底 ⇒ side
    # ★ 修正（同日实测）：B反 / ABC **不改 operation** ✗ ——
    #   原表给 B反/ABC 的是 `t_only`（**板块/个股场合**「不参与、等结束」），而我们的 `down1` 已经是
    #   `defense ＋ mode=自救`（原表 down=空仓防御 ＋ 他 2025-09「熊市不做」）⇒ 拿 t_only 去覆盖它是**放松** ✗
    #   （t_only 在 P3 会给 probe ≤3%，defense 给 None ✗）。故 B反/ABC 只作**标签**（`down_tag`，进 reasons ✓），
    #   档位与 operation 保持 down1 自身的口径 ✓。
    # ⛔「下跌浪4反弹」（原表 = exit 先卖）没有单独实现：它与 B反 的差别是"第几段下跌"（需子级计数 ✗）⇒ 留缺口 ✓。
    r = RUNGS[idx]
    w1, w2, w3, invest = _ALLOC.get(r["operation"], (0.6, 0.25, 0.15, 1.0))
    return {"date": f["date"], "rung_index": idx, "key": r["key"], "name": r["name"],
            "expect": r["expect"], "operation": r["operation"], "invest": invest,
            "weights": [w1, w2, w3], "day_stage": raw, "reset": reset, "reset_reason": why,
            "degraded": bool(prev is not None and idx > prev and not reset),
            # ★ 主升叠加档（2026-10-15 补 B，实测后**只保留 3-3**）：
            #   · 判据基于**上锁后的 rung**（不是当日档！首版用 raw ⇒ 10-09 那种"上锁 defense"的日子被放宽成 side ✗）；
            #   · **3-2 不做接管**：它跟 4 浪调整在价格上**不可区分**（2026-07-31 他叫"调整浪/4浪"，按 3-2 就会给 side ✗）
            #     —— 这是"等级不可分辨"问题的又一次实证，只能留作信息字段（`main_up_pb`）⛔。
            "ov_spec": ov,
            # 只留 B反 标签：ABC 的判据太宽（实测 1614 天几乎覆盖整段下跌 ⇒ 无信息量 ✗ 去掉）；
            # 下跌浪4反弹 需子级计数 ✗ 未实现（留缺口 ✓）。
            "down_tag": ("B反" if (idx >= RUNG_INDEX["w45_fail"] and f.get("b_rebound")) else ""),
            "main_up": ("3-3" if (idx <= RUNG_INDEX["w45_ok"] and f.get("main_up_up")) else ""),
            "main_up_pb_info": bool(f.get("main_up_pb")),
            "main_up_spec": (MAIN_UP if (idx <= RUNG_INDEX["w45_ok"] and f.get("main_up_up")) else None),
            "ov_expect": (ov or {}).get("expect"),
            "reasons": _reasons(f, idx), "features": f,
            "sub_stage": ("%s%s" % (f.get("sub_stage") or "", ("→" + f["sub_forming"]) if f.get("sub_forming") else "")),
            "levels": {"wave1_top": f["wave1_top"], "wave1_top_date": f["wave1_top_date"],
                       "wave1_top_minor": f["wave1_top_minor"], "prior_peak": f["prior_peak"],
                       "prev_peak": f["prev_peak"], "origin": f["origin"]}}


def walk(rows: Sequence[Tuple[str, float, float, float]], start: Optional[str] = None,
         end: Optional[str] = None, prev: Optional[int] = None) -> List[Dict[str, Any]]:
    """逐日回放（as-of 逐日推进，带单向锁 + 重定浪）→ 每次**降档/重定浪**都记一条 ✓。"""
    out: List[Dict[str, Any]] = []
    cur: Optional[int] = prev
    latch: Optional[str] = None
    for r in rows:
        d = r[0]
        if start and d < start:
            continue
        if end and d > end:
            break
        ev = evaluate(rows, d, cur, latch)
        if ev is None:
            continue
        if ev["reset"]:
            latch = None
        if ev["rung_index"] > 0 and (cur is None or ev["rung_index"] > cur):
            latch = d
        cur = ev["rung_index"]
        out.append(ev)
    return out


# ─────────────── 影子 / 接管（生产消费点的**唯一**入口） ───────────────
ADOPT_CACHE: Dict[str, Any] = {}


def ladder_operation(asof: Optional[str] = None) -> Optional[str]:
    """「若按浪型假设链走，今天的 operation 是什么」——**按需现算**（不依赖 artifact 是否新鲜），
    结果按 as-of 缓存；无数据/异常 ⇒ None（fail-open ✓）。"""
    key = (str(asof)[:8] if asof else "latest")
    if key in ADOPT_CACHE:
        return ADOPT_CACHE[key]
    op: Optional[str] = None
    try:
        rows = load_rows()
        if rows:
            ev = evaluate(rows, str(asof)[:8] if asof else rows[-1][0])
            op = (ev or {}).get("operation")
    except Exception as _e:
        _silent_alert("wave_ladder.py:ladder_operation", _e)
    ADOPT_CACHE[key] = op
    return op


def shadow_log(prod_op: Optional[str], lop: Optional[str], note: str = "") -> None:
    """影子双跑留痕 → `logs/wave_ladder_ab.jsonl`（同一 (日, 两个 op) 只记一次；**绝不抛** ✓）。"""
    try:
        d = time.strftime("%Y%m%d")
        key = "%s|%s|%s" % (d, prod_op, lop)
        if ADOPT_CACHE.get("logged") == key:
            return
        ADOPT_CACHE["logged"] = key
        for base in (os.environ.get("LOG_DIR", "logs"), "logs", DATA, "."):
            try:
                os.makedirs(base, exist_ok=True)
                with open(os.path.join(base, "wave_ladder_ab.jsonl"), "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "date": d,
                                         "prod_op": prod_op, "ladder_op": lop,
                                         "adopted": lop if enabled() else prod_op,
                                         "switch": "on" if enabled() else "shadow", "note": note},
                                        ensure_ascii=False) + chr(10))
                return
            except OSError as _e:
                _silent_alert("wave_ladder.py:shadow_log", _e)
    except Exception as _e:
        _silent_alert("wave_ladder.py:shadow_log_outer", _e)


def adopt_operation(prod_op: Optional[str], asof: Optional[str] = None) -> Optional[str]:
    """**生产消费点唯一入口**：`WOLF_WAVE_LADDER=0`（默认）⇒ 原样返回生产 op（生产逐字不变 ✓），
    同时把「浪型链会说什么」写进影子日志；`=1` 且算得出 ⇒ 由浪型链接管；算不出 ⇒ 回落生产 op ✓。"""
    try:
        lop = ladder_operation(asof)
        shadow_log(prod_op, lop)
        if enabled() and lop:
            return lop
    except Exception as _e:                     # 兜底：入口绝不把异常带给生产 ✓
        _silent_alert("wave_ladder.py:adopt_operation", _e)
    return prod_op


# ─────────────── 写生产 wave_state.json（2026-10-15 起：浪型链＝唯一写入方） ───────────────
# level 映射**必须**对齐既有消费方语义：wave_gate.py:14 / t_monitor.py:2365 / wolf_context.py:623
# 都以 `wave_state.level == "down"` 作为**指数大级别止损**判据（他 2026-08-27「转下跌1浪就止损」）
# ⇒ rung3 必须映射成 "down" ✓；取值域 d1..d5/down、sub_level 取值域 3-x/4-x/失败5/ABC/B反/C杀/…
LEVEL_OF_RUNG = {"w4_adj": "d4", "w45_ok": "d5", "w45_fail": "d5", "down1": "down"}
# ★ 2026-10-15 语义修正：`down1` 不再一律标 C杀 ✗ —— C杀 是**下跌的最后急杀段**，只有放量急杀当日才标；
#   其余标「下跌1浪」（原来把整段下跌都叫 C杀，与他的用法不符 ✗）。
SUB_OF_RUNG = {"w4_adj": "", "w45_ok": "4-5", "w45_fail": "失败5", "down1": "下跌1浪"}
CONF_OF_RUNG = {"w4_adj": 0.6, "w45_ok": 0.7, "w45_fail": 0.7, "down1": 0.8}


def to_wave_state(ev: Optional[Dict[str, Any]], asof: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """浪型链判定 → **既有 schema** 的 wave_state（消费方一行都不用改 ✓）。

    保留原字段 level/sub_level/operation/confidence/reasons/date/features（与旧 LLM 版同名同义），
    另加 source/rung/expect/levels/sub_legs 供复盘与后续扩展；取不到 ev ⇒ None（fail-open）✓。
    """
    if not ev:
        return None
    key = ev.get("key") or ""
    fi = ev.get("features") or {}
    mus = ev.get("ov_spec") or ev.get("main_up_spec") or {}   # 档位改写：W底/4-3 优先，其次主升 3-3 ✓
    sub = SUB_OF_RUNG.get(key, "")
    if key == "down1" and fixture_sharp(fi):        # 放量急杀当日 ⇒ C杀 ✓（他的用法）
        sub = "C杀"
    if key == "w4_adj" and fi.get("sub_stage"):
        sub = fi["sub_stage"] + (("→" + fi["sub_forming"]) if fi.get("sub_forming") else "")
    d = str(ev.get("date") or asof or "")[:8]
    return {
        "date": ("%s-%s-%s" % (d[:4], d[4:6], d[6:8])) if len(d) == 8 else str(ev.get("date") or ""),
        "as_of": d,
        "level": mus.get("level") or LEVEL_OF_RUNG.get(key, "d4"),
        "sub_level": (mus.get("sub_level") or sub) if mus else sub,
        "operation": mus.get("operation") or ev.get("operation"),
        "confidence": CONF_OF_RUNG.get(key, 0.5),
        "reasons": ("【浪型链 rung%d/%s】%s；预期：%s；操作：%s（invest %.2f）" % (
            int(ev.get("rung_index", -1)), ev.get("name") or "?", ev.get("reasons") or "",
            ev.get("expect") or "", ev.get("operation") or "", float(ev.get("invest") or 0))),
        "features": fi,
        "mode": MODE_OF_RUNG.get(key, ""),
        "mode_note": MODE_NOTE.get(MODE_OF_RUNG.get(key, ""), ""),
        "family": _family_of(mus.get("level") or LEVEL_OF_RUNG.get(key, "d4"), sub, key),
        "stage": _stage_of(mus.get("level") or LEVEL_OF_RUNG.get(key, "d4"), sub, key, mus),
        "status": _status_of(key, mus.get("operation") or ev.get("operation"), sub),
        "label_note": _label_note_of(key, sub),
        # ★ 2026-10-20：所有产物（live/dated/history）都带 session_date ✓（= 这份判定服务哪个交易日）
        "session_date": next_session_date(str(ev.get("date") or "")[:8]),
        "source": "wave_ladder",
        "rung": {"key": key, "name": ev.get("name"), "index": ev.get("rung_index"),
                 "expect": ev.get("expect"), "degraded": ev.get("degraded"), "reset": ev.get("reset")},
        "levels": ev.get("levels") or {},
        "sub_legs": fi.get("sub_legs") or [],
    }


def enrich_wave_state(ws: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """给已落盘的旧 wave_state 补 family/stage/status/label_note（只加字段，不改旧值）。

    用于读时兼容：旧文件没有 rung 字段时，按 level+sub_level 反推。
    """
    if not isinstance(ws, dict):
        return ws
    out = dict(ws)
    key = str(((ws.get("rung") or {}).get("key") or ""))
    level = str(ws.get("level") or "")
    sub = str(ws.get("sub_level") or "")
    op = str(ws.get("operation") or "")
    out.setdefault("family", _family_of(level, sub, key))
    out.setdefault("stage", _stage_of(level, sub, key, None))
    out.setdefault("status", _status_of(key, op, sub))
    if not out.get("mode"):
        out["mode"] = MODE_OF_RUNG.get(key, "")
    if not out.get("label_note"):
        out["label_note"] = _label_note_of(key, sub)
    return out


def next_session_date(d8: str) -> str:
    """`d8`（数据日 YYYYMMDD）之后的**下一个交易日**（只跳周末；节假日会偏一天，等下一根 K 线出现后自然对齐 ✓）。"""
    import datetime as _dt
    try:
        d = _dt.date(int(d8[:4]), int(d8[4:6]), int(d8[6:8]))
    except Exception:
        return ""
    for _ in range(10):
        d = d + _dt.timedelta(days=1)
        if d.weekday() < 5:
            return d.strftime("%Y%m%d")
    return ""


def write_wave_state(asof: Optional[str] = None, dated: bool = False,
                     rows: Optional[Sequence[Tuple[str, float, float, float]]] = None,
                     ev: Optional[Dict[str, Any]] = None,
                     session_date: Optional[str] = None) -> Optional[str]:
    """写 `data/wave_state.json`（可选带日期副本）→ 路径；失败返回 None ✓。

    传 ev（来自 walk，含单向锁）时按它写；否则用 `evaluate()` 现算（无降级锁，实时口径）。
    """
    if ev is None:
        # ★ 2026-10-21 修（二次踩坑 ✗）：原来这里走 `evaluate()`（**单日、无锁** ✗）⇒
        #   任何"不带 ev 的调用方"都会把上锁的档位写成未上锁的 ✗（实测：直接调本函数一次，
        #   生产 wave_state 就从 defense 变成了 t_only ✗）。改为**带锁回放 walk** ✓ 取末档 ✓。
        rows = rows or load_rows()
        if not rows:
            return None
        _asof8 = str(asof)[:8] if asof else ""
        if _asof8:
            rows = [r for r in rows if r[0] <= _asof8] or rows
        _evs = walk(rows)
        if not _evs:
            return None
        ev = _evs[-1]
    ws = to_wave_state(ev, asof)
    if not ws:
        return None
    # ★ 2026-10-20：新增 session_date（这份判定**服务于哪个交易日**）——
    #   文件是按"数据日期"命名的（用上一交易日收盘判 ✓），容易让人以为"当天没生成" ✗（用户 10-20 反馈 ✓）。
    #   实时写入（08:10）⇒ 就是**运行当天**（最准确 ✓）；回填/历史 ⇒ 取数据日的下一个工作日（近似 ✓）。
    ws["session_date"] = session_date or next_session_date(ws["as_of"])
    d = ws["as_of"]
    try:
        os.makedirs(DATA, exist_ok=True)
        p = os.path.join(DATA, "wave_state.json")
        json.dump(ws, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(p + ".tmp", p)
        if dated:
            pd = os.path.join(DATA, "wave_state_%s-%s-%s.json" % (d[:4], d[4:6], d[6:8]))
            json.dump(ws, open(pd + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(pd + ".tmp", pd)
        # ★ 2026-10-21（用户**第二次**困惑「10-21 怎么没有浪型记录」）：再写一份按**服务交易日**命名的副本 ✓
        #   根因：上面的 dated 件按「数据日期」命名 ✗（10-21 早上写出的档叫 wave_state_2026-10-20.json），
        #        用户按"今天"找会以为没更新 ✗。这份副本**内容完全相同**，只是文件名用 session_date ✓，
        #        两者可互为校验（同一天应当一致 ✓）。**不动 dated 命名**（回测 sim8 依赖它 ✗）。
        _sd = str(ws.get("session_date") or "")
        if _sd and len(_sd) >= 8:
            ps = os.path.join(DATA, "wave_state_session_%s-%s-%s.json" % (_sd[:4], _sd[4:6], _sd[6:8]))
            json.dump(ws, open(ps + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(ps + ".tmp", ps)
        # ★ 2026-10-21：**双写 PostgreSQL**（步 1/2 ✓ —— 文件仍是权威 ✓ 读侧一行未改 ✓ 生产零行为变化 ✓）
        #   幂等（session_date 主键 ✓）；任何 DB 异常都只告警 ✓ 绝不影响文件写入 ✓（见 wave_pg 的纪律 ✓）
        try:
            from wave_pg import upsert as _pg_upsert
            if not _pg_upsert(ws):
                print("[wave_ladder] 写库未成功（忽略 ✓ 文件已写）", flush=True)
        except Exception as _e_pg:
            print("[wave_ladder] 写库异常（忽略 ✓ 文件已写）: %s" % str(_e_pg)[:120], flush=True)
        return p
    except Exception as _e:
        _silent_alert("wave_ladder.py:write_wave_state", _e)
        return None


def read_ladder(state_file: str = "wave_ladder.json") -> Optional[Dict[str, Any]]:
    """读影子产物（供上层/复盘）。取不到返回 None（fail-open ✓）。"""
    for cand in (os.path.join(DATA, state_file), os.path.join("data", state_file)):
        try:
            if os.path.exists(cand):
                return json.load(open(cand, encoding="utf-8"))
        except Exception as _e:
            _silent_alert("wave_ladder.py:read", _e)
    return None


def write_artifact(ev: Optional[Dict[str, Any]], state_file: str = "wave_ladder.json") -> bool:
    """落盘 `wave_ladder.json`（影子档：**只在 WOLF_WAVE_LADDER=1 时才可真影响交易** ✓）。"""
    if not ev:
        return False
    try:
        os.makedirs(DATA, exist_ok=True)
        obj = dict(ev)
        obj["switch"] = "on" if enabled() else "shadow"
        obj["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = os.path.join(DATA, state_file + ".tmp")
        json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(DATA, state_file))
        return True
    except Exception as _e:
        _silent_alert("wave_ladder.py:write", _e)
        return False


def operation_for_gate(ev: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """给上层消费的**唯一入口**：开关关着 ⇒ 恒 None（生产逐字不变 ✓）。"""
    if not enabled():
        return None
    ev = ev or read_ladder()
    return (ev or {}).get("operation") or None


if __name__ == "__main__":
    import sys as _sys
    if "--write" in _sys.argv:
        _asof = ""
        if "--asof" in _sys.argv:
            _asof = _sys.argv[_sys.argv.index("--asof") + 1]
        # ★ 2026-10-20 修：原来调 write_wave_state(...) 不带 ev ⇒ 内部走 **evaluate()（单日、无锁）** ✗
        #   而链的核心是"单向降级锁" ⇒ 无锁口径会让上锁的档位被静默解除 ✗
        #   （实证：10-15 上锁「下跌1浪」→ 10-16 被写成「4浪调整/t_only」，期间**没有任何重定浪事件** ✗）。
        #   改为**回放 walk**（带锁 ✓ 与卡片/复盘完全同源 ✓），文件与接口从此不会各说一套 ✓。
        _rows = load_rows()
        if _asof:
            _rows = [r for r in _rows if r[0] <= str(_asof)[:8]] or _rows
        _evs = walk(_rows) if _rows else []
        _ev = _evs[-1] if _evs else None
        _today8 = time.strftime("%Y%m%d")
        _asof8 = ((_ev or {}).get("date") or "")[:8]
        # 实时写入：若今天已经晚于数据日 ⇒ session_date = 今天（精确 ✓）；否则取下一个工作日 ✓
        _sd = _today8 if (_asof8 and _today8 > _asof8) else next_session_date(_asof8)
        _p = write_wave_state(_asof or None, dated=("--dated" in _sys.argv), rows=_rows, ev=_ev, session_date=_sd)
        if _p:
            _ws = json.load(open(_p, encoding="utf-8")) or {}
            print("wave_state 已写:", _p, json.dumps({k: _ws.get(k) for k in ("date", "level", "sub_level", "operation")}, ensure_ascii=False))
        else:
            print("写失败（fail-open）")
        raise SystemExit(0 if _p else 1)
    rows = load_rows()
    print("bars:", len(rows), rows[0][0] if rows else "-", "->", rows[-1][0] if rows else "-")
    anchors = ["2026-07-31", "2026-08-05", "2026-09-01", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]
    cur = None
    for a in anchors:
        ev = evaluate(rows, a.replace("-", ""), cur)
        if ev is None:
            print(a, "-> 无数据(fail-open)")
            continue
        cur = ev["rung_index"]
        print("%s -> [%d]%s | op=%s invest=%s | 当日档=%d | %s" % (
            a, ev["rung_index"], ev["name"], ev["operation"], ev["invest"], ev["day_stage"], ev["reasons"]))
    if rows:
        write_artifact(evaluate(rows, rows[-1][0]))
        print("artifact:", read_ladder() is not None, "| switch:", "on" if enabled() else "shadow")
