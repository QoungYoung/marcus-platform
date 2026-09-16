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
import subprocess
import sys
import time
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import bt_env  # noqa: E402  （容器/本地两种布局都能跑）


DATA = os.environ.get("DATA_DIR") or bt_env.DATA
ROOT = os.path.join(DATA, "_bt_full")
TASK_TIMELINE = os.path.join(DATA, "_bt_task_timeline.json")


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
    return rc, time.time() - t0


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
    ap.add_argument("--prod-reset-first", action="store_true",
                    help="年跑第一天把本地模拟盘重置为起点（25 万空仓）；只在 start 那天生效")
    ap.add_argument("--prod-only", action="store_true", help="--prod 时不再跑 bt_account（默认两条都跑，互为交叉校验）")
    ap.add_argument("--code-dir", default="",
                    help="强制所有交易日使用同一份代码（默认空=按 rev_map 逐日；全年反事实建议用现行代码）")
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
        # 备份"当天(cut)口径"的确认域，供 08:18 用完还原
        try:
            import shutil
            src_c = os.path.join(sb, "stock_confirm_result.json")
            if os.path.exists(src_c):
                shutil.copy2(src_c, os.path.join(sb, "stock_confirm_result_cut.json"))
        except Exception:
            pass
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
            rc_arm, dta = run(_arm_cmd,
                              os.path.join(a.out, "arm_%s.log" % d8), timeout=2400)
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
                        except Exception:
                            pass
        if a.formal:
            _syms = sorted({l.get("symbol") for l in _formal_legs if l.get("symbol")})
            _pack = os.path.join(a.root, "pack")
            if _syms:
                run([sys.executable, bt_env.jobs_file("bt_fetch_mins.py"), "--symbols", ",".join(_syms),
                     "--days", d8, "--freq", "5min", "--out", os.path.join(DATA, "_bt_full", "mins"),
                     "--index", ""], os.path.join(a.out, "fetchmins_%s.log" % d8), timeout=1800)
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
                        except Exception:
                            pass
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
    except Exception:
        pass
    return os.path.join(bt_env.REPO, rel)


if __name__ == "__main__":
    sys.exit(main())
