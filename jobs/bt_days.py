# -*- coding: utf-8 -*-
"""bt_days.py — 全拟真回测：**多日流水线**（按日 seed → 两条布腿路径 → 汇总）。

每个交易日 T 依次做：
  1. `bt_seed_day.py --date T`      建 as-of 沙箱（含该日代码版本树、时钟打桩、gzcloud/relay 替身）
  2. `bt_day_legs_switch.py --date T`  08:18 路径（switch_builder）——**注意它用的是上一交易日的确认域**
  3. `bt_day_legs.py --date T`       09:20 路径（rotation_switch_arm）
  4. 汇总 `<out>/legs_all.jsonl` + `<out>/legs_by_day.json`

用法（容器内）：
  python jobs/bt_days.py --start 20260909 --end 20260915 [--skip-seed] [--out /app/data/_bt_full/_summary]
"""
from __future__ import annotations

import argparse
import json
import os
import os
import subprocess
import sys
import time
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import bt_env  # noqa: E402  （容器/本地两种布局都能跑）


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass



DATA = os.environ.get("DATA_DIR") or bt_env.DATA
ROOT = os.path.join(DATA, "_bt_full")
TASK_TIMELINE = os.path.join(DATA, "_bt_task_timeline.json")


def _future_union_symbols(root: str, day8: str) -> set:
    """未来某交易日的**候选并集**：直接读该日沙箱里已存在的 leg 文件（磁盘口径，不依赖 DB 现状）。

    为什么用 leg 文件：滚动前瞻预取要覆盖"**后面才会出现的票**"——那些票此刻既不在持仓里、
    条件也可能还没布防，只有 sandbox 的 legs 文件（全年都在磁盘上）能提前告诉我们它会被用到。
    """
    out = set()
    for fn in ("legs_switch.jsonl", "legs.jsonl"):
        p2 = os.path.join(root, day8, fn)
        if not os.path.exists(p2):
            continue
        try:
            for ln in open(p2, encoding="utf-8"):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    _o = json.loads(ln)
                except Exception as _e_sil1:
                    _silent_alert("bt_days.py:50", _e_sil1)
                    continue
                _sy = _o.get("symbol")
                if _sy:
                    out.add(_sy)
        except Exception as _e_sil2:
            _silent_alert("bt_days.py:55", _e_sil2)
    return out


def _fetch_mins_day(day8: str, syms, out_dir: str, log_path: str, timeout: int = 900) -> int:
    """按 (标的, 单日) 取 5min 分钟档（缺档自动补齐/前瞻预取共用）。"""
    if not syms:
        return 0
    return run([sys.executable, bt_env.jobs_file("bt_fetch_mins.py"), "--symbols", ",".join(sorted(syms)),
                "--days", day8, "--freq", "5min", "--out", out_dir, "--index", ""],
               log_path, timeout=timeout)


def _missing_mins_days(root: str, day8: str, syms) -> set:
    """当日并集名单里，`_bt_full/mins` 缺 5min 分钟档的标的集合（供缺档自动补齐自检）。

    为什么需要（2026-09-17 用户："以后这种能不能自动补齐，不要重跑啊"）：
    缺分钟档的标的在 `TMonitor._round` 里会被 `if not quote: continue` **静默跳过**，
    整日不评估 ⇒ 成交/离场都缺，事后只能整年重跑。故当日先补、补不齐就大声记账。
    """
    import glob as _glob
    out = set()
    base = os.path.join(DATA, "_bt_full", "mins")
    for sym in syms:
        code = "".join(ch for ch in str(sym) if ch.isdigit())[:6]
        if not code:
            continue
        if not _glob.glob(os.path.join(base, "*%s*5min*%s*.json" % (code, day8))):
            out.add(sym)
    return out


def mins_union_account() -> str:
    """拉分钟名单里「持仓 / 条件单」查询用的账户。
            # ★ 账本 §9.639 ✓（用户要求：每日起跑前数据体检 ✓）
            #   核对 `mins_adj/<日>` 档数 ≥ 当日名单 ✓ ⇒ 不达标就告警 ＋ 自动补齐 ✓
            #   （20260120 出过「19/43 标的当日无行情」✗，根因是复权崩溃 ✓）
            try:
                import subprocess as _sp_c
                _r_c = _sp_c.run([sys.executable, bt_env.jobs_file("bt_check_day_data.py"),
                                  "--day", d8, "--root", str(a.out), "--fix"],
                                 capture_output=True, text=True, timeout=1200)
                _o_c = (str(_r_c.stdout or "") + str(_r_c.stderr or "")).strip().splitlines()
                print("[days] %s 数据体检 rc=%s ⇒ %s" % (d8, _r_c.returncode,
                      " ｜ ".join(_o_c[-2:])[:220]), flush=True)
            except Exception as _e_c:
                print("[days] %s 数据体检失败（不阻断 ✓）: %s" % (d8, str(_e_c)[:90]), flush=True)

    ⚠️ 2026-09-19 修（用户报：电科数字 SH600850 买了卖不出去、之后再没卖出过）：
    这段原先**硬编码 account_id='stock'** ⇒ 回测账户（drabjan10 等）自己的持仓不进并集名单
    ⇒ 它们的分钟档既不检查也不补齐 ⇒ `TMonitor._round` 里 `if not quote: continue` **静默跳过**
    ⇒ 离场腿整日不评估、仓位冻结（实测 0112~0115 每天有 6 只持仓票「当日无行情」）。
    生产 T_MONITOR_ACCOUNT 默认就是 'stock' ⇒ 本修**在生产逐位不变**。
    """
    return (os.getenv("T_MONITOR_ACCOUNT", "stock") or "stock").strip() or "stock"


def mins_union_symbols(formal_legs, cut: str, account: str = ""):
    """拉分钟名单 = 腿 ∪ 该账户持仓 ∪ 该账户 active/expired 条件单标的。返回 (集合, 统计)。"""
    acc = account or mins_union_account()
    legs = {l.get("symbol") for l in (formal_legs or []) if l.get("symbol")}
    held, conds = set(), set()
    _dsn = os.environ.get("DATABASE_URL", "")
    if not _dsn:
        try:
            import sys as _s
            _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "main_line")
            if _p not in _s.path:
                _s.path.insert(0, _p)
            from db_dsn import dsn as _d
            _dsn = _d()
        except Exception:
            _dsn = "postgresql://marcus:marcus123@127.0.0.1:5432/marcus_trading"
    try:
        import psycopg2
        _conn = psycopg2.connect(_dsn)
        _cur = _conn.cursor()
        _cur.execute("SELECT symbol FROM paper_positions WHERE account_id=%s AND volume>0", (acc,))
        held = {r[0] for r in _cur.fetchall()}
        _cur.execute("SELECT DISTINCT symbol FROM t_conditions WHERE account_id=%s "
                     "AND status IN ('active','expired') AND trade_date >= %s", (acc, cut))
        conds = {r[0] for r in _cur.fetchall()}
        _cur.close(); _conn.close()
    except Exception as _se:
        print("[days] ⚠️ 并集名单取持仓/条件失败（退化为仅腿）: %s" % str(_se)[:90], flush=True)
    return legs | held | conds, {"legs": len(legs), "held": len(held), "conds": len(conds), "account": acc}


def load_timeline(path):
    """逐日 `config/tasks.yaml` 的任务登记表（`data/_bt_task_timeline.json`，由宿主机脚本从
    75 份逐日代码树抽出）→ **时代检查**：某条链当天根本不存在/被停用时，当天就不该布腿。

    ⚠️ 实测教训：不加这层检查，2026-09-01 会"用 9 月才上线的链"布出 5 条腿（生产当天 0 条），
    直接制造假阳性。
    """
    try:
        return (json.load(open(path, encoding="utf-8")) or {}).get("days") or {}
    except Exception:
        return {}


def task_on(tl, day, task_id):
    """该日这条链是否启用。三种返回：
       True/False = 当日版本树里有明确登记（False 含"整条任务还没上线"）
       None       = 当天不在登记表里（信息缺失）→ 保守：不拦
    """
    d = tl.get(day)
    if not d:
        return None
    t = (d.get("tasks") or {}).get(task_id)
    if t is None:
        return False                     # 当天有登记表、但没有这条任务 = 那时还没上线
    return bool(t.get("enabled"))


def trade_days(start: str, end: str, bars_db: str):
    import sqlite3
    c = sqlite3.connect(bars_db)
    days = [r[0] for r in c.execute(
        "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date", (start, end))]
    c.close()
    return days


def prev_trade_day(d8: str, bars_db: str) -> str:
    import sqlite3
    c = sqlite3.connect(bars_db)
    r = c.execute("SELECT max(trade_date) FROM bars WHERE trade_date < ?", (d8,)).fetchone()
    c.close()
    return r[0] if r and r[0] else d8


def run(cmd, log_path, timeout=3600):
    t0 = time.time()
    with open(log_path, "w") as f:
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    _dt = time.time() - t0
    # ★ 账本 §9.693 ✓（用户「加上」✓）：每个子阶段自己报用时 ✓ —— 用来定位 26 分钟到底花在哪 ✗
    try:
        _base = os.path.basename(str(cmd[1] if isinstance(cmd, (list, tuple)) and len(cmd) > 1 else cmd))
        print('[days] ⏱ 子步骤 %s ⇒ %d 秒 (rc=%s)' % (_base, int(_dt), rc), flush=True)
    except Exception:
        pass
    return rc, _dt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--root", default=ROOT, help="沙箱根目录（默认 <DATA_DIR>/_bt_full）")
    ap.add_argument("--bars-db", default=os.path.join(DATA, "_bt_full", "bars.sqlite"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--skip-seed", action="store_true", help="沙箱已建好时跳过 seed")
    ap.add_argument("--llm-mode", default=os.getenv("BT_LLM_MODE", "record"), choices=["record", "replay"])
    ap.add_argument("--task-timeline", default=TASK_TIMELINE,
                    help="逐日任务登记表（时代检查；缺失则不拦）")
    ap.add_argument("--state-from-producers", action="store_true",
                    help="反事实模式：state 由生产者按 as-of 数据算（不要求生产历史快照）；与 --no-era-gating 一起用")
    ap.add_argument("--formal", action="store_true",
                    help="每天布完腿后**就地**跑正式口径（拉当天分钟 → 打包 → 账户层续跑 hold/leg）："
                         "跑批落地即增量，无需事后手动补跑")
    ap.add_argument("--no-era-gating", action="store_true",
                    help="关掉时代检查：**全年反事实**跑法（用现行完整代码跑所有交易日，不再问"
                         "\"当天生产有没有这条链\"）。对齐生产时不要开。")
    ap.add_argument("--reuse-seeded", action="store_true",
                    help="已有 _seed.json 且 cut 相同的沙箱不重跑 seed（续跑/复用已验证沙箱）")
    ap.add_argument("--prod", action="store_true",
                    help="用**生产代码**跑当日交易（调用 jobs/bt_prod_run.py：本地 PG + 生产布腿/触发/网关/模拟盘），"
                         "替代内置 bt_account 账户层")
    # 交易腿 agent（LLM）透传给 bt_prod_run.py —— 年跑要带 agent 必须从这里过
    ap.add_argument("--agent", choices=["on", "off"], default=os.getenv("BT_AGENT", "off"),
                    help="交易腿 agent 的 LLM 决策层（on = 每触发调 /chat，默认 off）")
    ap.add_argument("--agent-url", default=os.getenv("BT_AGENT_CHAT_URL", "http://127.0.0.1:13101/chat"),
                    help="LLM /chat 地址（硬隔离后默认指向本地隔离 dsh 13101）")
    ap.add_argument("--agent-cache", default=os.getenv("BT_LLM_CACHE", os.path.join(DATA, "_bt_llm_year2")))
    ap.add_argument("--agent-mode", default=os.getenv("BT_LLM_MODE", "record"), choices=["record", "replay"])
    ap.add_argument("--agent-tool-guard", default=os.getenv("BT_AGENT_TOOL_GUARD", "on"),
                    choices=["on", "off"], help="as-of 工具口径守卫（提示词层）")
    ap.add_argument("--prod-reset-first", action="store_true",
                    help="年跑第一天把本地模拟盘重置为起点（25 万空仓）；只在 start 那天生效")
    ap.add_argument("--prod-only", action="store_true", help="--prod 时不再跑 bt_account（默认两条都跑，互为交叉校验）")
    # A/B 对照用：独立模拟盘账户（PG 行按 account_id 隔离，不动 stock 主账户）
    ap.add_argument("--account", default=None, help="生产链模拟盘账户（默认 stock）")
    ap.add_argument("--code-dir", default="",
                    help="强制所有交易日使用同一份代码（默认空=按 rev_map 逐日；全年反事实建议用现行代码）")
    ap.add_argument("--low-logic-effective", default=os.getenv("BT_LOW_LOGIC_EFFECTIVE", "prod"),
                    choices=["prod", "same_week"],
                    help="low_logic.json 承载口径：prod=只用上一个 ISO 周或更早写出的 map（生产跨周因果，默认）；"
                         "same_week=本周内即时生效（与生产不符，仅做敏感性对比）")
    ap.add_argument("--low-logic-force", action="store_true",
                    help="周一该日已有 low_logic_written.json 时也重跑 agent（默认幂等跳过，不重复外呼 LLM）")
    a = ap.parse_args()

    if not a.out:
        a.out = os.path.join(a.root, "_summary")
    os.makedirs(a.out, exist_ok=True)
    tl = {} if a.no_era_gating else load_timeline(a.task_timeline)
    if a.no_era_gating:
        print("[days] ⚠️ 时代检查已关闭（全年反事实模式）：所有交易日都用同一份代码跑布腿链", flush=True)
    if a.code_dir:
        print("[days] 强制代码目录：%s" % a.code_dir, flush=True)
    if tl:
        print("[days] 时代检查：任务登记表 %d 天（%s）" % (len(tl), a.task_timeline), flush=True)
    days = trade_days(a.start, a.end, a.bars_db)
    print("[days] %d 个交易日：%s → %s" % (len(days), days[0] if days else "-", days[-1] if days else "-"), flush=True)
    by_day, all_legs = {}, []
    for d8 in days:
        sb = os.path.join(a.root, d8)
        cut = prev_trade_day(d8, a.bars_db)
        entry = {"date": d8, "cut": cut, "steps": {}}
        # ① seed
        # `--reuse-seeded`：已有 `_seed.json` 且 cut 相同的沙箱**不重跑 seed**。
        # 为什么需要：seed 里的 wave agent 是 LLM 步（本地隧道不可用时会被记成 regen_llm_err），
        # 重跑会把已验证沙箱里的 wave_state 冲空（文档里踩过）；年跑续跑必须能复用。
        _reuse = False
        if a.reuse_seeded and not a.skip_seed:
            try:
                _m0 = json.load(open(os.path.join(sb, "_seed.json"), encoding="utf-8")) or {}
                _reuse = str(_m0.get("cut") or "") == cut
            except Exception:
                _reuse = False
            if _reuse:
                entry["steps"]["seed"] = {"reused": True}
                print("[days] %s seed 复用（已有沙箱 cut=%s）" % (d8, cut), flush=True)
        if not a.skip_seed and not _reuse:
            _seed_cmd = [sys.executable, bt_env.jobs_file("bt_seed_day.py"), "--date", d8,
                         "--root", a.root, "--llm-mode", a.llm_mode]
            if a.code_dir:
                _seed_cmd += ["--code-dir", a.code_dir]
            if a.state_from_producers or a.no_era_gating:
                _seed_cmd += ["--state-from-producers"]
            rc, dt = run(_seed_cmd,
                         os.path.join(a.out, "seed_%s.log" % d8), timeout=3600)
            entry["steps"]["seed"] = {"rc": rc, "s": round(dt, 1)}
            print("[days] %s seed rc=%d %.0fs" % (d8, rc, dt), flush=True)
            # ⛔ seed 非 0 = 该日 as-of 输入不可信（如 main_line_state 三处都取不到）→
            #   **不要**照常跑布腿：否则"没跑对"会被记成"生产当天就是 0 条腿"，直接污染对账。
            if rc != 0:
                entry["blocked"] = "seed_rc=%d" % rc
                try:
                    _m = json.load(open(os.path.join(sb, "_seed.json"), encoding="utf-8")) or {}
                    entry["blocked_reason"] = _m.get("fatal") or entry["blocked"]
                except Exception:
                    entry["blocked_reason"] = entry["blocked"]
                by_day[d8] = entry
                print("[days] %s ⛔ 阻断：%s（跳过两条布腿路径）" % (d8, entry["blocked_reason"]), flush=True)
                continue
        # ①b **跨日状态结转**（P0-a / P0-b，2026-09-17）：seed 之后、**跑生产链之前**，把**上一交易日沙箱**
        #     的状态文件带进本日沙箱（首日 / 前值不可用 → `{}`）。为什么必须在这一步：
        #       · 这些文件决定行为（floor 锁卖腿 / 档位额度 / 254 分步回补链 / 禁买 / 回补 / 换手窗口），
        #         软链到生产当期快照 = 未来记录参与当日判断 + 跨日不连续；
        #       · `--reuse-seeded` / `--skip-seed` 时 seed 不跑 → 只有这里能保证结转。
        #     幂等：重复跑同一天，结果只由"前一交易日沙箱"决定；失败只告警、不阻断。
        try:
            import bt_seed_day as _bsd
            _carry = _bsd.carry_state_files(sb, a.root, d8, a.bars_db)
            if _carry:
                entry["steps"]["carry_state"] = {
                    k: {"src": v.get("src"), "kind": v.get("kind"), "events": v.get("events"),
                        "max_rebased_at": v.get("max_rebased_at"), "max_date": v.get("max_date"),
                        "prev": v.get("prev")} for k, v in _carry.items()}
                _tf = _carry.get("t_base_floor_rebase.json") or {}
                print("[days] %s 跨日状态结转：t_base_floor_rebase.json %s（events=%s, max_rebased_at=%s, %s）"
                      % (d8, _tf.get("kind"), _tf.get("events"), _tf.get("max_rebased_at") or "-",
                         _tf.get("src")), flush=True)
        except Exception as _ce:
            entry["steps"]["carry_state"] = {"err": str(_ce)[:160]}
            print("[days] %s ⚠️ 跨日状态结转失败（不阻断，继续跑）：%s" % (d8, str(_ce)[:160]), flush=True)
        # ①c **low_logic 按周承载 + 周内不重算**（`jobs/bt_low_logic_week.py`，2026-09-17 接线）：
        #     · `position_class.py:124 read_logic()` 读 `low_logic.json`，生产语义 = **周一 08:20 先读后写**
        #       ⇒ 当日沙箱必须承载"上周（或更早）产出的 map"，周一新产出的 map 只给**下周** position_class 用；
        #     · 只有**周一**才跑 `bt_low_logic_asof.py`（record/replay 由 `--llm-mode`/`BT_LLM_MODE` 决定），
        #       产物落 `<沙箱>/low_logic_written.json`（旁挂，**不覆盖**当日 position_class 已经读过的那份）；
        #     · 审计落 `<沙箱>/low_logic_provenance.json`（本周用的是哪一天的 map / 本日有没有产出 / 给哪一周用）。
        #     幂等：carry 只由"产品扫描 + 产品文件字节"决定；agent 在该日已有产品时直接跳过（除非 --low-logic-force）。
        #     seed（PINNED 之前）已做过一次 carry；这一步覆盖 `--reuse-seeded` / `--skip-seed`（seed 没跑）的情况。
        try:
            import bt_low_logic_week as _llw
            _lc = _llw.carry_into_sandbox(d8, sb, a.root, effective=a.low_logic_effective,
                                          quiet=True, from_seed=False)
            _la = _llw.run_agent(d8, sb, a.root, mode=a.llm_mode, force=a.low_logic_force, quiet=True)
            _em = _lc.get("effective_map") or {}
            entry["steps"]["low_logic"] = {
                "effective": {"source_day": _em.get("source_day"), "kind": _em.get("source_kind"),
                              "md5": _em.get("md5"), "n": _em.get("n_concepts"),
                              "is_repo_stub": _em.get("is_repo_stub"), "bootstrap": _em.get("bootstrap")},
                "written_this_day": _la,
                "provenance": os.path.join(sb, "low_logic_provenance.json")}
            print("[days] %s low_logic：本日 map ← %s（%s, md5=%s%s）｜周一产出：%s"
                  % (d8, _em.get("source_day") or "-", _em.get("source_kind"), str(_em.get("md5"))[:8],
                     "，仍是 repo 9 月 stub" if _em.get("is_repo_stub") else "",
                     ("md5=%s n=%s，%s" % (str(_la.get("product_md5"))[:8], _la.get("product_n"),
                                           _la.get("applies_to_week") or ""))
                     if _la.get("product_md5") else (_la.get("skip") or "-")), flush=True)
        except Exception as _le:
            entry["steps"]["low_logic"] = {"err": str(_le)[:160]}
            print("[days] %s ⚠️ low_logic 按周承载/周一产出失败（不阻断，继续跑）：%s"
                  % (d8, str(_le)[:160]), flush=True)
        # 备份"当天(cut)口径"的确认域，供 08:18 用完还原
        try:
            import shutil
            src_c = os.path.join(sb, "stock_confirm_result.json")
            if os.path.exists(src_c):
                shutil.copy2(src_c, os.path.join(sb, "stock_confirm_result_cut.json"))
        except Exception as _e_sil3:
            _silent_alert("bt_days.py:341", _e_sil3)
        # ② 08:18 路径：用**上一交易日**的确认域（switch_builder 08:18 跑，confirm 08:20 才刷新）
        prev = prev_trade_day(cut, a.bars_db)
        sw_on = task_on(tl, d8, "tranche_ladder_report")
        arm_on = task_on(tl, d8, "rotation_switch_arm")
        conf_on = task_on(tl, d8, "stock_confirm_refresh")
        entry["paths"] = {"tranche_ladder_report": sw_on, "rotation_switch_arm": arm_on,
                          "stock_confirm_refresh": conf_on}
        if conf_on is False:
            entry["steps"]["confirm_prevday"] = {"skipped": "task_not_registered"}
        else:
          _cdir = a.code_dir or os.path.join(DATA, "_bt_code", "rev_" + (
              (json.load(open(os.path.join(DATA, "_bt_code", "rev_map.json"), encoding="utf-8"))
               .get(d8, {}) or {}).get("rev", "")))
          rc_prev, dtp = run([sys.executable, bt_env.jobs_file("bt_run_pinned.py"), "--as-of", prev,
                              "--data-dir", sb, "--bars-db", a.bars_db, "--code-dir", _cdir,
                              "--script", (os.path.join(a.code_dir, "apps/main_line/stock_confirm_judge.py")
                                           if a.code_dir else _script_in_rev(d8, "apps/main_line/stock_confirm_judge.py"))],
                             os.path.join(a.out, "confirm_%s.log" % d8), timeout=1800)
          entry["steps"]["confirm_prevday"] = {"rc": rc_prev, "as_of": prev, "s": round(dtp, 1)}
        if sw_on is False:
            entry["steps"]["switch_0818"] = {"skipped": "task_not_registered"}
        else:
            _sw_cmd = [sys.executable, bt_env.jobs_file("bt_day_legs_switch.py"), "--date", d8, "--held-from-db",
                       "--sandbox", sb, "--bars-db", a.bars_db]
            if a.code_dir:
                _sw_cmd += ["--code-dir", a.code_dir]
            rc_sw, dts = run(_sw_cmd,
                             os.path.join(a.out, "switch_%s.log" % d8), timeout=1800)
            entry["steps"]["switch_0818"] = {"rc": rc_sw, "s": round(dts, 1)}
            # ── 账本 §9.470 ✓：当日**缺分钟⇒自动补齐（含退避重试）**（用户要求 ✓ 默认开 ✓）──
            #   为什么必须在这里 ✓：臂每天新布腿 ⇒ 新标的的分钟可能缺 ✗ ⇒ 盘中判断会被**静默跳过** ✗
            #   （"离场层哑火"同源 ✓）；且**必须自动读 `.env` 的 key** ✓，否则会误判成"上游没有" ✗
            try:
                if str(os.getenv("WOLF_ENSURE_MINS", "1")).strip().lower() in ("1", "true", "yes", "on"):
                    _em = subprocess.run([sys.executable, bt_env.jobs_file("bt_ensure_mins_day.py"),
                                          "--day", d8, "--root", a.root,
                                          "--account", str(a.account or "drabt35")],
                                         capture_output=True, text=True, timeout=1800)
                    entry.setdefault("steps", {})["ensure_mins"] = (
                        "ok" if _em.returncode == 0 else "rc=%s" % _em.returncode)
                    for _l in (":".join(_em.stdout.splitlines()[-2:]) if _em.stdout else "").split("\n"):
                        if _l.strip():
                            print("[days] ensure_mins %s" % _l.strip()[:120], flush=True)
            except Exception as _e:
                entry.setdefault("steps", {})["ensure_mins"] = "err:%s" % str(_e)[:40]
            # ── 账本 §9.465 ✓：低吸腿「**未命中留痕**」（用户要求 ✓ 默认开 ✓；只加日志、不改判据 ✓）──
            try:
                if str(os.getenv("WOLF_LEG_MISS_REPORT", "1")).strip().lower() in ("1", "true", "yes", "on"):
                    _mr = subprocess.run([sys.executable, bt_env.jobs_file("leg_miss_report.py"),
                                          "--day", d8, "--sandbox", sb],
                                         capture_output=True, text=True, timeout=300)
                    if _mr.returncode == 0:
                        entry.setdefault("steps", {})["leg_miss_report"] = "ok"
                    else:
                        entry.setdefault("steps", {})["leg_miss_report"] = "rc=%s" % _mr.returncode
            except Exception as _e:
                entry.setdefault("steps", {})["leg_miss_report"] = "err:%s" % str(_e)[:40]
        # 留痕（2026-09-19 排查教训）：08:18 布腿器读的就是这份"上一交易日 as-of"确认域，但它随后会被
        #   当天口径覆盖 ⇒ 事后取证只能靠日志推断。先把这份**运行期产物**落盘成
        #   `stock_confirm_result_prev0818.json`（只增加一个副本文件，不改任何行为）。
        try:
            import shutil as _sh2
            _src_p = os.path.join(sb, "stock_confirm_result.json")
            if os.path.exists(_src_p):
                _sh2.copy2(_src_p, os.path.join(sb, "stock_confirm_result_prev0818.json"))
        except Exception as _e_sil4:
            _silent_alert("bt_days.py:408", _e_sil4)
        # 08:18 用的是上一交易日确认域 → 跑完必须**还原当天口径**，否则 09:20 路径会看错版本
        try:
            import shutil
            bak = os.path.join(sb, "stock_confirm_result_cut.json")
            if os.path.exists(bak):
                shutil.copy2(bak, os.path.join(sb, "stock_confirm_result.json"))
                entry["steps"]["confirm_restored_to_cut"] = True
        except Exception as _re:
            entry["steps"]["confirm_restore_err"] = str(_re)[:80]
        # ③ 09:20 路径
        if arm_on is False:
            entry["steps"]["arm_0920"] = {"skipped": "task_not_registered"}
        else:
            _arm_cmd = [sys.executable, bt_env.jobs_file("bt_day_legs.py"), "--date", d8, "--held-from-db",
                        "--sandbox", sb, "--bars-db", a.bars_db]
            if a.code_dir:
                _arm_cmd += ["--code-dir", a.code_dir]
            # 超时 2400 → 5400s（2026-09-20：0211 因 member 判定瞬时排队超时，把整个 run 打死）
            rc_arm, dta = run(_arm_cmd,
                              os.path.join(a.out, "arm_%s.log" % d8), timeout=5400)
            entry["steps"]["arm_0920"] = {"rc": rc_arm, "s": round(dta, 1)}
        # ③b **正式口径就地增量**（--formal）：当天腿 → 拉分钟 → 打包 → 账户层续跑
        _formal_legs = []
        for _fn in ("legs_switch.jsonl", "legs.jsonl"):
            _p2 = os.path.join(sb, _fn)
            if os.path.exists(_p2):
                for _ln in open(_p2, encoding="utf-8"):
                    _ln = _ln.strip()
                    if _ln:
                        try:
                            _formal_legs.append(json.loads(_ln))
                        except Exception as _e_sil5:
                            _silent_alert("bt_days.py:441", _e_sil5)
        if a.formal:
            # ⚠️ 2026-09-16：拉分钟名单必须是**并集**——只拉"当天布腿标的"会漏掉
            # 持仓票的离场腿（`_arm_stock_exit_legs` 布 vwap/support/high_sell）与当日 armed 条件标的
            # → 那些标的没有当日 bars → `TMonitor._round` 里 `if not quote: continue` **静默跳过**
            # → 离场层哑火、仓位冻结（实测 588 个标的日中 196 个缺分钟，占 33%）。
            # ⚠️ 账户必须用 T_MONITOR_ACCOUNT（回测=drabXX；生产默认 stock）—— 原先硬编码 'stock'
            #    ⇒ 回测自己的持仓不进名单 ⇒ 分钟档不补 ⇒ 监控静默跳过 ⇒ 持仓票卖不出去（见函数 docstring）
            _symset, _uc = mins_union_symbols(_formal_legs, cut)
            print("[days] %s 拉分钟名单=腿%d ∪ 持仓%d ∪ 条件%d = %d（账户=%s）"
                  % (d8, _uc["legs"], _uc["held"], _uc["conds"], len(_symset), _uc["account"]), flush=True)
            _syms = sorted(x for x in _symset if x)
            # ── 打包目录：**共享池**（`WOLF_PACK_SHARED`，库内默认 0 = 旧行为逐字不变）──
            #   2026-09-25 用户拍板「把这个共享打包实现掉」。依据：pack 是**确定性产物** ——
            #   同一标的在 T25 与 T28 两个臂里 md5 **完全一致**（9d4e441162 ✓）⇒ 每臂各打一份纯浪费 ✗
            #   （1,081 只 / 642,150 根 bar / 139MB；单日 1.5~2.3 分钟 ⇒ 75 天 ≈ 2~2.9 小时 ✗）。
            #   置 1 ⇒ 用 `data/_bt_full/pack_shared/<复权口径>_<打包工具md5>/`（跨臂复用 ✓）；
            #   桶名含口径与**打包工具代码 md5** ⇒ 口径或逻辑一变自动换桶，绝不混用 ✓
            _pack = os.path.join(a.root, "pack")
            if str(os.getenv("WOLF_PACK_SHARED", "0")).strip() == "1":
                try:
                    import hashlib as _hl
                    _adj = "adj" if str(os.getenv("WOLF_ADJ_PRICE", "0")).strip() == "1" else "raw"
                    with open(bt_env.jobs_file("bt_pack_mins.py"), "rb") as _fh:
                        _tool = _hl.md5(_fh.read()).hexdigest()[:8]
                    _pack = os.path.join(DATA, "_bt_full", "pack_shared", "%s_%s" % (_adj, _tool))
                    os.makedirs(_pack, exist_ok=True)
                    print("[days] %s 打包目录=**共享池** %s（WOLF_PACK_SHARED=1 ✓ 跨臂复用）"
                          % (d8, _pack.replace(DATA + os.sep, "")), flush=True)
                except Exception as _pe2:
                    print("[days] 共享打包池初始化失败，回退臂内 pack：%s" % str(_pe2)[:80], flush=True)
            if _syms:
                # ── 缺档自动补齐（2026-09-17 用户："以后这种能不能自动补齐，不要重跑啊"）──
                # 先补当日缺档再往下跑；补不齐就写进 entry["no_quote"] 并大声打印，
                # 绝不静默跳过——否则"少做"会被误读成策略选择，事后只能整年重跑。
                try:
                    _miss0 = _missing_mins_days(a.root, d8, _syms)
                    if _miss0:
                        print("[days] %s 缺分钟档 %d 个 → 自动补齐: %s"
                              % (d8, len(_miss0), ",".join(sorted(_miss0)[:8])), flush=True)
                        run([sys.executable, bt_env.jobs_file("bt_backfill_mins_union.py"),
                             "--start", d8, "--end", d8, "--root", a.root,
                             "--account", mins_union_account()],      # ⚠️ 账户口径（原先漏传 ⇒ 默认 stock）
                            os.path.join(a.out, "minsfill_%s.log" % d8), timeout=1200)
                        _miss1 = _missing_mins_days(a.root, d8, _syms)
                        if _miss1:
                            entry["no_quote"] = sorted(_miss1)
                            print("[days] %s ⚠️ 自动补齐后仍缺 %d 个标的分钟档（当日不评估）: %s"
                                  % (d8, len(_miss1), ",".join(sorted(_miss1)[:10])), flush=True)
                        else:
                            print("[days] %s ✅ 缺档已补齐" % d8, flush=True)
                except Exception as _me:
                    print("[days] %s 缺档自动补齐异常（继续跑，缺档会计账）: %s" % (d8, str(_me)[:90]), flush=True)
                _fetch_mins_day(d8, _syms, os.path.join(DATA, "_bt_full", "mins"),
                                os.path.join(a.out, "fetchmins_%s.log" % d8), timeout=1800)
                # ⚠️ 2026-09-19：**复核必须在常规取数之后** —— 上面那次复核在取数之前，
                #   实测因此打出假的"仍缺 22 个（当日不评估）"，而随后的 fetchmins 其实全取到了。
                try:
                    _miss2 = _missing_mins_days(a.root, d8, _syms)
                    if _miss2:
                        entry["no_quote"] = sorted(_miss2)
                        print("[days] %s ⚠️ 取数后仍缺 %d 个标的分钟档（当日不评估）: %s"
                              % (d8, len(_miss2), ",".join(sorted(_miss2)[:10])), flush=True)
                    else:
                        entry.pop("no_quote", None)
                        print("[days] %s ✅ 当日名单分钟档齐全（%d 个标的）" % (d8, len(_syms)), flush=True)
                except Exception as _me2:
                    print("[days] %s 取数后复核异常（忽略）: %s" % (d8, str(_me2)[:80]), flush=True)
                # ── 滚动前瞻预取（2026-09-17 用户拍板）──
                # 每天按**未来 K 天的 leg 文件**预取分钟档，堵住"后出现的票当天无数据"这个缺口；
                # 只在缺档时取、失败不影响当日（当日缺口仍由上面的复检大声记账）。
                # BT_PREFETCH_DAYS=0 关闭；默认 3。
                try:
                    _K = int(os.getenv("BT_PREFETCH_DAYS", "3") or 0)
                    if _K > 0 and d8 in days:
                        _i = days.index(d8)
                        _miss_list = []
                        for _fd in days[_i + 1:_i + 1 + _K]:
                            _fs = _future_union_symbols(a.root, _fd)
                            _fm = _missing_mins_days(a.root, _fd, _fs)
                            if not _fm:
                                continue
                            _fetch_mins_day(_fd, _fm, os.path.join(DATA, "_bt_full", "mins"),
                                            os.path.join(a.out, "prefetch_%s.log" % _fd), timeout=600)
                            _left = _missing_mins_days(a.root, _fd, _fs)
                            _miss_list.append((_fd, len(_fm), len(_left)))
                        if _miss_list:
                            print("[days] %s 前瞻预取(%d天)：%s" % (
                                d8, _K, "；".join("%s 缺%d→仍缺%d" % t for t in _miss_list)), flush=True)
                except Exception as _pe:
                    print("[days] %s 前瞻预取异常（不影响当日）: %s" % (d8, str(_pe)[:90]), flush=True)
                # ── ③ 预取后**补跑一次复权分钟档**（`WOLF_ADJ_MINS_AUTO`，库内默认 0 = 关 ✓）
                #   病灶（账本 §9.185）：`mk_adj_mins.py` 只对**当时已存在**的原始文件生成过复权档 ✓
                #   ⇒ **预取/新抓到**的原始文件**不会自动**生成 `mins_adj` ✗
                #   ⇒ 新票当日"无报价" ✗（实测 SZ000408 0203/0204 ⚠️ 无行情 ✓）
                if str(os.getenv("WOLF_ADJ_MINS_AUTO", "0")).strip().lower() in ("1", "true", "yes", "on"):
                    try:
                        _mk = os.path.join(bt_env.REPO, ".dsh-tmp", "wolfbt", "mk_adj_mins.py")
                        if os.path.exists(_mk):
                            run([sys.executable, _mk],
                                os.path.join(a.out, "adj_mins_%s.log" % d8), timeout=600)
                            print("[days] %s ✅ 复权分钟档补跑完成（WOLF_ADJ_MINS_AUTO=1）" % d8, flush=True)
                    except Exception as _ame:
                        print("[days] %s 复权分钟档补跑异常（不影响当日）: %s" % (d8, str(_ame)[:90]), flush=True)
                run([sys.executable, bt_env.jobs_file("bt_pack_mins.py"),
                     "--mins", os.path.join(DATA, "_bt_full", "mins"), "--pack", _pack,
                     "--bars-db", a.bars_db], os.path.join(a.out, "pack_%s.log" % d8), timeout=1800)
                if not a.prod_only:
                    for _m in ("hold", "leg"):
                        run([sys.executable, bt_env.jobs_file("bt_account.py"), "--root", a.root, "--pack", _pack,
                             "--mode", _m, "--resume", "--bars-db", a.bars_db,
                             "--out", os.path.join(a.out, "account_%s.json" % _m)],
                            os.path.join(a.out, "acct_%s_%s.log" % (_m, d8)), timeout=3600)
                # ③c **生产链**（用户 2026-09-16 拍板）：生产布腿 → 触发 → 网关 → 模拟盘（本地 PG）
                if a.prod:
                    _pc = [sys.executable, bt_env.jobs_file("bt_prod_run.py"), "--day", d8,
                           "--root", a.root, "--mins", os.path.join(DATA, "_bt_full", "mins"),
                           "--bars-db", a.bars_db,
                           "--out", os.path.join(a.out, "prod_%s.json" % d8)]
                    if a.prod_reset_first and d8 == days[0]:
                        _pc.append("--reset")
                    if getattr(a, "account", None):
                        _pc += ["--account", str(a.account)]
                    # agent 层透传（年跑带 LLM 决策必须显式给，否则子进程默认 off）
                    if a.agent == "on":
                        _pc += ["--agent", "on", "--agent-url", a.agent_url,
                                "--agent-cache", a.agent_cache, "--agent-mode", a.agent_mode,
                                "--agent-tool-guard", a.agent_tool_guard]
                    _prod_out = os.path.join(a.out, "prod_%s.json" % d8)
                    _rcp, _dtp = run(_pc, os.path.join(a.out, "prod_%s.log" % d8), timeout=10800)
                    entry["steps"]["prod"] = {"rc": _rcp, "s": round(_dtp, 1)}
                    # ⛔ 生产链失败必须**当天停**：否则 bt_days 会把 170 天全部"跑完"却一条结果都没有
                    #   （2026-09-16 实测：子进程 170 次 NameError，父进程照跑到 06-09，白跑 2 小时）
                    if _rcp != 0 or not os.path.exists(_prod_out):
                        entry["blocked"] = "prod_rc=%d" % _rcp
                        entry["blocked_reason"] = "生产链失败（见 %s）" % os.path.join(a.out, "prod_%s.log" % d8)
                        by_day[d8] = entry
                        print("[days] %s ⛔ 生产链失败 rc=%d → 终止年跑（不静默跳过）" % (d8, _rcp), flush=True)
                        break
                entry["steps"]["formal"] = {"symbols": len(_syms), "modes": ["hold", "leg"]}
                print("[days] %s 正式口径已增量更新（%d 个标的）" % (d8, len(_syms)), flush=True)

        # ④ 汇总当日腿
        legs = []
        for fn, src in (("legs_switch.jsonl", "switch_0818"), ("legs.jsonl", "arm_0920")):
            p = os.path.join(sb, fn)
            if os.path.exists(p):
                for ln in open(p, encoding="utf-8"):
                    ln = ln.strip()
                    if ln:
                        try:
                            legs.append(json.loads(ln))
                        except Exception as _e_sil6:
                            _silent_alert("bt_days.py:594", _e_sil6)
        entry["n_legs"] = len(legs)
        entry["legs"] = [l.get("symbol") for l in legs]
        by_day[d8] = entry
        all_legs.extend(legs)
        print("[days] %s cut=%s → 腿 %d 条 %s" % (d8, cut, len(legs), entry["legs"]), flush=True)

    with open(os.path.join(a.out, "legs_all.jsonl"), "w", encoding="utf-8") as f:
        for l in all_legs:
            f.write(json.dumps(l, ensure_ascii=False) + "\n")
    with open(os.path.join(a.out, "legs_by_day.json"), "w", encoding="utf-8") as f:
        json.dump(by_day, f, ensure_ascii=False, indent=1)
    print("[days] 完成：%d 天 / %d 条腿 → %s" % (len(by_day), len(all_legs), a.out), flush=True)
    return 0


def _script_in_rev(d8: str, rel: str) -> str:
    """该日版本树里的脚本路径（不存在则回现行 /app 下的）。"""
    try:
        m = json.load(open(os.path.join(DATA, "_bt_code", "rev_map.json"), encoding="utf-8"))
        rev = ((m.get(d8) or {}).get("rev")) or ""
        p = os.path.join(DATA, "_bt_code", "rev_%s" % rev, rel)
        if rev and os.path.exists(p):
            return p
    except Exception as _e_sil7:
        _silent_alert("bt_days.py:619", _e_sil7)
    return os.path.join(bt_env.REPO, rel)



# ★ 账本 §9.623 ✓：把 logging(WARNING+) 接到告警中心（用户质问"怎么这么多没接的" ✓）
#   WARNING ⇒ 只落盘（看板可见 ✓）；ERROR ⇒ 落盘 ＋ 推 QQ ✓；**无需改任何调用点** ✓
#   开关 `WOLF_ALERT_FROM_LOGGING`（库内默认 0 ⇒ 生产零影响 ✓；回测 pins 置 1 ✓）
try:
    # ★ 账本 §9.638 ✓（用户连问"这个也不推送QQ"✗ ⇒ 查出**钩子根本没装上** ✓）：
    #   实测 ✓：`from core import …` 在回测子进程里**失败** ✗（`No module named 'core'` /
    #   `cannot import name 'alert_exceptions' from 'core'` ⇒ 环境里已有**另一个 core 包** ✗）
    #   ⇒ **两个钩子全部静默失效** ✗ ⇒ 于是"该推的一条都没推" ✓✓
    #   ⇒ 改成**按文件路径导入** ✓（与 `alert_hub.push_qq` 同一套路 ✓，不依赖 sys.path ✓）
    import importlib.util as _ilu_a
    _root_a = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # ★ 用 __file__ 推导（不依赖 REPO 变量 ✓）
    _p_a = os.path.join(_root_a, "core", "alert_logging.py")
    _sp_a = _ilu_a.spec_from_file_location("marcus_alert_logging", _p_a)
    _m_a = _ilu_a.module_from_spec(_sp_a)
    _sp_a.loader.exec_module(_m_a)
    _m_a.install()
except Exception as _e_al_log:
    print("[bt] alert_logging 安装失败: %s" % str(_e_al_log)[:90], flush=True)


# ★ 账本 §9.624 ✓：**异常抛出点**接告警（用户："抛出异常来你推送给我不就行吗" ✓）
#   ① `sys.monitoring` 的 RAISE ⇒ **被 try/except 吞掉的异常也能看见** ✓
#   ② `subprocess.run` 的 rc≠0 ⇒ 子进程失败也推 ✓
#   只报自家代码 ✓、按 (文件,行,类型) 去重 600 秒 ✓ ⇒ 不刷屏 ✓
#   开关 `WOLF_ALERT_ON_RAISE`（库内默认 0 ⇒ 生产零影响 ✓；回测 pins 置 1 ✓）
try:
    import importlib.util as _ilu_e            # ★ §9.638：按路径导入 ✓（不依赖 sys.path ✓）
    _root_e = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _p_e = os.path.join(_root_e, "core", "alert_exceptions.py")
    _sp_e = _ilu_e.spec_from_file_location("marcus_alert_exceptions", _p_e)
    _m_e = _ilu_e.module_from_spec(_sp_e)
    _sp_e.loader.exec_module(_m_e)
    _m_e.install()
except Exception as _e_al_exc:
    print("[bt] alert_exceptions 安装失败: %s" % str(_e_al_exc)[:90], flush=True)

if __name__ == "__main__":
    sys.exit(main())
