# -*- coding: utf-8 -*-
"""底仓 floor 口径（2026-09-16 用户拍板）：一次性认账 rebasing + 只增不减 + 分档 ratio。

## 为什么是这套写法（两条已证否的路径）

· floor 锚「当前持仓 × ratio」（2026-09-07 之前的写法）→ 每卖一次持仓变小、floor 跟着变小
  → T 仓“复活”→ 继续卖，把底仓一点点啃掉（588170 连卖超卖事故，已被那次修复否定）。
· floor 锚「累计未void买入 × ratio」（2026-09-07 至今）→ 卖出不动锚、不会被啃，但**历史卖超的标的**
  floor 会永久大于当前持仓 → 卖腿推导量恒 0（2026-09-16 实测：SH588170 持仓23200/累计买入109000/
  floor54500、SH512480 1300/8000/4000、SZ002409 200/400/200 全部被锁死）。

二者不可兼得：**floor 挂在持仓上就会侵蚀，挂在累计买入上就可能锁死**。本模块的解法：

1. 锚仍是「累计未void买入」（卖出不动锚，保留 2026-09-07 防连卖超卖的原始意图）；
2. 只在 floor 把 T 仓锁死时（floor > 可卖 − 100 股）对该 (account, symbol) 做**一次性认账**：
   rec.base = 可卖 × ratio、rec.cum_at_base = 当时的累计买入；
   此后 floor = base + (累计买入 − cum_at_base) × ratio —— **只随新增买入增长，不随卖出下降**；
3. 持仓归零时写 reset 记录（base=0、cum_at_base=当时的累计买入），避免清仓后重建又被历史累计买入毒住；
4. ratio 按浪型分档（狼大 2026-01-17「主升趋势就75%以上…调整就50% 有风险就30% 下跌趋势就不做」
   + 2026-04-14「50%的底仓 30%左右做日内…剩下20%应对黑天鹅」），开盘前定、盘中不变。

## 开关 / 状态

· `T_BASE_FLOOR_REBASE=0` → 退回旧的「累计买入 × ratio」口径（回滚用）。
· `T_BASE_FLOOR_AUDIT=0` → 不写 t_triggers 审计行。
· `T_BASE_FLOOR_SKIP_NOOP=0` → 关闭 no-op 守卫（2026-09-17 修复前行为：重标结果与旧 floor 相同时
  仍写一条 `base_floor_rebase` 审计行并全量重写状态，占 t_triggers 的 19.2% 纯噪声）。
· 状态文件：`$DATA_DIR/t_base_floor_rebase.json`（原子写；可人工编辑 bucket_override / buckets）。
"""
from __future__ import annotations

import json
import os


def _armdb_root(start: str) -> str:
    """向上找到含 `jobs/arm_db.py` 的仓库根（账本 §9.539：写死层数会数错 ✗）。"""
    p = os.path.dirname(start)
    while p and p != "/" and not os.path.exists(os.path.join(p, "jobs", "arm_db.py")):
        p = os.path.dirname(p)
    return p or os.path.dirname(start)

import tempfile
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from trade_direction import is_buy, is_sell  # noqa: E402 统一方向词表

MIN_LOT = 100                    # 一手；也是"至少留一条腿"的可卖额度
_STATE_NAME = "t_base_floor_rebase.json"
_MAX_EVENTS = 30                 # 每标的保留最近 N 条认账/重置事件（审计）

# ratio 分档（底仓 / 持仓）。主升档取 2/3 = 他的话"底仓 50% / 总仓 75%"（2026-04-14 + 2026-01-17）。
_BUCKET_ENV = {
    "main_up": "T_BASE_KEEP_RATIO_MAIN_UP",
    "range": "T_BASE_KEEP_RATIO",          # 沿用历史默认 0.5
    "high": "T_BASE_KEEP_RATIO_HIGH",
    "exit": "T_BASE_KEEP_RATIO_EXIT",
}
_BUCKET_DEFAULT = {"main_up": 2.0 / 3.0, "range": 0.5, "high": 1.0 / 3.0, "exit": 0.0}
# 浪型 operation → 档（与 position_tier.CORPUS_PROFILE["op_to_total"] 同源：build=主升/t_only·side=调整/
# defense=有风险/exit=下跌）
OP_BUCKET = {"build": "main_up", "t_only": "range", "side": "range",
             "defense": "high", "exit": "exit"}
_STALE_DAYS = int(os.getenv("T_BASE_RATIO_STALE_DAYS", "7"))

# ── 防"锚被高抛腿一路啃低"（2026-09-19 实测修复；库内默认关、回测开）────────────────
# 实测（data/_bt_jan5 0105→0116）：SH603383 建仓 1100 股（cum=1100, ratio=2/3 ⇒ floor 733），
#   被"高抛兑现"卖腿连卖 6 次后 floor 一路重标：733 → 400 → 200 → 100，最终 1100 股**全被卖光**；
#   卖出理由里写着"可卖底仓1100股充足" —— 就是 floor 只剩一手的表现。结果是账户"建仓→高抛清仓→再建仓"，
#   平均仓位只有 13.4%（语料档位在调整期是 50%）。
# 根因：`floor > sel − 100` 时 `base = 可卖 × ratio` 用的是**已经卖小的可卖额** ⇒ 卖出 → 可卖变小 →
#   再次认账把锚压低 → 再卖……构成下调反馈，与该模块 docstring 自己声明的
#   "此后 floor 只随**新增买入**增长，不随卖出下降"相矛盾。
# 修法：**最多认账一次**（无 rec 时才 rebase）。代价：个别标的 T 仓可能被锁死（floor > 可卖），
#   需人工用 bucket_override/buckets 调整 —— 但这正是语料"底仓不动、T 出半"的代价。
# ── 严格锚（2026-09-19 用户拍板 A2）：**完全不做向下认账** ──────────────────────────
# 语义：floor 恒 = 累计买入 × ratio（只有清仓才 reset）；`floor > 可卖 − 100`（T 仓被锁）时
#   **不下调锚**，宁可不做这一笔 T。依据语料「底仓不动、T 出半」+ 2026-09-03「仓位不会低于65%收盘，
#   日内做T仓位20%」。代价：历史上被卖超的标的 T 仓可能长期锁死，只能人工 bucket_override/buckets 调整。
# 与 NO_DOWN_REBASE 的关系：NO_DOWN_REBASE=1 允许**一次**认账（温和）；STRICT_ANCHOR=1 连那一次也不做。
# 开关 WOLF_BASE_FLOOR_STRICT：库内默认关、回测由 pins/驱动打开。
STRICT_ANCHOR = str(os.getenv("WOLF_BASE_FLOOR_STRICT",
                              "0")).strip().lower() in ("1", "true", "yes", "on")

NO_DOWN_REBASE = str(os.getenv("WOLF_BASE_FLOOR_NO_DOWN",
                               "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")


# ────────────────────────────── 基础 ──────────────────────────────

def _enabled() -> bool:
    return os.getenv("T_BASE_FLOOR_REBASE", "1").strip().lower() not in ("0", "false", "no")


def _skip_noop_rebase() -> bool:
    """no-op 守卫开关（2026-09-17 新增，默认开）。

    关闭（`T_BASE_FLOOR_SKIP_NOOP=0`）即回到修复前行为：即使重标结果与旧 floor 相同也写事件。
    """
    return os.getenv("T_BASE_FLOOR_SKIP_NOOP", "1").strip().lower() not in ("0", "false", "no")


def _audit_enabled() -> bool:
    return os.getenv("T_BASE_FLOOR_AUDIT", "1").strip().lower() not in ("0", "false", "no")


def _data_dir() -> str:
    """状态文件目录。按 DATA_DIR → MARCUS_WORKSPACE/data → 逐级向上找已存在的 data/ → 兜底。

    ⚠️ 2026-09-16 部署踩坑：容器里代码挂在 /app/app（= 宿主 backend/app），从
    /app/app/services 向上三级会走到 "/"，于是状态文件被写进**容器内的 /data**（可写层）——
    既不落宿主 bind mount，又让 backend 与 worker 两个容器各写一份、floor 互相不一致。
    故改为"向上逐级探测已存在的 data/"，不再假设固定层级。
    """
    # 跨日状态：**回测**由 `WOLF_STATE_ROOT` 指定运行根 ✓（生产不设 ⇒ 行为不变 ✓；§9.248）
    _sr = os.environ.get("WOLF_STATE_ROOT")
    if _sr:
        return _sr
    d = os.environ.get("DATA_DIR") or ""
    if d:
        return d
    ws = os.environ.get("MARCUS_WORKSPACE") or ""
    if ws:
        cand = os.path.join(ws, "data")
        if os.path.isdir(cand):
            return cand
    # 逐级向上：优先认"仓库根"（同级有 backend/ 或 apps/），否则退回首个存在的 data/
    p = os.path.dirname(os.path.abspath(__file__))
    fallback = None
    for _ in range(6):
        cand = os.path.join(p, "data")
        if os.path.isdir(cand):
            if os.path.isdir(os.path.join(p, "backend")) or os.path.isdir(os.path.join(p, "apps")):
                return cand
            if fallback is None:
                fallback = cand
        nxt = os.path.dirname(p)
        if nxt == p:
            break
        p = nxt
    if fallback:
        return fallback
    return "/app/data" if os.path.isdir("/app/data") else "data"


def _state_path() -> str:
    return os.path.join(_data_dir(), _STATE_NAME)


def _key(account_id: str, symbol: str) -> str:
    return "%s:%s" % (account_id or "stock", symbol or "")


def _lot_floor(x: float, lot: int = MIN_LOT) -> int:
    """向下取整到一手（floor 与可卖额度都保持 100 股整数倍）。"""
    try:
        return int(int(x) // int(lot)) * int(lot)
    except Exception:
        return 0


def load_state() -> Dict[str, Any]:
    p = _state_path()
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
            return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save_state(st: Dict[str, Any]) -> None:
    p = _state_path()
    try:
        d = os.path.dirname(p) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".t_base_floor_", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, p)
    except Exception as e:
        print(f"[t-base-floor] 状态写入失败: {type(e).__name__}: {str(e)[:80]}")

    # 账本 §9.539（用户「切进去」）：写文件之外，**同时落臂库** `arm_state`（单一事实来源、可查、不会被重置切碎）。
    #   为什么不能再只写文件：转正/底仓锚这类**跨日状态**以前只在文件里 ⇒ 路径一变就读不到
    #   （`ambush_promoted.json` 就是这么翻车的）；臂库路径由 `arm_db.arm_root()` 统一推导。
    #   开关 `WOLF_STATE_ARMDB`（默认 1 ＝ 开；置 0 ＝ 回到只写文件）。
    try:
        if str(os.getenv("WOLF_STATE_ARMDB", "1")).strip().lower() in ("1", "true", "yes", "on"):
            import sys as _sysA
            _jobsA = os.path.join(_armdb_root(os.path.abspath(__file__)), "jobs")
            if _jobsA not in _sysA.path:
                _sysA.path.insert(0, _jobsA)
            import arm_db as _adbA
            # 账本 §9.540：**不再默认 drabt35** ✗（否则会把库写进别的臂）；空 ⇒ 留痕跳过 ✓
            _accA = _adbA.resolve_account()
            if not _accA:
                print('[t-base-floor] 臂库跳过：未指定账户（T_MONITOR_ACCOUNT/ARM_ACCOUNT 均空）', flush=True)
                return
            _cA = _adbA.connect(_accA)
            _adbA.put_state(_cA, _accA, _STATE_NAME, st)
            _cA.close()
    except Exception as e:
        # 账本 §9.539：**不再静默**（今天的教训：静默失败最难查）
        print('[t-base-floor] 臂库写入失败（文件已写 ✓）: %s: %s'
              % (type(e).__name__, str(e)[:100]), flush=True)


# ────────────────────────────── ratio 分档 ──────────────────────────────

def ratio_of(bucket: str) -> float:
    """档 → ratio（环境变量优先，未知档按 range）。"""
    b = str(bucket or "").strip().lower() or "range"
    if b not in _BUCKET_DEFAULT:
        b = "range"
    env = _BUCKET_ENV.get(b)
    raw = os.getenv(env) if env else None
    if raw not in (None, ""):
        try:
            return max(float(raw), 0.0)
        except Exception as _e_sil1:
            try:
                from app.services import alert_hub as _ah_sil
                _ah_sil.note_silent("t_base_floor.py:221", _e_sil1)
            except Exception:
                print("[silent:t_base_floor.py:221] %s: %s" % (type(_e_sil1).__name__, str(_e_sil1)[:110]), flush=True)
    return float(_BUCKET_DEFAULT[b])


# ── 底仓保留比例的**结构下限**（`WOLF_BASE_KEEP_MIN`，库内默认 0 = 逐字零变化）──────────
# 病灶（2026-09-22 实测，data/_bt_t5 全窗）：
#   档位把"底仓占比"也一起降了下去 —— `_BUCKET_DEFAULT` 是 main_up 2/3 / range 1/2 /
#   **high 1/3 / exit 0**。3 月中旬那波下跌把浪型打到 defense(`high`)/`exit` ⇒ ratio 掉到 1/3~0
#   ⇒ 「可卖 = 持仓 − floor」几乎等于全仓 ⇒ 高抛腿把底仓一路卖到 0；归零又写 reset
#   （`compute_floor` 的 `kind=reset`）⇒ 账户进入"建仓→高抛清仓→再建仓"循环：
#   **实测 T5 清仓次数 1月 2 次 / 2月 3 次 / 3月 10 次 / 4月 16 次，持仓期中位 8→10→3→3 天，
#   3-12 之后「老仓（买入 ≥4 个交易日）占比」恒为 0%** ⇒ 底仓再也建不起来 ⇒ 反弹段（市场 +9.6%、
#   持仓端 +22.7%）组合只吃到 +5.7%。与"高抛腿把锚 733→400→200→100 啃光整仓"同族。
# 语料（支持"底仓 50% 是结构、不随档位变"）：
#   · 2026-04-14「**50%的底仓 30%左右做日内** 或者一天到两天的T，**剩下20%左右应对…黑天鹅的抄底**」
#   · 2026-09-03「**仓位不会低于65%收盘，日内做T仓位20%**」
#   · 档位那几句（2026-01-17「主升75%以上…调整50…有风险30…下跌不做」）讲的是**总仓**；
#     语料里**没有**"底仓占比随档位降到 1/3 或 0"的说法 ⇒ high/exit 的 1/3、0 属自设。
# 口径：`ratio_eff = max(ratio_of(bucket), WOLF_BASE_KEEP_MIN)`，**只抬不压**；只作用于走本模块的
#   卖腿（止盈/做T类）—— 止损/破位/减仓类走 `t_gateway._stop_exit_volume` 穿透底仓，不受影响
#   （语料允许减仓：「减仓到40%甚至更低」），所以止血动作不会被挡。
# 生产零影响：默认 0 ⇒ `max(r, 0) == r`，逐字等价于改动前。
def _keep_min() -> float:
    """底仓保留比例下限（读环境，便于测试与运行期生效）。非法值 → 0（不动）。"""
    try:
        v = float(os.getenv("WOLF_BASE_KEEP_MIN", "0") or 0)
    except Exception:
        return 0.0
    return min(max(v, 0.0), 1.0)


def apply_keep_min(ratio: float, keep_min: Optional[float] = None) -> float:
    """把档位 ratio 抬到结构下限（纯函数；keep_min 省略时读 `WOLF_BASE_KEEP_MIN`）。"""
    km = _keep_min() if keep_min is None else min(max(float(keep_min or 0.0), 0.0), 1.0)
    try:
        r = float(ratio)
    except Exception:
        r = 0.0
    return max(r, km)


# ── 仓位目标 / 下限（`WOLF_POSITION_FLOOR`，库内默认 0 = 逐字零变化）──────────────────────
# 为什么：语料里**仓位是目标/下限**，而我们所有容量件（`_get_total_cap` 75/50/30、单票 cap、现金保留线 25%）
#   都只有**上限**语义，没有任何"补到目标 / 不低于目标"的表达 ⇒ 实测 T5/T6/T8 **≥65% 天数 0/75**、
#   均值 24.5/15.8/10.7%，与语料「收盘仓位不低于65%」差 40pp 以上。
# 语料：
#   · 2026-09-03「所以我这里**仓位不会低于65%收盘**，日内做T仓位20%」（docs/wolf-daily-log-nga.md:925）
#   · 2026-01-17「**主升趋势就75%以上**…**调整就50%**…**有风险就30%**…**下跌趋势就不做**」
#     （docs/wolf-buy-parameter-ledger.md:46；档位→总仓上限已由 `position_tier.CORPUS_PROFILE` 表达）
#   · 2026-02-05「对啊 **只要当天收盘没有跌破前一天低点 都是70%仓位** 没走弱不用减仓」
#     （docs/PRODUCTION_PIPELINE.md:240「条件式仓位目标」；该文档自记 **❌ 未落**）
# 口径（**只抬不压**）：档位目标 = main_up 75 / range 50 / high 30 / exit 0；
#   若「当日指数收盘 ≥ 前一日最低」（语料判据，**用 `low` 不是 `close`**）⇒ 目标抬到 `WOLF_POSITION_FLOOR_PCT`（默认 70）；
#   **exit（下跌不做）恒 0**，不套用 70% 条款。
# 作用面：总仓 < 目标 ⇒ 把**底仓保留比例**抬到 `WOLF_POSITION_FLOOR_KEEP`（默认 0.5）
#   ⇒ floor 抬高 ⇒ 止盈/做T 卖腿的可卖量（`可卖 − floor`）缩小 ⇒ **不会为了做T把仓位越做越低**。
#   止损/破位/减仓类仍豁免（走 `_stop_exit_volume` 穿透），所以止血不受影响。
# 生产零影响：开关默认 0 ⇒ 不计算、不取数、比值原样返回（逐字等价改动前）。
_POS_TARGET_BY_BUCKET = {"main_up": 75.0, "range": 50.0, "high": 30.0, "exit": 0.0}
_POSITION_FLOOR = str(os.getenv("WOLF_POSITION_FLOOR", "0")).strip().lower() in ("1", "true", "yes", "on")
# `WOLF_POSITION_FLOOR_ABS`（默认 0）：在"总仓 < 目标"时对本票做 **latch 式绝对底仓认账**
#   （base = 可卖 × keep）。为什么必须有它：只抬 ratio 在被清过仓的票上**完全空转**
#   （清仓写 reset ⇒ anchor 恒 0 ⇒ floor = 一手，与 ratio 无关；纯函数与反事实双重实测）。
_POSITION_FLOOR_ABS = str(os.getenv("WOLF_POSITION_FLOOR_ABS", "0")).strip().lower() in ("1", "true", "yes", "on")


def _pos_keep() -> float:
    """仓位下限的底仓保留比例（`WOLF_POSITION_FLOOR_KEEP`，默认 0.5 = 语料「50%的底仓」）。"""
    try:
        v = float(os.getenv("WOLF_POSITION_FLOOR_KEEP", "0.5") or 0.5)
    except Exception:
        return 0.5
    return min(max(v, 0.0), 1.0)
_IDX_CSV = os.getenv("WOLF_INDEX_CSV",
                     os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                         os.path.dirname(os.path.abspath(__file__))))),
                         "data", "指数数据", "index_daily", "000001.SH.csv"))
_EXPO_CACHE: Dict[str, Any] = {"at": 0.0, "acct": "", "v": None}
_IDX_CACHE: Dict[str, Any] = {"at": 0.0, "v": None}


def position_target_pct(bucket: str, not_broken_prev_low: Optional[bool] = None,
                        pct: Optional[float] = None,
                        targets: Optional[Dict[str, float]] = None) -> float:
    """语料口径的**目标总仓（%）**。`exit`（下跌不做）恒 0；未知档按 range。

    `pct` = 「未跌破前一日低点」时抬到的下限（默认 `WOLF_POSITION_FLOOR_PCT`）。
    `None` = 判据不可用 ⇒ **不套用**该条款（fail-safe，不臆造）。
    """
    t = dict(_POS_TARGET_BY_BUCKET if targets is None else targets)
    b = str(bucket or "range").strip().lower()
    if b not in t:
        b = "range"
    if b == "exit":
        return 0.0                                  # 「下跌趋势就不做」
    base = float(t.get(b, 50.0))
    if not_broken_prev_low:
        try:
            floor_pct = float(os.getenv("WOLF_POSITION_FLOOR_PCT", "70") if pct is None else pct)
        except Exception:
            floor_pct = 70.0
        base = max(base, floor_pct)
    return base


def _index_rows(path: str) -> List[Tuple[str, float, float]]:
    """读一份指数日线 OHLC → [(trade_date, close, low)]。缺 low/解析失败的行**跳过**（不臆造）。"""
    out: List[Tuple[str, float, float]] = []
    try:
        if path.endswith((".json", ".jsonl")):
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
            seq = obj if isinstance(obj, list) else (obj.get("rows") or obj.get("data") or [])
            for r in seq:
                if not isinstance(r, dict):
                    continue
                d = str(r.get("trade_date") or r.get("date") or "").replace("-", "")
                c, lo = r.get("close"), r.get("low")
                if d and c is not None and lo is not None:
                    out.append((d, float(c), float(lo)))
        elif path.endswith(".parquet"):
            import pandas as pd                                   # 懒导入
            df = pd.read_parquet(path)
            for d, row in df.iterrows():
                dd = str(d).replace("-", "")
                c, lo = row.get("close"), row.get("low")
                if dd and c is not None and lo is not None and str(c) != "nan" and str(lo) != "nan":
                    out.append((dd, float(c), float(lo)))
        else:
            import csv as _csv
            with open(path, encoding="utf-8") as f:
                for r in _csv.DictReader(f):
                    d = str(r.get("trade_date") or "").replace("-", "")
                    c, lo = r.get("close"), r.get("low")
                    if not d or c in (None, "") or lo in (None, ""):
                        continue                                  # ⚠️ 2026 段 open/high/low 为空 ⇒ 必须跳过
                    out.append((d, float(c), float(lo)))
    except Exception:
        return []
    out.sort()
    return out


def index_not_broken_prev_low(day: str = "", stale_days: int = 10,
                              sources: Optional[List[str]] = None) -> Optional[bool]:
    """语料 2026-02-05 判据：**当日指数收盘 ≥ 前一日最低** ⇒ 未走弱。

    只读**带 low 的日线 OHLC**（`close` 版不行——「前一日低点」就是 low）。
    多源按序尝试，任一源给出"新鲜"的一对即用；全部不可用 ⇒ `None`（**不套用** 70% 条款）。

    ⚠️ 新鲜度守卫（2026-09-22 踩坑）：仓库 CSV `data/指数数据/index_daily/000001.SH.csv`
    2026 段的 `open/high/low` **全为空**；若不校验新鲜度，就会拿**上一年**的两根行做常数比较
    ⇒ 静默返回恒定的 False（实测"75 天全跌破"这种不可能的结论）。所以要求
    **最新一行的日期距 as-of 不超过 `stale_days` 个自然日**，否则视为不可用。
    """
    cands = list(sources) if sources is not None else []
    if not cands:
        env = os.getenv("WOLF_INDEX_OHLC", "")
        cands = [p for p in env.split(os.pathsep) if p] or [_IDX_CSV]
    try:
        d8 = str(day).replace("-", "")
        for p in cands:
            rows = _index_rows(p)
            if d8:
                rows = [x for x in rows if x[0] <= d8]
            if len(rows) < 2:
                continue
            # 新鲜度：最新一行必须在 as-of 的 stale_days 个自然日内（否则判据不可用）
            if d8:
                try:
                    _a = datetime.strptime(rows[-1][0], "%Y%m%d")
                    _b = datetime.strptime(d8, "%Y%m%d")
                    if abs((_b - _a).days) > int(stale_days):
                        continue
                except Exception:
                    continue
            return bool(rows[-1][1] >= rows[-2][2])
        return None
    except Exception:
        return None


def exposure_pct(account_id: str = "stock", ttl: float = 30.0) -> Optional[float]:
    """当前总仓%（**持仓成本口径**，与 `t_gateway` 建仓资金锚同源）。取不到 ⇒ None。

    TTL 缓存：`base_floor_shares` 是每条卖腿都要走的热路径，不能每次都拉账本+净值。
    """
    import time as _t
    now = _t.time()
    if (_EXPO_CACHE.get("v") is not None and _EXPO_CACHE.get("acct") == account_id
            and now - float(_EXPO_CACHE.get("at") or 0) <= ttl):
        return _EXPO_CACHE["v"]
    try:
        from app.services.t_gateway import get_sellable_ledger         # 懒导入防循环
        from app.services import t_capacity as _cap
        led = get_sellable_ledger(account_id) or {}
        eq = _cap.account_equity(account_id, ledger=led)
        if not eq:
            return None
        pv = 0.0
        for _s, it in led.items():
            try:
                pv += float((it or {}).get("volume") or 0) * float((it or {}).get("avg_price") or 0)
            except Exception:
                continue
        v = pv / float(eq) * 100.0
        _EXPO_CACHE.update({"at": now, "acct": account_id, "v": v})
        return v
    except Exception:
        return None


def apply_position_floor(ratio: float, exposure: Optional[float], target: Optional[float],
                         keep: Optional[float] = None) -> Tuple[float, str]:
    """总仓 < 目标 ⇒ 把底仓保留比例抬到 `keep`（纯函数，**只抬不压**）。返回 (ratio, 归因)。"""
    try:
        r = float(ratio)
    except Exception:
        r = 0.0
    if exposure is None or target is None:
        return r, "仓位下限未介入（仓位/目标不可用）"
    try:
        ex, tg = float(exposure), float(target)
    except Exception:
        return r, "仓位下限未介入（仓位/目标非法）"
    if tg <= 0:
        return r, "仓位下限未介入（目标 0：下跌不做）"
    if ex >= tg:
        return r, "仓位下限未介入（总仓 %.1f%% ≥ 目标 %.0f%%）" % (ex, tg)
    try:
        km = float(os.getenv("WOLF_POSITION_FLOOR_KEEP", "0.5") if keep is None else keep)
    except Exception:
        km = 0.5
    km = min(max(km, 0.0), 1.0)
    if km <= r:
        return r, "仓位下限：总仓 %.1f%% < 目标 %.0f%%，但 ratio 已 ≥ %.2f" % (ex, tg, km)
    return km, ("仓位下限：总仓 %.1f%% < 目标 %.0f%% ⇒ 底仓保留比例 %.2f→%.2f"
                % (ex, tg, r, km))


def wave_bucket() -> Optional[str]:
    """当前浪型 operation → 档；文件缺失/过旧（mtime 超过 T_BASE_RATIO_STALE_DAYS）→ None。

    只读 data/wave_state*.json（与 position_tier.read_wave_state 同源）。**用文件 mtime 判新鲜度**，
    不用文件内 date 字段（daily_decision 记录过该字段口径不一致的坑：文件写于当日、date 却是前一交易日）。
    """
    cands = []
    now = datetime.now()
    d8 = now.strftime("%Y%m%d")
    dash = now.strftime("%Y-%m-%d")
    cands.append(os.path.join(_data_dir(), f"wave_state_{d8}.json"))
    cands.append(os.path.join(_data_dir(), f"wave_state_{dash}.json"))
    cands.append(os.path.join(_data_dir(), "wave_state.json"))
    for p in cands:
        try:
            if not os.path.isfile(p):
                continue
            age = now - datetime.fromtimestamp(os.path.getmtime(p))
            if age > timedelta(days=_STALE_DAYS):
                continue
            with open(p, encoding="utf-8") as f:
                st = json.load(f)
            b = OP_BUCKET.get(str((st or {}).get("operation") or "").strip().lower())
            if b:
                return b
        except Exception:
            continue
    return None


def resolve_ratio(symbol: Optional[str] = None, bucket: Optional[str] = None,
                  state: Optional[Dict[str, Any]] = None) -> Tuple[float, str]:
    """ratio 与档位。优先级：按标的覆盖 > 显式 bucket > 状态文件全局覆盖 > 浪型 > range。"""
    st = state if state is not None else load_state()
    if symbol:
        ov = (st.get("buckets") or {}).get(symbol)
        if ov:
            b = str(ov).strip().lower()
            return ratio_of(b), b
    if bucket:
        b = str(bucket).strip().lower()
        return ratio_of(b), b
    ov = st.get("bucket_override")
    if ov:
        b = str(ov).strip().lower()
        return ratio_of(b), b
    b = wave_bucket() or "range"
    return ratio_of(b), b


# ────────────────────────────── 纯核心 ──────────────────────────────

def _rebase_floor(sellable: int, ratio: float, min_lot: int = MIN_LOT) -> int:
    """认账后的新底仓 = 可卖 × ratio（取整到一手，且至少给 T 仓留一条腿）。"""
    sel = int(sellable or 0)
    if sel < 2 * min_lot:
        return sel                      # 不足两条腿：整仓即底仓，不解锁（别把唯一一手当 T 仓卖光）
    return max(min(_lot_floor(sel * float(ratio), min_lot), sel - min_lot), 0)


def compute_floor(cum_buy: int, sellable: int, ratio: float,
                  rec: Optional[Dict[str, Any]] = None,
                  override: Optional[int] = None,
                  min_lot: int = MIN_LOT,
                  position: Optional[int] = None) -> Tuple[int, Optional[Dict[str, Any]]]:
    """纯函数：算底仓 floor，并返回需要落库的事件（None 表示无需认账）。

    rec（已认账记录）：{"base": int, "cum_at_base": int}。给了 rec 后 floor 只随**新增买入**增长，
    卖出不动它 —— 这是"解锁但不侵蚀"的关键。

    position = **真实持仓**（volume−frozen），sellable = 今日可卖额。两者必须分开：
      · position ≤ 0 → 真清仓 → 记 reset（否则重建后被历史累计买入毒住）；
      · position > 0 但 sellable ≤ 0 → 只是**今日无券可卖**（T+1 当日买入冻结），
        **既不认账也不重置**，保持原有锚（明天可卖时照旧生效）。
      不区分这两者会把"今天刚买、还没到 T+1"的标的错误地重标成 0 底仓。
    """
    cum = int(cum_buy or 0)
    sel = int(sellable or 0)
    pos = sel if position is None else int(position or 0)

    if rec is not None:
        base = int(rec.get("base") or 0)
        cum_at = int(rec.get("cum_at_base") or 0)
        anchor = base + int(max(cum - cum_at, 0) * float(ratio))
    elif cum > 0:
        anchor = int(cum * float(ratio))
    elif sel > 0:
        anchor = int(sel * float(ratio))        # 无成交流水（外部同步仓）
    elif override is not None:
        anchor = int(override)                  # 旧 T_BASE_FLOOR_OVERRIDES（仅无流水、无持仓时）
    else:
        anchor = min_lot
    floor = max(_lot_floor(anchor, min_lot), min_lot)

    if pos <= 0:
        # 无持仓：无流水且无认账记录 → 沿用旧口径返回值（override/下限），无需记事件
        if cum <= 0 and rec is None:
            return floor, None
        # 真清仓 → 记 reset，别让历史累计买入在重建后继续毒住 floor
        if rec is None or int(rec.get("base") or 0) != 0 or int(rec.get("cum_at_base") or 0) != cum:
            return 0, {"kind": "reset", "floor": 0, "sellable": 0, "cum_buy": cum,
                       "ratio": float(ratio), "old_floor": floor}
        return 0, None

    if sel <= 0:
        # 持仓还在、今日无券可卖（T+1 冻结）→ 不认账、不重置，锚保持不变（明天照旧生效）
        return floor, None

    if floor > sel - min_lot:
        # 刚 reset 过且无新增买入 → 不重复认账（避免每次读都重算、以及 0 股时的抖动）
        if rec is not None and int(rec.get("base") or 0) <= 0 and int(rec.get("cum_at_base") or 0) == cum:
            return floor, None
        # A2 严格锚：完全不下调（连首次认账也不做）
        if STRICT_ANCHOR:
            return floor, None
        # 防下调反馈（2026-09-19）：已认账过（base>0）就不再向下重标 —— 否则"卖出→可卖变小→再认账"
        # 会把底仓锚一路啃到一手（实测 733→400→200→100，最终整仓被高抛腿卖光）。
        if NO_DOWN_REBASE and rec is not None and int(rec.get("base") or 0) > 0:
            return floor, None
        new_floor = _rebase_floor(sel, ratio, min_lot)
        if new_floor == floor and _skip_noop_rebase():
            # 2026-09-17 修复：重标结果 = 旧 floor ⇒ 这是 **no-op rebase**，不是真认账。
            # 触发场景：sellable == min_lot(100) 时 `floor > sel - min_lot` 恒真（右侧 0），
            # 而 `_rebase_floor` 因 `sel < 2*min_lot` 直接 `return sel` → 新 floor == 旧 floor。
            # 实测本地 PG 18,642 条 base_floor_rebase 里 16,660 条（89.4%）新旧相同、
            # 占 t_triggers 总量 19.2%（Top reason「底仓 floor rebase：100 → 100 股…」2,661 次）：
            # 每次调用都写一条审计行 + 全量重写状态文件，纯噪声且掩盖真实 rebase。
            # 返回 None 的语义与"写这条事件"完全一致（floor 不变、状态里 base/cum_at_base 也等于旧值）。
            return floor, None
        return new_floor, {"kind": "rebase", "floor": new_floor, "sellable": sel, "cum_buy": cum,
                           "ratio": float(ratio), "old_floor": floor}
    return floor, None


# ────────────────────────────── 落库（事件 + 审计）──────────────────────────────

def _record_event(account_id: str, symbol: str, event: Dict[str, Any],
                  ratio: float, bucket: str) -> Dict[str, Any]:
    st = load_state()
    key = _key(account_id, symbol)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    syms = st.setdefault("symbols", {})
    prev = syms.get(key) or {}
    events: List[Dict[str, Any]] = list(prev.get("events") or [])
    entry = {
        "base": int(event.get("floor") or 0),
        "cum_at_base": int(event.get("cum_buy") or 0),
        "kind": event.get("kind"),
        "rebased_at": now,
        "ratio": round(float(ratio), 4),
        "bucket": bucket,
        "sellable": int(event.get("sellable") or 0),
    }
    events.append({**entry, "prev": {"base": prev.get("base"), "cum_at_base": prev.get("cum_at_base")}})
    syms[key] = {**entry, "events": events[-_MAX_EVENTS:]}
    st["as_of"] = now
    st["last_bucket"] = bucket
    save_state(st)
    print(f"[t-base-floor] {event.get('kind')} {key}: floor {event.get('old_floor')} → {entry['base']}"
          f"（可卖{entry['sellable']} 累计买入{entry['cum_at_base']} ratio={round(float(ratio), 4)} {bucket}）",
          flush=True)
    _audit_trigger(account_id, symbol, event, ratio, bucket)
    return entry


def _audit_trigger(account_id: str, symbol: str, event: Dict[str, Any],
                   ratio: float, bucket: str) -> None:
    """落一条 t_triggers 审计行（status=info，绝不 pending —— 否则会被 t_bridge 当待执行单再跑一次）。"""
    if not _audit_enabled():
        return
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        reason = ("底仓 floor %s：%s → %s 股（可卖%s，累计买入%s，ratio=%s，档=%s）"
                  % (event.get("kind"), event.get("old_floor"), event.get("floor"),
                     event.get("sellable"), event.get("cum_buy"), round(float(ratio), 4), bucket))
        snap = json.dumps({"old_floor": event.get("old_floor"), "floor": event.get("floor"),
                           "sellable": event.get("sellable"), "cum_buy": event.get("cum_buy"),
                           "ratio": float(ratio), "bucket": bucket, "kind": event.get("kind")},
                          ensure_ascii=False)
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO t_triggers (account_id, symbol, event_type, status, mode, direction, reason, "
                "snapshot, created_at) "
                "VALUES (:a, :s, 'base_floor_rebase', 'info', 'system', NULL, :r, CAST(:snap AS jsonb), :ts)"),
                {"a": account_id, "s": symbol, "r": reason[:200], "snap": snap,
                 "ts": datetime.now()})   # ⚠️ 2026-09-19：与全表同钟（Python 钉钟；生产两钟一致）
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[t-base-floor] 审计行写入失败（不影响 floor）: {type(e).__name__}: {str(e)[:80]}")


# ────────────────────────────── 数据读取 ──────────────────────────────

def _cum_buy_volume(account_id: str, symbol: str) -> int:
    """累计未void买入量（底仓锚定基数：只升不降，卖出不缩小底仓）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        v = db.execute(text(
            "SELECT COALESCE(SUM(volume), 0) FROM paper_trades "
            "WHERE account_id = :a AND symbol = :s AND direction IN ('买入','buy') "
            "AND (voided = 0 OR voided IS NULL)"),
            {"a": account_id, "s": symbol}).scalar()
        return int(v or 0)
    finally:
        db.close()


def _hold_shares(account_id: str, symbol: str) -> int:
    """当前可卖持仓 = volume − frozen（与 api/trades._get_hold_shares 同口径）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT volume, frozen FROM paper_positions WHERE account_id = :a AND symbol = :s"),
            {"a": account_id, "s": symbol}).mappings().first()
        if not row:
            return 0
        return max(int(row.get("volume") or 0) - int(row.get("frozen") or 0), 0)
    finally:
        db.close()


# ────────────────────────────── 对外入口 ──────────────────────────────

def _legacy_floor(account_id: str, symbol: str, volume: Optional[int],
                  override: Optional[int], cum_buy: Optional[int] = None) -> int:
    """旧口径（T_BASE_FLOOR_REBASE=0 或新口径异常时的回退）：累计买入×ratio，下限 100 股。"""
    keep = float(os.getenv("T_BASE_KEEP_RATIO", "0.5"))
    base = cum_buy if cum_buy is not None else _cum_buy_volume(account_id, symbol)
    if base and int(base) > 0:
        return max(int(int(base) * keep), MIN_LOT)
    if volume is not None and int(volume) > 0:
        return max(int(int(volume) * keep), MIN_LOT)
    return int(override) if override is not None else MIN_LOT


def evaluate(account_id: str, symbol: str, volume: Optional[int] = None,
             cum_buy: Optional[int] = None, override: Optional[int] = None,
             persist: bool = True) -> Tuple[int, Optional[Dict[str, Any]]]:
    """返回 (floor, event)。volume=可卖持仓（None 时自查 paper_positions），cum_buy 可注入（测试用）。"""
    if not _enabled():
        return _legacy_floor(account_id, symbol, volume, override, cum_buy), None

    cum = int(cum_buy) if cum_buy is not None else _cum_buy_volume(account_id, symbol)
    sel = int(volume) if volume is not None else None
    if sel is None:
        pos = _hold_shares(account_id, symbol)
        sel = pos
    elif sel > 0:
        pos = sel                      # 有券可卖 ⇒ 持仓必然 > 0，免一次查询（热路径）
    else:
        pos = _hold_shares(account_id, symbol)   # 无券可卖：区分"真清仓"与"T+1 冻结"
    st = load_state()
    ratio, bucket = resolve_ratio(symbol=symbol, state=st)
    ratio = apply_keep_min(ratio)          # 结构下限（WOLF_BASE_KEEP_MIN，默认 0 ⇒ 不改动）
    # 仓位目标/下限（WOLF_POSITION_FLOOR，默认 0 ⇒ 不取数、不改动）：总仓低于语料目标时抬底仓保留比例
    if _POSITION_FLOOR:
        try:
            _tgt = position_target_pct(bucket, index_not_broken_prev_low())
            _r_new, _pf_why = apply_position_floor(ratio, exposure_pct(account_id), _tgt)
            if _r_new > ratio:
                # 可观测性：臂里要能数出"仓位下限抬了多少次、从多少抬到多少"
                print("[t-base-floor] %s 仓位下限抬高底仓比例 %.3f→%.3f：%s"
                      % (symbol, ratio, _r_new, _pf_why), flush=True)
            ratio = _r_new
        except Exception as _pfe:
            print("[t-base-floor] 仓位下限计算失败(忽略): %s" % str(_pfe)[:70])
    rec = (st.get("symbols") or {}).get(_key(account_id, symbol))
    floor, event = compute_floor(cum, sel, ratio, rec=rec, position=pos,
                                 override=override if (cum <= 0 and not rec) else None)
    if event and persist:
        _record_event(account_id, symbol, event, ratio, bucket)
    # ── latch 式**绝对**底仓（`WOLF_POSITION_FLOOR_ABS`，库内默认 0）────────────────────────
    # 为什么还需要它（2026-09-22 实测）：上面把 ratio 抬到 0.5 **在被清过仓的票上完全空转** ——
    #   清仓写 reset（base=0, cum_at_base=cum）⇒ 该票再次买入前 anchor 恒为 0
    #   ⇒ floor = max(anchor, MIN_LOT) = **一手**，与 ratio 无关（实测 ratio 0/0.33/0.5/0.67 同结果）。
    # 做法：总仓 < 语料目标时，对本票做一次**认账**（复用既有 rebase 语义：base 只随**新增买入**增长、
    #   不随卖出下降）⇒ 底仓咬在"当时的可卖 × keep"上，不会像"持仓×ratio"那样形成下调反馈被啃光。
    #   止损/破位/减仓类仍走 `_stop_exit_volume` 穿透，不受影响。
    if _POSITION_FLOOR and _POSITION_FLOOR_ABS and pos > 0 and sel and int(sel) > 0:
        try:
            _ex2 = exposure_pct(account_id)
            _tgt2 = position_target_pct(bucket, index_not_broken_prev_low())
            if _ex2 is not None and _tgt2 and float(_ex2) < float(_tgt2):
                _km2 = _pos_keep()
                _want = min(int(_lot_floor(int(sel) * _km2, MIN_LOT)), int(sel))
                if _want > int(floor):
                    _ev2 = {"kind": "rebase", "floor": _want, "sellable": int(sel), "cum_buy": cum,
                            "ratio": float(ratio), "old_floor": int(floor),
                            "src": "position_floor_abs"}
                    if persist:
                        _record_event(account_id, symbol, _ev2, ratio, bucket)
                    print("[t-base-floor] %s 仓位下限(latch) 底仓 %d→%d（可卖%d×%.2f；总仓%.1f%%<目标%.0f%%）"
                          % (symbol, int(floor), _want, int(sel), _km2, _ex2, _tgt2), flush=True)
                    return _want, _ev2
        except Exception as _ae:
            print("[t-base-floor] 仓位下限(latch)失败(忽略): %s" % str(_ae)[:70])
    return int(floor), event


def base_floor_shares(account_id: str, symbol: str, volume: Optional[int] = None,
                      override: Optional[int] = None, cum_buy: Optional[int] = None,
                      persist: bool = True) -> int:
    """底仓保留下限（狼大铁律：底仓不卖）—— 新口径入口。"""
    return evaluate(account_id, symbol, volume=volume, cum_buy=cum_buy,
                    override=override, persist=persist)[0]


def maintain(accounts: Tuple[str, ...] = ("stock",), persist: bool = True) -> Dict[str, Any]:
    """盘前一次性认账：对每个持仓标的按新口径算 floor，需要认账的落状态 + 审计。返回摘要。"""
    from app.services.t_gateway import get_sellable_ledger      # 懒导入，避免循环依赖
    out: Dict[str, Any] = {"checked": 0, "rebased": [], "reset": [], "floors": {}, "bucket": None}
    for acc in accounts or ():
        try:
            ledger = get_sellable_ledger(acc) or {}
        except Exception as e:
            print(f"[t-base-floor] 读取 {acc} 可卖账本失败: {type(e).__name__}: {str(e)[:80]}")
            continue
        for sym, item in ledger.items():
            sel = int((item or {}).get("sellable") or 0)
            try:
                floor, event = evaluate(acc, sym, volume=sel, persist=persist)
            except Exception as e:
                print(f"[t-base-floor] {acc}:{sym} 评估失败: {type(e).__name__}: {str(e)[:80]}")
                continue
            out["checked"] += 1
            out["floors"][_key(acc, sym)] = floor
            if event:
                key = "rebased" if event.get("kind") == "rebase" else "reset"
                out[key].append(f"{sym}(可卖{sel}/底仓{floor})")
    try:
        out["bucket"] = resolve_ratio()[1]
    except Exception as _e_sil2:
        try:
            from app.services import alert_hub as _ah_sil
            _ah_sil.note_silent("t_base_floor.py:801", _e_sil2)
        except Exception:
            print("[silent:t_base_floor.py:801] %s: %s" % (type(_e_sil2).__name__, str(_e_sil2)[:110]), flush=True)
    return out
