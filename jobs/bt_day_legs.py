# -*- coding: utf-8 -*-
"""bt_day_legs.py — 在 as-of 沙箱里重放**单个决策日的 09:20 布腿器（买侧）**，产出当日腿。

口径（与 `jobs/rotation_switch_arm.py::main()` 的买侧逐行对齐，只是数据换成沙箱 + as-of）：
  ① 卖侧不动（本轮只做买侧；卖侧 leg 在盘中/次日由成交与止损链处理）
  ② `wave_state.operation` → wop；`rotation_universe_result` → crowded/room/holdT/healthy/sucking
  ③ 方向层池（`mainline_today` = 沙箱 `wolf_mainline_select.json`）→ 主题可买门（结构 ∧ 资金）
  ④ 买链 = `room + holdT` 过"主线性"资格（`MAINLINE_QUALIFY`）→ 路径A `pick_buy`（≤2 链 × ≤3 只）
  ⑤ 确认池主题 → 路径B `confirm_pick`（`pick_v2`：组内前2 ∩ 容量 ∩ LOW/MID ∩ v3 排序）≤ `ROT_POOL_LEGS`
  ⑥ 板块前过滤（`WOLF_PICK_BOARD_EXCLUDE`）→ **两道入口门**（`wolf_entry_filters.filter_legs`，分位按候选域）
  ⑦ 每条腿：253（custom_m5dump）+ 254（custom_prevlow；`WOLF_MA_LINE_ENTRY=1` 时挂最近均线价）

数据层：`bt_daily_cache.Bars`（本地 SQLite，强制 ≤ cut）+ ETF/指数走 relay 兜底（同样按日期截断）。

用法：
  python jobs/bt_day_legs.py --date 20260911                      # 持仓用 --held 或 --held-from-db
      --held "SZ002409,SH588170,SH512480,SH603259" | --held-from-db
      [--sandbox /app/data/_bt_full/20260911] [--out <sb>/legs.jsonl]
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time

sys.path[:0] = []
import bt_env  # noqa: E402
bt_env.add_paths()


class GzShim:
    """生产 `rotation_switch_arm._gz` 的替身：daily/daily_basic 走本地 SQLite；其余走 relay 并截断 ≤ as_of。"""

    def __init__(self, bars_db: str, as_of: str):
        self.bars_db = bars_db
        self.as_of = str(as_of)
        self.n_local = self.n_remote = 0
        # 真 relay 的原函数（打了 relay shim 后 module 属性已换，必须直接拿）
        import tushare_relay as _tr
        self._real = _tr.relay_items
    
    def _rows_from_sqlite(self, ts_code, cols, start, end):
        import sqlite3
        e = min(str(end or self.as_of), self.as_of)
        c = sqlite3.connect(self.bars_db)
        try:
            cur = c.execute("SELECT %s FROM bars WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date"
                            % ",".join(cols), (str(ts_code), str(start), e))
            return [tuple(r) for r in cur.fetchall()]
        finally:
            c.close()

    def __call__(self, api, params, fields):
        p = dict(params or {})
        f = [x.strip() for x in str(fields or "").split(",") if x.strip()]
        if api == "daily" and p.get("ts_code"):
            cols = [c for c in f if c in ("ts_code", "trade_date", "open", "high", "low", "close",
                                          "pre_close", "pct_chg", "vol", "amount", "total_mv", "turnover_rate")]
            if not cols:
                cols = ["ts_code", "trade_date", "close", "amount", "low", "high"]
            self.n_local += 1
            return self._rows_from_sqlite(p["ts_code"], cols, p.get("start_date") or "19000101",
                                          p.get("end_date") or self.as_of)
        if api == "daily_basic" and p.get("trade_date"):
            d = min(str(p["trade_date"]), self.as_of)
            import sqlite3
            c = sqlite3.connect(self.bars_db)
            try:
                rows = c.execute("SELECT ts_code,total_mv FROM bars WHERE trade_date=? AND total_mv IS NOT NULL",
                                 (d,)).fetchall()
            finally:
                c.close()
            self.n_local += 1
            return [tuple(r) for r in rows]
        # 其余（ETF 日线 / 指数 / 资金流…）：真 relay + 按日期字段丢掉 as_of 之后的行
        try:
            flds, items = self._real(api, fields=fields, **p)
        except Exception as e:
            print("[legs] relay %s 失败 %s" % (api, str(e)[:70]), file=sys.stderr)
            return []
        self.n_remote += 1
        idx = None
        for cand in ("trade_date", "date", "trade_time", "end_date"):
            if cand in (flds or []):
                idx = flds.index(cand); break
        if idx is not None and items:
            items = [r for r in items
                     if not (len(str(r[idx]).replace("-", "")[:8]) == 8 and str(r[idx]).replace("-", "")[:8].isdigit()
                             and str(r[idx]).replace("-", "")[:8] > self.as_of)]
        return items


def held_from_db(cut: str, account: str = "stock"):
    """从生产 `paper_trades`（≤ cut）重建持仓 —— 校验窗口用它，保证与生产当时的持仓一致。"""
    import psycopg2
    url = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
    c = psycopg2.connect(url); cur = c.cursor()
    # 注意：原实现用 `LIKE '买%'`，psycopg2 会把那个 `%` 当占位符（实测 IndexError）→ 改用 `= '买入'`
    cur.execute("""SELECT symbol,
                          SUM(CASE WHEN direction = '买入' THEN volume ELSE -volume END) AS v
                   FROM paper_trades
                   WHERE account_id=%s AND COALESCE(voided,0)=0 AND created_at < %s
                   GROUP BY symbol
                   HAVING SUM(CASE WHEN direction = '买入' THEN volume ELSE -volume END) > 0""",
                (account, "%s-%s-%sT00:00:00" % (cut[:4], cut[4:6], cut[6:8])))
    out = sorted(r[0] for r in cur.fetchall())
    cur.close(); c.close()
    return out



def task_enabled(code_dir: str, task_id: str):
    """该日版本树的 `config/tasks.yaml` 里这条任务是否存在且 `enabled: true`。

    ⚠️ 为什么必须查：**规则买入链是 2026-09-03 才上线的**（`rotation_switch_arm` 09-03、
    `tranche_ladder_report` 09-07）。不查就会出现"用未来才有的链在 1-8 月布腿"的**时代错位**
    （实测 09-01 回放布出 5 条腿，而生产当天根本没有这条链 → 假阳性污染对账）。
    返回 (ok, reason)。
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
    root = os.path.join(os.environ.get("DATA_DIR", bt_env.DATA), "_bt_code")
    try:
        m = json.load(open(os.path.join(root, "rev_map.json"), encoding="utf-8"))
    except Exception:
        return ""
    rev = ((m.get(date8) or {}).get("rev")) or ""
    d = os.path.join(root, "rev_%s" % rev) if rev else ""
    return d if d and os.path.isdir(d) else ""

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="决策日 T（如 20260911）")
    ap.add_argument("--cut", default="", help="数据切点（默认 = 沙箱 _seed.json 里的 cut）")
    ap.add_argument("--sandbox", default="")
    ap.add_argument("--bars-db", default=os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite"))
    ap.add_argument("--held", default="", help="持仓（逗号分隔，作为 exclude）")
    ap.add_argument("--held-from-db", action="store_true", help="用 paper_trades(≤cut) 重建持仓")
    ap.add_argument("--code-dir", default="", help="该日的代码版本树（默认按 data/_bt_code/rev_map.json 自动解析）")
    ap.add_argument("--out", default="")
    ap.add_argument("--require-task", default="rotation_switch_arm",
                    help="该日版本树里必须启用的任务 id（时代检查；空字符串=不检查）")
    a = ap.parse_args()

    sb = a.sandbox or os.path.join(os.environ.get("DATA_DIR", bt_env.DATA), "_bt_full", a.date)
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
    cut = a.cut or seed.get("cut")
    if not cut:
        print("需要 --cut 或沙箱里有 _seed.json", file=sys.stderr); return 2
    os.environ["DATA_DIR"] = sb

    # 钉时钟（含 time.strftime/localtime）+ 钉取数：否则 09-11 的代码会 glob 到"最新那天的 gate/文件"
    try:
        bt_env.add_paths()
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
    arm = importlib.import_module("rotation_switch_arm")
    # ⚠️ 模块来源校验：必须来自**当日版本树**，否则就是"今天的代码在跑历史"
    if _code:
        _af = os.path.abspath(getattr(arm, "__file__", "") or "")
        _exp = os.path.abspath(os.path.join(_code, "jobs"))
        if _af and not _af.startswith(_exp):
            print("ABORT_MODULE_PROVENANCE rotation_switch_arm=%s 期望在 %s 内" % (_af, _exp), flush=True)
            return 5
    if a.require_task:
        ok, why = task_enabled(_code, a.require_task)
        if not ok:
            print("SKIP_TASK_NOT_AVAILABLE %s %s（该日生产根本没有这条链 → 本路径当日应为 0 条腿）"
                  % (a.require_task, why), flush=True)
            return 0
    arm.DATA = sb
    arm._today = lambda: str(cut)
    shim = GzShim(a.bars_db, cut)
    arm._gz = shim
    # 资金门（P2-2）与主题门（P1）都要 as-of 且**不能发通知**：
    #   · `theme_nets(theme, as_of=None, days=…)` 返回 **List[float]**（不是 dict！），
    #     正确做法是**强制传 as_of=cut**，而不是过滤返回值（第一版按 dict 过滤 → 返回 0 长度
    #     → 资金门 fail-closed → 农业/金融/医药 全被"停止买入"，还顺手发了 QQ 通知）；
    #   · 回测跑一年会触发成百上千次告警 → 必须把通知打成空操作（否则刷屏）。
    try:
        import wolf_theme_vol_fund as _WVF
        _orig_nets = getattr(_WVF, "theme_nets", None)
        if _orig_nets:
            def _nets_cut(theme, as_of=None, *a, **kw):
                return _orig_nets(theme, as_of=cut, *a, **kw)
            _WVF.theme_nets = _nets_cut
        _WVF.notify_once = lambda *a, **kw: False
        _WVF._send_qq = lambda *a, **kw: False
        import wolf_context as _WC
        if getattr(_WC, "theme_nets", None) is not None:
            _WC.theme_nets = _WVF.theme_nets
        if getattr(_WC, "notify_once", None) is not None:
            _WC.notify_once = lambda *a, **kw: False
        print("[legs] 资金门已 as-of(cut=%s) 且通知置空" % cut, flush=True)
    except Exception as e:
        print("[legs] 资金门 as-of 打桩失败: %s" % str(e)[:90], file=sys.stderr)

    held = set()
    if a.held_from_db:
        held = set(held_from_db(cut))
    elif a.held:
        held = {x.strip() for x in a.held.split(",") if x.strip()}

    t0 = time.time()
    wave = arm.load("wave_state.json"); ru = arm.load("rotation_universe_result.json")
    ml = arm.load("main_line_state.json")
    wop = str(wave.get("operation") or "side").lower()
    crowded = ru.get("crowded_top") or []; room = ru.get("room_bottom") or []
    holdT = ru.get("holdT_top") or []
    healthy = bool(ru.get("rotation_healthy")); sucking = bool(ru.get("mainline_sucking"))
    print("[legs] T=%s cut=%s wop=%s room=%s holdT=%s healthy=%s sucking=%s held=%d"
          % (a.date, cut, wop, room, holdT, healthy, sucking, len(held)), flush=True)

    # ⚠️ 资格集合的取法必须**随版本自适应**：09-13 之前是 `gate_confirmed_today()`（gate 的
    #    confirmed_candidate），09-13 起才是 `mainline_today()`（方向层池）。写死任何一个都会错版本：
    #    实测 09-11 用新版函数 → 拿到 13 个主题 → 免税/Kimi 两条链被放行（生产当天其实一条没布）。
    _f_sel = getattr(arm, "mainline_today", None) or getattr(arm, "gate_confirmed_today", None)
    confirmed_today_set = set()
    if callable(_f_sel):
        try:
            confirmed_today_set = set(_f_sel(cut) or ())
        except Exception as _se:
            print("[legs] 资格集合取数失败 %s: %s" % (getattr(_f_sel, "__name__", "?"), str(_se)[:90]), flush=True)
    print("[legs] 资格集合(%s)=%s" % (getattr(_f_sel, "__name__", "?"), sorted(confirmed_today_set)), flush=True)
    print("[legs] module=%s" % getattr(arm, "__file__", "?"), flush=True)
    # `theme_of_chain` 是**版本相关**函数（早期版本树没有）→ 取一次、后面都用它（容错）
    _toc = getattr(arm, "theme_of_chain", None)
    # ⚠️ `theme_of_chain` 是**版本相关**函数：早期版本树里没有它（实测 rev_5a6327… 报
    #    AttributeError → 整个 09-10 布腿路径崩掉、静默变成 0 条腿）。诊断打印必须容错。
    for c in list(room) + list(holdT):
        print("   链 %s → theme=%s" % (c, _toc(c) if callable(_toc) else "(该版本无 theme_of_chain)"),
              flush=True)

    buy_chains = []
    qualify = os.getenv("MAINLINE_QUALIFY", "1").strip() in ("1", "true", "yes")
    for c in room + holdT:
        old_is_main = any(t in c or c in t for x in [str(ml.get("main_line") or ""),
                                                     " ".join(str(v) for v in (ml.get("candidates") or []))]
                          for t in x.split("/"))
        is_main, skip_reason = old_is_main, None
        # ⚠️ `theme_of_chain` 是**版本相关**函数：早期版本树（如 rev_5a6327…）没有它 →
        #    早期版本的主线性判定就走"按 main_line/candidates 文本匹配"（`old_is_main`）。
        #    这里必须容错，否则该日整条布腿路径 AttributeError 崩掉、被误读成"生产当天 0 条腿"。
        _th0 = _toc(c) if callable(_toc) else None
        if qualify and _th0 is not None:
            if _th0 in confirmed_today_set:
                is_main = True
            else:
                is_main = False; skip_reason = "not_today_confirmed:" + _th0
        if is_main:
            buy_chains.append((c, "mainline"))
        elif skip_reason:
            print("[legs] SKIP_MAINLINE_LOWBUY %s %s" % (c, skip_reason), flush=True)
        elif wop in ("t_only", "side", "defense", "exit") and healthy and not sucking:
            buy_chains.append((c, "defensive_resource"))
    print("[legs] 买链=%s" % buy_chains, flush=True)

    dom, legs_out = [], []
    for chain, side in buy_chains[:2]:
        try:
            picks = arm.pick_buy(chain, exclude=held, limit=3, domain_out=dom)
        except Exception as e:
            print("[legs] pick_buy %s ERR %s" % (chain, str(e)[:120]), flush=True); picks = []
        for p in picks:
            legs_out.append({"symbol": p["symbol"], "chain": chain, "side": side, "theme": arm.theme_of_chain(chain),
                             "src": "pathA", "position": p.get("position"), "leader": p.get("leader")})
        print("[legs] pathA %-12s → %s" % (chain, [p["symbol"] for p in picks]), flush=True)

    pool = [t for t in confirmed_today_set if t != "银行"]
    _gate_blocked = []
    if pool:
        try:
            from wolf_context import theme_buyable
            ok_pool = []
            for th in pool:
                ok, why = theme_buyable(th)
                if ok:
                    ok_pool.append(th)
                else:
                    print("[legs] SKIP_THEME_NOT_BUYABLE %s %s" % (th, str(why)[:110]), flush=True)
                    _gate_blocked.append({"theme": th, "why": str(why)[:200]})
            pool = ok_pool
        except Exception as e:
            print("[legs] THEME_BUYABLE_ERR(放行) %s" % str(e)[:110], flush=True)
    if qualify and pool:
        pool_legs = int(os.getenv("ROT_POOL_LEGS", "4"))
        got = 0
        for th in pool:
            if got >= pool_legs:
                break
            try:
                _pick = arm.confirm_pick(th, exclude=held, limit=pool_legs - got, domain_out=dom)
            except Exception as e:
                print("[legs] confirm_pick %s ERR %s" % (th, str(e)[:110]), flush=True); _pick = []
            for cand in _pick:
                if got >= pool_legs:
                    break
                legs_out.append({"symbol": cand["symbol"], "chain": th, "side": "mainline_confirmed", "theme": th,
                                 "src": cand.get("pick_source") or "v2", "tier": cand.get("tier")})
                got += 1
            print("[legs] pathB %-12s → %s" % (th, [c["symbol"] for c in _pick]), flush=True)

    # 板块前过滤（与生产同一函数）
    _nb = len(legs_out)
    legs_out = [l for l in legs_out if arm.board_ok(l["symbol"])]
    if len(legs_out) != _nb:
        print("[legs] BOARD_FILTER 去掉 %d 条" % (_nb - len(legs_out)), flush=True)
    # 两道入口门（生产同一函数；分位按候选域）
    try:
        import wolf_entry_filters as EF
        kept, blocked = EF.filter_legs(legs_out, cut, domain=dom)
        if blocked:
            print("[legs] ENTRY_FILTER 拦 %d/%d: %s" % (len(blocked), len(legs_out),
                                                        [(b["symbol"], b["why"][:60]) for b in blocked]), flush=True)
        legs_out = kept
    except Exception as e:
        print("[legs] entry_filters ERR %s" % str(e)[:110], flush=True)

    out = a.out or os.path.join(sb, "legs.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for l in legs_out:
            f.write(json.dumps(dict(l, date=a.date, cut=cut), ensure_ascii=False) + "\n")
    print("[legs] 写出 %s：%d 条腿 %s | shim local=%d remote=%d | %.0fs"
          % (out, len(legs_out), [l["symbol"] for l in legs_out], shim.n_local, shim.n_remote, time.time() - t0),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
