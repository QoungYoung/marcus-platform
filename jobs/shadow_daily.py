# -*- coding: utf-8 -*-
"""shadow_daily.py — 「对齐后 vs 现状」影子日报（2026-09-15 round 5）。

把**全部影子项**汇总成一页，供用户切换开关前评估（**只记录，不改变任何决策**）：

| 影子项 | 会说清什么 | 产物 |
|---|---|---|
| ① v3 排序 | v3 会选哪只 vs 现行 leader 实际选哪只 | `data/rank_v3_<as_of>.json` |
| P1 选板块第一要素 | 会拦哪些主题、会放行哪些 | `data/theme_volfund_shadow_<date>.json` |
| C1 254 容差 | 按语料值(tol=0)会**少成交**哪些票、价差多少 | `data/dip_tol_shadow_<date>.json` |
| C3 仓位档 | 当前**总仓位**会超过他的话（75/50/30/0）多少 | `data/pos_cap_shadow_<date>.json`（本脚本产出） |
| C4 ETF 波动 | 候选/持仓 ETF 的 20 日日均振幅是否 ≥3% | `data/etf_vol_shadow_<date>.json`（本脚本产出） |

用法（生产容器内或宿主）：
  python jobs/shadow_daily.py [--data DIR] [--date YYYYMMDD] [--no-remote]
输出：控制台一页 + `data/shadow_daily_<date>.json`
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_TOTAL_CAP = {"build": 75, "t_only": 50, "side": 50, "defense": 30, "exit": 0}   # 2026-01-17 他的话


def _j(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _pg_positions():
    """(总持仓市值, 净值) —— 只读 PG；失败 → (None, None)。"""
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL",
                                          "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(volume * avg_price),0) FROM paper_positions "
                    "WHERE account_id='stock' AND volume>0")
        pos_val = float((cur.fetchone() or [0])[0] or 0)
        cur.execute("SELECT COALESCE(SUM(amount),0) FROM paper_capital_adjustments WHERE account_id='stock'")
        cap = float((cur.fetchone() or [0])[0] or 0)
        if cap <= 0:
            cur.execute("SELECT COALESCE(initial_capital,0) FROM paper_accounts WHERE account_id='stock'")
            cap = float((cur.fetchone() or [0])[0] or 0)
        cur.close(); conn.close()
        return pos_val, cap
    except Exception as e:
        print("[shadow_daily] PG 读取失败: %s" % str(e)[:80])
        return None, None


def c3_position_cap(data_dir, date8):
    """C3：当前总仓位 vs 他 2026-01-17 的分档上限（75/50/30/0）。"""
    wave = _j(os.path.join(data_dir, "wave_state.json"), {}) or {}
    op = str(wave.get("operation") or "side").lower()
    cap = CORPUS_TOTAL_CAP.get(op, 50)
    pos_val, net = _pg_positions()
    out = {"date": date8, "operation": op, "corpus_total_cap_pct": cap,
           "quote": "2026-01-17「主升趋势就75%以上…调整就50% 有风险就30% 下跌趋势就不做」"}
    if pos_val is None or not net:
        out["note"] = "取数失败 → 只记录档位上限"
        return out
    pct = round(pos_val / net * 100.0, 2) if net else None
    out.update({"position_value": round(pos_val, 2), "net_asset": round(net, 2), "position_pct": pct,
                "would_exceed": bool(pct is not None and pct > cap),
                "gap_pp": (round(pct - cap, 2) if pct is not None else None)})
    return out


def c4_etf_vol(data_dir, date8):
    """C4：候选/持仓 ETF 的 20 日日均振幅是否 ≥3%（他的话 2026-08-21）。"""
    # 容器里 backend/app 挂在 /app/app（宿主是 <repo>/backend/app）→ 两种布局都要能导入
    for _p in (os.path.join(ROOT, "backend"), ROOT, os.path.join(ROOT, "app"), os.path.dirname(ROOT)):
        if _p and os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)
    try:
        from app.services import wolf_etf_vol as V
    except Exception as e:
        return {"date": date8, "note": "wolf_etf_vol 不可用: %s（sys.path 提示：容器里 app 包在 /app/app）"
                                       % str(e)[:60]}
    syms = []
    m = _j(os.path.join(data_dir, "etf_theme_map_pi.json"), {}) or {}
    for t in (m.get("themes") or []):
        for k in ("primary", "secondary"):
            for e in (t.get(k) or []):
                ts = str(e.get("ts_code") or "")
                if ts:
                    syms.append(("SH" if ts.endswith(".SH") else "SZ") + ts[:6])
    # 持仓里的 ETF 也纳入
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL",
                                          "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT symbol FROM paper_positions WHERE account_id='stock' AND volume>0")
        syms += [str(r[0]) for r in cur.fetchall()]
        cur.close(); conn.close()
    except Exception:
        pass
    syms = sorted({s for s in syms if V.is_etf(s)})
    rows = {}
    for s in syms[:20]:
        ok, why, d = V.check(V.etf_daily(s))
        rows[s] = {"pass": bool(ok), "amp": (d or {}).get("amp"), "why": why[:80]}
    return {"date": date8, "n": len(rows), "pass_n": sum(1 for v in rows.values() if v["pass"]),
            "threshold": V.VOL_THR, "themes": rows,
            "quote": "2026-08-21「选半导体仅仅只是因为他波动大 ETF都有3个点以上的波动」"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", os.path.join(ROOT, "data")))
    ap.add_argument("--date", default="")
    args = ap.parse_args()
    import datetime as dt
    d8 = args.date or dt.date.today().strftime("%Y%m%d")
    data_dir = args.data

    rep = {"date": d8, "note": "影子模式：全部只记录、不改变决策", "items": {}}

    def _latest(pat):
        fs = sorted(glob.glob(os.path.join(data_dir, pat)))
        return fs[-1] if fs else None

    # ① v3
    f = _latest("rank_v3_*.json")
    if f:
        j = _j(f, {}) or {}
        rep["items"]["rank_v3"] = {"file": os.path.basename(f), "themes": {
            th: {"mode": t.get("mode"), "q": t.get("theme_r5_qtile"), "domain_n": t.get("domain_n"),
                 "v3": [x.get("symbol") for x in (t.get("v3") or [])],
                 "leader": [x.get("symbol") for x in (t.get("leader") or [])]}
            for th, t in (j.get("themes") or {}).items()}}
    # P1 门
    f = _latest("theme_volfund_shadow_*.json")
    if f:
        j = _j(f, {}) or {}
        th = j.get("themes") or {}
        rep["items"]["theme_volfund"] = {"file": os.path.basename(f), "n": len(th),
                                         "blocked": {k: v.get("why") for k, v in th.items() if not v.get("pass")},
                                         "allowed": [k for k, v in th.items() if v.get("pass")]}
    # C1
    f = _latest("dip_tol_shadow_*.json")
    if f:
        j = _j(f, {}) or {}
        rep["items"]["dip_tol"] = {"file": os.path.basename(f), "n": len(j.get("items") or {}),
                                   "items": list((j.get("items") or {}).items())[:10]}
    # F2 选股域⊆执行域（账户板块权限）
    f = _latest("board_prefilter_shadow_*.json")
    if f:
        j = _j(f, {}) or {}
        rep["items"]["board_prefilter"] = {"file": os.path.basename(f),
                                           "chains": j.get("chains") or {}}
    # 144 线大级别资格（影子；含当日其它门状态，供增量对照）
    f = _latest("ma144_shadow_*.json")
    if f:
        j = _j(f, {}) or {}
        rep["items"]["ma144"] = {"file": os.path.basename(f), "state": j.get("state") or {},
                                 "extra": j.get("extra") or {}}
    # ⑫ 白线在上 ∧ 缩量（round 40 补；门已正式开启 → 每日必看）
    f = _latest("line_regime_shadow_*.json")
    if f:
        j = _j(f, {}) or {}
        rep["items"]["line_regime"] = {"file": os.path.basename(f), "state": j.get("state") or {},
                                       "extra": j.get("extra") or {}}
    # ⑬ 大盘级量能档位（1WE/1.5WE/2WE）
    f = _latest("market_vol_shadow_*.json")
    if f:
        j = _j(f, {}) or {}
        rep["items"]["market_vol"] = {"file": os.path.basename(f), "state": j.get("state") or {},
                                      "extra": j.get("extra") or {}}
    # ⑭ 分步回补（缩量条件）
    f = _latest("step_refill_shadow_*.json")
    if f:
        j = _j(f, {}) or {}
        rep["items"]["step_refill"] = {"file": os.path.basename(f), "gate": j.get("gate"),
                                       "state": j.get("state") or {},
                                       "candidates": (j.get("extra") or {}).get("candidates") or []}
    # ⑮ 均线挂单（13/34/60/144）：各链每条腿挂在哪条线上
    f = _latest("ma_line_shadow_*.json")
    if f:
        j = _j(f, {}) or {}
        chains = j.get("chains") or {}
        rep["items"]["ma_line"] = {"file": os.path.basename(f), "lines": j.get("lines"),
                                   "chains": {th: {"legs": [{"symbol": l.get("symbol"),
                                                             "k": l.get("ma_line_k"),
                                                             "dist_pct": l.get("ma_line_dist_pct")}
                                                            for l in (v.get("legs") or [])]}
                                              for th, v in chains.items()}}
    # ⑯ 资金门告警（fail-closed 触发记录；2026-09-15 上线）
    al = _latest("fund_gate_alerts_*.json")
    if al:
        j = _j(al, {}) or {}
        rep["items"]["fund_gate_alerts"] = {"file": os.path.basename(al), "n": len(j),
                                            "items": [{"key": k, "msg": (v or {}).get("msg")}
                                                      for k, v in list(j.items())[:10]]}
    # C3 / C4（本脚本现算）
    rep["items"]["pos_cap"] = c3_position_cap(data_dir, d8)
    rep["items"]["etf_vol"] = c4_etf_vol(data_dir, d8)

    try:
        out = os.path.join(data_dir, "shadow_daily_%s.json" % d8)
        with open(out, "w", encoding="utf-8") as f2:
            json.dump(rep, f2, ensure_ascii=False, indent=1, default=str)
    except Exception as e:
        print("[shadow_daily] 写盘失败: %s" % str(e)[:80])

    # ── 一页输出 ──
    print("=" * 84)
    print("影子日报 %s（**只记录不改变决策**）" % d8)
    print("=" * 84)
    it = rep["items"]
    if "rank_v3" in it:
        print("\n① v3 排序：v3 会选什么 vs 现行 leader 实际选什么")
        for th, v in it["rank_v3"]["themes"].items():
            mark = "相同" if set(v["v3"]) == set(v["leader"]) else "**不同**"
            print("   %-12s mode=%-6s 主题分位=%-6s 域=%-3s v3=%-16s leader=%-16s %s"
                  % (th, v["mode"], v["q"], v["domain_n"], ",".join(v["v3"]) or "—",
                     ",".join(v["leader"]) or "—", mark))
    else:
        print("\n① v3 排序：（暂无影子文件）")
    if "theme_volfund" in it:
        g = it["theme_volfund"]
        print("\nP1 选板块第一要素：检查 %d 主题，会拦 %d，放行 %d"
              % (g["n"], len(g["blocked"]), len(g["allowed"])))
        for k, why in g["blocked"].items():
            print("   ✋ %-14s %s" % (k, str(why)[:80]))
        if g["allowed"]:
            print("   ✅ 放行：%s" % ", ".join(g["allowed"]))
    else:
        print("\nP1 选板块第一要素：（暂无影子文件）")
    if "dip_tol" in it:
        c = it["dip_tol"]
        print("\nC1 254 容差：按语料值(tol=0)会少成交 %d 只" % c["n"])
        for sym, v in c["items"][:6]:
            print("   %-10s 前低=%-8s 今低=%-8s 差=%s%%" % (sym, v.get("prev_low"), v.get("today_low"),
                                                          v.get("gap_pct")))
    else:
        print("\nC1 254 容差：（暂无影子文件 —— 需部署含影子的 t_monitor）")
    if "board_prefilter" in it:
        b = it["board_prefilter"]
        print("\nF2 选股域⊆执行域（账户无创业板/科创板权限）：会白丢几条腿")
        for ch, v in (b["chains"] or {}).items():
            print("   %-14s 现选=%s → 剔后=%s（白丢 %s）"
                  % (ch, ",".join(v.get("current") or []) or "—",
                     ",".join(v.get("with_prefilter") or []) or "—",
                     ",".join(v.get("dropped") or []) or "—"))
    else:
        print("\nF2 选股域⊆执行域：（当日无差异记录 —— 或选出的票本就都在可交易板块）")
    if "ma144" in it:
        m144 = it["ma144"]
        st = m144["state"] or {}
        print("\n⑨ 144 线资格（影子，默认只记录）：指数 %s 收盘 %s vs MA144 %s（%s）| 斜率(%s日) %s%% → %s"
              % ("000001.SH", st.get("close"), st.get("ma144"),
                 "在上方" if st.get("above") else "在下方", st.get("slope_win"), st.get("slope_pct"),
                 "放行" if st.get("allow_slope") else "会拦"))
        _ex = m144["extra"] or {}
        print("   当日其它门：wave_op=%s gate_blocked=%s；当日买腿 %s（选股原始 %s 条）"
              % (_ex.get("wave_op"), _ex.get("gate_blocked"), _ex.get("buy_legs"), _ex.get("raw_legs_n")))
    else:
        print("\n⑨ 144 线资格：（暂无影子文件）")
    p = it["pos_cap"]
    print("\nC3 仓位档（他的话 75/50/30/0）：operation=%s → 上限 %s%%；当前仓位 %s%% %s"
          % (p.get("operation"), p.get("corpus_total_cap_pct"), p.get("position_pct"),
             ("**超上限 %.2fpp**" % p["gap_pp"]) if p.get("would_exceed") else ("（未超，差 %s pp）" % p.get("gap_pp")
                                                                          if p.get("gap_pp") is not None else "")))
    e = it["etf_vol"]
    print("\nC4 ETF 波动（他的话 ≥3%%）：检查 %s 只，达标 %s 只" % (e.get("n"), e.get("pass_n")))
    for s, v in list((e.get("themes") or {}).items())[:8]:
        print("   %-10s 振幅=%-6s %s" % (s, v.get("amp"), "✅" if v.get("pass") else "✋"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
