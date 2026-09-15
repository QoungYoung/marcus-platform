# -*- coding: utf-8 -*-
"""bt_day_legs_switch.py — 重放**盘前 08:18 那条布腿路径**（`switch_builder.build_plan()`）。

为什么单独一个脚本：生产每天有**两条**布腿路径，都写 `publisher='switch'` 的条件：
  ① **08:18** `jobs/tranche_ladder_report.py`（`SWITCH_AUTO_EXEC=1`）→ `switch_builder.build_plan()`
     —— 买新 = `stock_confirm_result` 里 theme ∈ 方向层池 TOP2 且 stage ∈ (突破候选, 确认) 的票；
  ② **09:20** `jobs/rotation_switch_arm.py` 的买侧（`bt_day_legs.py` 负责）。
实测 09-11：生产 18 条 switch 条件（9 只票）**全部来自 ①**（09:20 那条当天 `buy_chains=[]`、一条没布）；
09-15 才是 ② 布的。只重放 ② 会漏一大半腿。

本脚本在 as-of 沙箱里跑 `build_plan()`，并把**副作用**换掉：
  · `held_positions()` → 回测持仓（`--held` / `--held-from-db`）；
  · `_arm_legs()` → 只**记录**腿（不写生产库、不布条件）。

用法：
  python jobs/bt_day_legs_switch.py --date 20260911 --held-from-db
      [--sandbox /app/data/_bt_full/20260911] [--out <sb>/legs_switch.jsonl]
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time

sys.path[:0] = ["/app", "/app/apps/main_line", "/app/jobs", "/app/app", "/app/core", "/app/backend"]


def held_from_db(cut: str, account: str = "stock"):
    import psycopg2
    url = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
    c = psycopg2.connect(url); cur = c.cursor()
    cur.execute("""SELECT symbol,
                          SUM(CASE WHEN direction = '买入' THEN volume ELSE -volume END) AS v
                   FROM paper_trades
                   WHERE account_id=%s AND COALESCE(voided,0)=0 AND created_at < %s
                   GROUP BY symbol
                   HAVING SUM(CASE WHEN direction = '买入' THEN volume ELSE -volume END) > 0""",
                (account, "%s-%s-%sT00:00:00" % (cut[:4], cut[4:6], cut[6:8])))
    out = [{"symbol": r[0], "volume": int(r[1])} for r in cur.fetchall()]
    cur.close(); c.close()
    return out



def task_enabled(code_dir: str, task_id: str):
    """该日版本树的 `config/tasks.yaml` 里这条任务是否存在且 `enabled: true`（时代检查）。

    ⚠️ 规则买入链 2026-09-07 才有 `tranche_ladder_report`；不查就会出现"用未来才有的链
    在 6-8 月布腿"的时代错位（假阳性）。
    """
    if not code_dir:
        return True, "no_code_dir(未做版本树检查)"
    p = os.path.join(code_dir, "config", "tasks.yaml")
    if not os.path.exists(p):
        return True, "no_tasks_yaml"
    try:
        txt = open(p, encoding="utf-8", errors="replace").read()
    except Exception as e:
        return True, "tasks_yaml_unreadable:%s" % str(e)[:40]
    import re as _re
    for blk in _re.split(r"\n(?=\s*-\s*id:)", txt):
        m = _re.search(r"id:\s*([A-Za-z0-9_]+)", blk)
        if not m or m.group(1) != task_id:
            continue
        en = _re.search(r"enabled:\s*(true|false)", blk)
        if en and en.group(1) == "false":
            return False, "task_disabled"
        return True, "task_enabled"
    return False, "task_not_registered"


def resolve_code_dir(date8: str, explicit: str = "") -> str:
    """该日"在跑的代码版本"目录：`--code-dir` > `data/_bt_code/rev_map.json` 里的映射 > 空（用现行代码）。"""
    if explicit:
        return explicit
    root = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "_bt_code")
    try:
        m = json.load(open(os.path.join(root, "rev_map.json"), encoding="utf-8"))
    except Exception:
        return ""
    rev = ((m.get(date8) or {}).get("rev")) or ""
    d = os.path.join(root, "rev_%s" % rev) if rev else ""
    return d if d and os.path.isdir(d) else ""

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--cut", default="")
    ap.add_argument("--sandbox", default="")
    ap.add_argument("--held", default="")
    ap.add_argument("--held-from-db", action="store_true")
    ap.add_argument("--bars-db", default="/app/data/_bt_full/bars.sqlite")
    ap.add_argument("--code-dir", default="", help="该日的代码版本树（默认按 data/_bt_code/rev_map.json 自动解析）")
    ap.add_argument("--out", default="")
    ap.add_argument("--require-task", default="tranche_ladder_report",
                    help="该日版本树里必须启用的任务 id（时代检查；空=不检查）")
    a = ap.parse_args()

    sb = a.sandbox or os.path.join(os.environ.get("DATA_DIR", "/app/data"), "_bt_full", a.date)
    _code = resolve_code_dir(a.date, a.code_dir)
    if _code:
        for _sub in ("apps/main_line", "jobs", "backend", "core", "config"):
            _p = os.path.join(_code, _sub)
            if os.path.isdir(_p):
                sys.path.insert(0, _p)
        print("[code] 使用该日版本树 %s" % _code, flush=True)
    seed = {}
    try:
        seed = json.load(open(os.path.join(sb, "_seed.json"), encoding="utf-8"))
    except Exception:
        pass
    cut = a.cut or seed.get("cut") or a.date
    os.environ["DATA_DIR"] = sb
    # 生产 08:18 的 `tranche_ladder_report.py` 会 `os.environ.setdefault("SWITCH_AUTO_EXEC", "1")`
    # → 才走 _arm_legs() 真正产出腿；不设 = DRY 模式（实测：命中 9 只但 buy_new 记录为空）
    os.environ.setdefault("SWITCH_AUTO_EXEC", "1")

    # 钉时钟（含 time.strftime/localtime）+ 钉取数：否则 09-11 的代码会 glob 到"最新那天的 gate/文件"
    try:
        sys.path.insert(0, "/app/jobs")
        from bt_run_pinned import pin_clock, install_relay_shim, install_gzcloud_shim
        pin_clock(cut)
        _rs = install_relay_shim(a.bars_db, cut)
        _gs = install_gzcloud_shim(a.bars_db, cut)
        print("[pin] clock=%s relay_shim/gzcloud_shim 已装" % cut, flush=True)
    except Exception as _pe:
        print("[pin] 打桩失败（结论可能失真）: %s" % str(_pe)[:110], flush=True)
    # ⚠️ `bt_run_pinned` 在 import 时会把 /app* 插到 sys.path 最前 → 必须在它之后再插一次版本树，
    #    否则 import 到的是**今天的**模块（实测：mainline_confirm_state 用了现行版，
    #    gate_top_themes 不存在 → 回退到 main_line_state.fusion → 主题变 农业/消费/金融）
    if _code:
        for _sub in ("apps/main_line", "jobs", "backend", "core", "config"):
            _p = os.path.join(_code, _sub)
            if os.path.isdir(_p):
                sys.path.insert(0, _p)
        print("[code] 版本树路径已重新置顶（在 shim import 之后）", flush=True)
    if a.require_task:
        _ok, _why = task_enabled(_code, a.require_task)
        if not _ok:
            print("SKIP_TASK_NOT_AVAILABLE %s %s（该日生产根本没有这条链 → 本路径当日应为 0 条腿）"
                  % (a.require_task, _why), flush=True)
            return 0
    sb_mod = importlib.import_module("switch_builder")
    if _code:
        _sf = os.path.abspath(getattr(sb_mod, "__file__", "") or "")
        _se = os.path.abspath(os.path.join(_code, "apps", "main_line"))
        if _sf and not _sf.startswith(_se):
            print("ABORT_MODULE_PROVENANCE switch_builder=%s 期望在 %s 内" % (_sf, _se), flush=True)
            return 5
    try:      # 诊断：确认主题来源模块/函数（版本树 vs 现行）
        _mcs = importlib.import_module("mainline_confirm_state")
        print("[diag] mainline_confirm_state = %s | gate_top_themes=%s | mainline_top_themes=%s"
              % (getattr(_mcs, "__file__", "?"), hasattr(_mcs, "gate_top_themes"),
                 hasattr(_mcs, "mainline_top_themes")), flush=True)
        if hasattr(_mcs, "gate_top_themes"):
            print("[diag] gate_top_themes(3) =", _mcs.gate_top_themes(3), flush=True)
    except Exception as _de:
        print("[diag] err %s" % str(_de)[:120], flush=True)
    sb_mod.DATA = sb


    # ① 持仓：换成回测持仓（生产版读 paper_positions = 当期，会前视）
    if a.held_from_db:
        held = held_from_db(cut)
    elif a.held:
        held = [{"symbol": x.strip(), "volume": 0} for x in a.held.split(",") if x.strip()]
    else:
        held = []
    sb_mod.held_positions = lambda: list(held)

    # ② 布腿：只记录，不写库
    recorded = {"sell_old": [], "buy_new": []}

    def _record_only(plan):
        for s in plan.get("sell_old") or []:
            recorded["sell_old"].append(dict(s, type="sell_vwap_break"))
        for b in plan.get("buy_new") or []:
            code = str(b["code"])
            sym = ("SH" if code[:1] == "6" else "SZ") + code
            recorded["buy_new"].append({"symbol": sym, "code": code, "theme": b.get("theme"),
                                        "stage": b.get("stage"), "type": "buy_253/254"})
        return [{"type": "bt_noop", "n_sell": len(recorded["sell_old"]), "n_buy": len(recorded["buy_new"])}]

    sb_mod._arm_legs = _record_only
    # 诊断：包一层 active_stocks_by，看 build_plan 内部调用时传的 stages 与返回值
    _orig_asb = sb_mod.active_stocks_by
    def _asb_probe(stages, *a, **kw):
        r = _orig_asb(stages, *a, **kw)
        n = len((r[0] or {})) if isinstance(r, tuple) else -1
        print("[diag] build_plan→active_stocks_by(stages=%s) 命中=%s top12=%s"
              % (stages, n, (r[1] if isinstance(r, tuple) else None)), flush=True)
        return r
    sb_mod.active_stocks_by = _asb_probe

    t0 = time.time()
    print("[legs_switch] T=%s cut=%s held=%d（盘前 08:18 路径）" % (a.date, cut, len(held)), flush=True)
    try:      # 诊断：active_stocks_by 的中间结果（top12 / 确认域规模 / 命中）
        _sc = sb_mod._load("stock_confirm_result.json")
        _bn, _t12 = sb_mod.active_stocks_by(("突破候选", "确认"))
        print("[diag] confirm 概念=%d | top12=%s | 命中=%d %s"
              % (len(_sc or {}), _t12, len(_bn or {}), sorted(_bn or {})[:12]), flush=True)
    except Exception as _de:
        import traceback; traceback.print_exc()
        print("[diag] active_stocks_by err %s" % str(_de)[:110], flush=True)
    try:
        plan = sb_mod.build_plan()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("[legs_switch] build_plan 失败 %s" % str(e)[:120], flush=True)
        return 1
    print("[legs_switch] top3=%s" % plan.get("top3"), flush=True)
    print("[legs_switch] buy_new=%s" % recorded["buy_new"], flush=True)
    print("[legs_switch] sell_old=%s" % recorded["sell_old"], flush=True)
    print("[legs_switch] tiers(前5)=%s" % json.dumps(dict(list((plan.get("tiers") or {}).items())[:5]),
                                                     ensure_ascii=False)[:300], flush=True)
    out = a.out or os.path.join(sb, "legs_switch.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for b in recorded["buy_new"]:
            f.write(json.dumps(dict(b, date=a.date, cut=cut, src="switch_builder_0818"),
                               ensure_ascii=False) + "\n")
        for s in recorded["sell_old"]:
            f.write(json.dumps(dict(s, date=a.date, cut=cut, src="switch_builder_0818_sell"),
                               ensure_ascii=False) + "\n")
    print("[legs_switch] 写出 %s：买腿 %d / 卖腿 %d | %.0fs"
          % (out, len(recorded["buy_new"]), len(recorded["sell_old"]), time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
