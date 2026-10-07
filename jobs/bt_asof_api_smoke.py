#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_asof_api_smoke.py — `jobs/bt_asof_api.py` 的自测（启动真服务 → 打端点 → 断言，退出码 0/1）。

覆盖（每条都打印 PASS/FAIL + 实测值）：
  1. **启动契约**：`python jobs/bt_asof_api.py --port 0 …` 能起、打印 LISTENING、`--print-paths` 的默认路径
     含 `data/_bt_year/<YYYYMMDD>/asof_api/` 与 `.dsh-tmp/wolfbt/asof_api_requests.jsonl`；
     **非回环 DSN / 非回环 host 必须拒绝启动**（生产隔离的 fail-closed 证据）。
  2. **P1 端点**：/health、/prompts、/t/fields、/portfolio/positions、/market/quote、/market/kline、
     /market/market-state、/t/conditions(GET+POST)、/t/ai/actions、/t/build/no-rebuild。
  3. **as-of 取值正确**：quote.current == 本地 1min 在 as-of bar 的收盘，且 **!= 当日日线收盘**（证明没有前视）；
     kline 最新一根 == as-of bar、全部 label ≤ bar；positions 里**不含** as-of 之后才买入的标的。
  4. **fail-closed**：未实现端点 → 501 + `{"error":"backtest as-of API: <path> not implemented"}`；
     状态文件缺失 / 过期 / symbol 当日无本地数据 → 409 + `{"error":"no as-of state"}`；/health 不受影响。
  5. **沙箱写**：POST /t/conditions、POST /t/build/no-rebuild 只落 `<sandbox>/<day>/asof_api/*.jsonl`
     （append-only，带 as-of 时间戳与请求体），且新建条件能立刻在 GET /t/conditions 里看到。
  6. **请求日志**：逐行 JSONL 含 ts/method/path/status/asof/duration_ms，且 **全部行 `upstream_http == 0`**
     （= 生产请求数为 0 的证据）。
  7. **--created-at-policy=trade-date**：另起一个实例，验证放宽口径能取到本地 PG 的历史条件与真市场诊断。

用法：`.venv/bin/python jobs/bt_asof_api_smoke.py [--root .] [--keep]`
自测只写 `.dsh-tmp/wolfbt/asof_smoke/`（自带 state/log/sandbox），**不碰**驱动用的
`.dsh-tmp/wolfbt/asof_state.json`，也**不碰** `data/_bt_year/`（沙箱根用 --sandbox-root 重定向）。
"""
from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request


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


DAY, BAR = "2026-03-05", "10:35"
DAY8 = DAY.replace("-", "")
QUOTE_SYM = "SH600584"          # m1@10:35 = 50.18，当日日线收盘 = 48.70 → 用它证明 as-of 截断
LATE_SYM = "SZ000158"           # 只在 2026-08-27 之后才买入 → as-of 2026-03-05 不应出现
FUTURE_SYM = "SZ999999"         # 本地无任何数据 → 应 409

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" | " + detail) if detail else ""), flush=True)
    return bool(ok)


def note(msg: str) -> None:
    print("NOTE " + msg, flush=True)


# ── HTTP ─────────────────────────────────────────────────────────────────────
def call(base: str, method: str, path: str, body=None, timeout: float = 60.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})

    def _hdr(hs):                       # HTTP 头大小写不敏感（http.server 会规范化成 X-Bt-Asof-Day）
        return {str(k).lower(): v for k, v in dict(hs).items()}

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw), _hdr(r.headers)
            except Exception:
                return r.status, raw, _hdr(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), _hdr(e.headers)
        except Exception:
            return e.code, {"_raw": raw[:200].decode("utf-8", "replace")}, _hdr(e.headers)


class Server:
    """起一个真服务子进程（--port 0 → 从 stdout 的 LISTENING 行拿端口）。"""

    def __init__(self, py: str, script: str, args, tag: str):
        self.tag = tag
        self.log_tail = []
        self.proc = subprocess.Popen([py, script] + [str(a) for a in args], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.port = None
        deadline = time.time() + 45
        while time.time() < deadline:
            r, _, _ = select.select([self.proc.stdout], [], [], 0.5)
            if r:
                line = self.proc.stdout.readline()
                if not line:
                    break
                self.log_tail.append(line.rstrip())
                if "LISTENING http://" in line:
                    self.port = int(line.rstrip().split(":")[-1].split("/")[0])
                    break
            if self.proc.poll() is not None:
                break
        if self.port is None:
            self.log_tail.append("(未拿到 LISTENING，rc=%s)" % self.proc.poll())

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d/api/v1" % (self.port or 0)

    def stop(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
        except Exception as _e_sil1:
            _silent_alert("bt_asof_api_smoke.py:121", _e_sil1)
        try:
            if self.proc.stdout:
                self.proc.stdout.close()
        except Exception as _e_sil2:
            _silent_alert("bt_asof_api_smoke.py:126", _e_sil2)


# ── 期望值（独立于服务，直接读本地原始数据） ─────────────────────────────────
def m1_close(repo: str, sym: str, day8: str, hhmm: str):
    code, ex = sym[2:], sym[:2]
    p = os.path.join(repo, "data", "_bt_full", "mins1", "%s_%s_1min_%s.json" % (code, ex, day8))
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    for b in d.get("bars") or []:
        if str(b[1])[11:16] == hhmm:
            return float(b[5])
    return None


def daily_close(repo: str, sym: str, day8: str):
    db = os.path.join(repo, "data", "_bt_full", "bars.sqlite")
    ts = "%s.%s" % (sym[2:], sym[:2])
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = c.execute("SELECT close FROM bars WHERE ts_code=? AND trade_date=?", (ts, day8)).fetchone()
        c.close()
        return float(row[0]) if row else None
    except Exception:
        return None


def pg_today_symbols(dsn: str, account: str):
    try:
        import psycopg2
        c = psycopg2.connect(dsn, connect_timeout=4)
        cur = c.cursor()
        cur.execute("SELECT symbol FROM paper_positions WHERE account_id=%s", (account,))
        out = {r[0] for r in cur.fetchall()}
        c.close()
        return out
    except Exception:
        return set()


# ── 主流程 ───────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="bt_asof_api.py 自测")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--port", type=int, default=0, help="0 = 让内核挑（默认；避免与在跑的服务撞端口）")
    a = ap.parse_args(argv)
    repo = os.path.abspath(a.root)
    py = sys.executable
    script = os.path.join(repo, "jobs", "bt_asof_api.py")
    smoke = os.path.join(repo, ".dsh-tmp", "wolfbt", "asof_smoke")
    state = os.path.join(smoke, "state.json")
    log = os.path.join(smoke, "requests.jsonl")
    sandbox = os.path.join(smoke, "sandbox")
    print("=" * 100)
    print("bt_asof_api_smoke | python=%s" % py)
    print("repo=%s" % repo)
    print("smoke_dir=%s  (state/log/sandbox 都在这里；不碰驱动用的 asof_state.json 与 data/_bt_year/)" % smoke)
    print("=" * 100, flush=True)

    shutil.rmtree(smoke, ignore_errors=True)
    os.makedirs(smoke, exist_ok=True)
    with open(state, "w", encoding="utf-8") as f:
        json.dump({"run": "smoke", "day": DAY, "bar": BAR, "account": "stock",
                   "session": "t-agent-smoke"}, f)

    # ── 1. 静态契约：--print-paths 的默认路径 ────────────────────────────
    r = subprocess.run([py, script, "--root", repo, "--print-paths"], capture_output=True, text=True, timeout=120)
    sand_line = next((l for l in r.stdout.splitlines() if l.startswith("sandbox")), "")
    check("--print-paths 退出码 0", r.returncode == 0, "rc=%d" % r.returncode)
    check("默认日志 = .dsh-tmp/wolfbt/asof_api_requests.jsonl",
          ".dsh-tmp/wolfbt/asof_api_requests.jsonl" in r.stdout)
    check("默认沙箱 = data/_bt_year/<YYYYMMDD>/asof_api/",
          "data/_bt_year/<YYYYMMDD>/asof_api/" in r.stdout, sand_line[:90])

    # ── 2. 生产隔离的 fail-closed：非回环 DSN / host 拒绝启动 ─────────────
    def must_refuse(args, label):
        try:
            p = subprocess.run([py, script, "--root", repo, "--port", "0"] + args,
                               capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return check(label, False, "60s 未退出 = 竟然启动了")
        out = (p.stderr or "") + (p.stdout or "")
        return check(label, p.returncode != 0 and ("回环" in out), "rc=%d out=%s" % (p.returncode, out.strip()[:90]))

    must_refuse(["--state-file", state, "--log", log,
                 "--dsn", "postgresql://u:p@81.70.44.68:5432/marcus"],
                "非回环 DSN 拒绝启动（生产隔离 fail-closed）")
    must_refuse(["--state-file", state, "--log", log, "--host", "0.0.0.0"],
                "非回环 listen host（0.0.0.0）拒绝启动")

    srv = s2 = None
    try:
        # ── 3. 起服务（strict 口径） ─────────────────────────────────────
        srv = Server(py, script, ["--root", repo, "--port", a.port, "--state-file", state, "--log", log,
                                  "--sandbox-root", sandbox], "strict")
        check("服务启动 (LISTENING)", srv.port is not None,
              "port=%s log=%s" % (srv.port, " / ".join(srv.log_tail[-2:])))
        if srv.port is None:
            return 1
        B = srv.base
        exp_m1 = m1_close(repo, QUOTE_SYM, DAY8, BAR)
        exp_day = daily_close(repo, QUOTE_SYM, DAY8)
        note("独立期望值（直接读本地原始文件）: %s m1@%s = %s，当日日线收盘 = %s" % (QUOTE_SYM, BAR, exp_m1, exp_day))

        # ── 4. /health ──────────────────────────────────────────────────
        s, j, h = call(B, "GET", "/health")
        check("/health 200 + ok", s == 200 and j.get("ok") is True, "status=%s ok=%s" % (s, j.get("ok")))
        check("/health asof 回显 as-of 状态",
              (j.get("asof") or {}).get("day") == DAY8 and (j.get("asof") or {}).get("bar") == BAR,
              json.dumps(j.get("asof"), ensure_ascii=False)[:120])
        check("/health implemented 非空（11 个端点）", len(j.get("implemented") or []) >= 11,
              "n=%d" % len(j.get("implemented") or []))
        check("/health 隔离自述：pg 在回环 + upstream_http=0",
              (j.get("isolation") or {}).get("pg_host") in ("127.0.0.1", "localhost")
              and (j.get("isolation") or {}).get("upstream_http_calls") == 0
              and (j.get("pg") or {}).get("readonly") is True,
              json.dumps(j.get("isolation"), ensure_ascii=False)[:140])

        # ── 5. 快照回放 ────────────────────────────────────────────────
        s, j, h = call(B, "GET", "/prompts")
        check("/prompts 200 + 8 条提示词", s == 200 and isinstance(j, dict) and j.get("count") == 8
              and "CHAT_SYSTEM_PROMPT" in (j.get("prompts") or {}),
              "status=%s count=%s" % (s, (j or {}).get("count") if isinstance(j, dict) else "?"))
        s, j, h = call(B, "GET", "/t/fields")
        check("/t/fields 200 + 79 字段（逐字回放快照）",
              s == 200 and len(j.get("fields") or []) == 79,
              "status=%s fields=%s" % (s, len(j.get("fields") or []) if isinstance(j, dict) else "?"))

        # ── 6. /market/quote（as-of 取值） ──────────────────────────────
        s, j, h = call(B, "GET", "/market/quote/" + QUOTE_SYM)
        got = j.get("current") if isinstance(j, dict) else None
        check("quote 200 + current == 本地1min@as-of bar", s == 200 and exp_m1 is not None and got == exp_m1,
              "status=%s current=%s expected=%s" % (s, got, exp_m1))
        check("quote **不是**当日日线收盘（无前视）",
              exp_day is None or got != exp_day,
              "current=%s 当日日线收盘=%s" % (got, exp_day))
        check("quote 字段对齐插件读取点",
              all(k in (j or {}) for k in ("name", "current", "change", "percent", "last_close", "open",
                                           "high", "low", "volume", "amount", "turnover_rate", "amplitude",
                                           "intraday_percentile")),
              "keys=%s" % sorted((j or {}).keys())[:8])
        check("quote 响应头带 as-of 审计头",
              h.get("x-bt-asof-day") == DAY8 and h.get("x-bt-asof-bar") == BAR,
              "x-bt-asof-day=%s bar=%s source=%s" % (h.get("x-bt-asof-day"), h.get("x-bt-asof-bar"),
                                                     h.get("x-bt-asof-source")))

        # ── 7. /market/kline ────────────────────────────────────────────
        s, j, h = call(B, "GET", "/market/kline/%s?freq=1" % QUOTE_SYM)
        kl = (j or {}).get("klines") or []
        check("kline freq=1 最新一根 == as-of bar（倒序）",
              s == 200 and kl and kl[0].get("time", "")[11:16] == BAR and kl[0].get("close") == exp_m1,
              "status=%s n=%s newest=%s" % (s, len(kl), kl[0].get("time") if kl else None))
        check("kline 全部 label <= as-of bar（无前视）",
              bool(kl) and all(x.get("time", "")[11:16] <= BAR for x in kl),
              "max_label=%s" % max((x.get("time", "")[11:16] for x in kl), default=""))
        check("kline freq=1 根数 == 09:30..10:35 的 66 根", len(kl) == 66, "n=%d" % len(kl))
        s5, j5, _ = call(B, "GET", "/market/kline/%s?freq=5" % QUOTE_SYM)
        kl5 = (j5 or {}).get("klines") or []
        check("kline freq=5 可用且 <= bar",
              s5 == 200 and kl5 and all(x.get("time", "")[11:16] <= BAR for x in kl5)
              and kl5[0].get("trade_date", "")[-4:] == BAR.replace(":", ""),
              "n=%s newest trade_date=%s" % (len(kl5), kl5[0].get("trade_date") if kl5 else None))

        # ── 8. /portfolio/positions（FIFO + as-of 截断） ────────────────
        s, j, h = call(B, "GET", "/portfolio/positions")
        pos = j if isinstance(j, list) else []
        by = {p.get("symbol"): p for p in pos}
        check("positions 200 + 非空数组（生产返回裸数组）", s == 200 and isinstance(j, list) and len(pos) >= 5,
              "status=%s n=%d" % (s, len(pos)))
        check("positions 每项字段齐全（volume/avg_price/current_price/floating_pnl_pct）",
              all(all(k in p for k in ("symbol", "name", "volume", "avg_price", "current_price",
                                       "floating_pnl_pct", "entry_date")) for p in pos),
              "sample=%s" % json.dumps(pos[0], ensure_ascii=False)[:150] if pos else "")
        check("positions 现价 == 本地1min@as-of bar（%s）" % QUOTE_SYM,
              QUOTE_SYM in by and by[QUOTE_SYM].get("current_price") == exp_m1,
              "%s.current_price=%s expect=%s" % (QUOTE_SYM, (by.get(QUOTE_SYM) or {}).get("current_price"), exp_m1))
        check("positions 不含 as-of 之后才买入的标的 %s" % LATE_SYM, LATE_SYM not in by,
              "symbols=%s" % sorted(by)[:12])
        today = pg_today_symbols("postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading", "stock")
        if today:
            check("对照：本地 PG 现持仓含 %s（证明上面不是「本来就空」）" % LATE_SYM, LATE_SYM in today,
                  "pg_now=%s" % sorted(today)[:8])
        else:
            note("本地 PG 直连不可用 → 跳过「现持仓对照」子检查（不影响 as-of 断言）")

        # ── 9. /market/market-state ────────────────────────────────────
        s, j, h = call(B, "GET", "/market/market-state")
        check("market-state 200 + trade_date == as-of day",
              s == 200 and j.get("trade_date") == DAY8,
              "status=%s trade_date=%s state=%s" % (s, j.get("trade_date"), j.get("state")))
        check("market-state strict 口径：不回填行/或给 as-of 说明（不臆造）",
              (j.get("state") != "unknown") or bool(j.get("asof_note")),
              "state=%s asof_note=%s" % (j.get("state"), str(j.get("asof_note"))[:90]))

        # ── 10. /t/conditions GET（strict） ────────────────────────────
        s, j, h = call(B, "GET", "/t/conditions")
        check("conditions GET 200 + conditions 数组",
              s == 200 and isinstance(j.get("conditions"), list),
              "status=%s n=%s" % (s, j.get("count")))
        check("conditions 显式回报被 as-of 排除的行数（不静默空）",
              "excluded_created_after_asof" in (j or {}) and "excluded_not_active" in (j or {}),
              "excluded_created_after_asof=%s excluded_not_active=%s"
              % (j.get("excluded_created_after_asof"), j.get("excluded_not_active")))
        if j.get("excluded_created_after_asof"):
            note("as-of 口径排除了 %d 行（本地 PG 副本里 trade_date=%s 的历史条件 created_at 是后来回填的）"
                 % (j["excluded_created_after_asof"], DAY8))

        # ── 11. POST /t/conditions → 沙箱 ──────────────────────────────
        req_body = {"symbol": QUOTE_SYM, "trigger_kind": "custom",
                    "expression": {"and": [{"field": "quote.current", "op": "<=", "value": 49.0},
                                           {"field": "vol_ratio", "op": ">=", "value": 1.5}]},
                    "stop_loss_price": 47.5, "reason": "smoke：回测沙箱低吸条件（不写生产 PG）"}
        s, j, h = call(B, "POST", "/t/conditions", req_body)
        cid = j.get("condition_id") if isinstance(j, dict) else None
        check("POST /t/conditions 200 + condition_id + expression_summary",
              s == 200 and j.get("success") is True and cid and j.get("expression_summary"),
              "status=%s id=%s summary=%s" % (s, cid, str(j.get("expression_summary"))[:60]))
        cfile = os.path.join(sandbox, DAY8, "asof_api", "conditions.jsonl")
        check("条件落在沙箱 append-only JSONL", os.path.exists(cfile), cfile)
        line = {}
        if os.path.exists(cfile):
            with open(cfile, encoding="utf-8") as f:
                line = json.loads(f.readline() or "{}")
        check("沙箱行含 as-of 时间戳与完整请求体",
              (line.get("asof") or {}).get("day") == DAY8 and (line.get("asof") or {}).get("bar") == BAR
              and (line.get("body") or {}).get("symbol") == QUOTE_SYM,
              json.dumps(line, ensure_ascii=False)[:160])
        s, j, h = call(B, "GET", "/t/conditions")
        ids = [c.get("id") for c in (j.get("conditions") or [])]
        check("GET /t/conditions 立刻可见沙箱新条件（合并）", cid in ids, "sandbox=%s ids=%s"
              % (j.get("sandbox_conditions"), ids[:5]))

        # ── 12. POST /t/build/no-rebuild → 沙箱 ────────────────────────
        s, j, h = call(B, "POST", "/t/build/no-rebuild", {"action": "add", "symbol": QUOTE_SYM})
        check("no-rebuild add 200 + symbols 含标的",
              s == 200 and QUOTE_SYM in (j.get("symbols") or []),
              "status=%s symbols=%s ignored=%s" % (s, j.get("symbols"), j.get("ignored")))
        s2b, j2b, _ = call(B, "POST", "/t/build/no-rebuild", {"action": "list"})
        check("no-rebuild list 从沙箱日志重放出同一名单", s2b == 200 and QUOTE_SYM in (j2b.get("symbols") or []),
              "symbols=%s" % j2b.get("symbols"))
        check("no-rebuild 落沙箱",
              os.path.exists(os.path.join(sandbox, DAY8, "asof_api", "no_rebuild.jsonl")))

        # ── 13. /t/ai/actions ──────────────────────────────────────────
        s, j, h = call(B, "GET", "/t/ai/actions?limit=5")
        check("ai/actions 200 + actions 数组 + 覆盖区间说明",
              s == 200 and isinstance(j.get("actions"), list) and j.get("pg_coverage") is not None,
              "status=%s n=%s coverage=%s" % (s, j.get("count"), json.dumps(j.get("pg_coverage"), ensure_ascii=False)[:80]))

        # ── 14. 501：未实现端点（fail-closed） ─────────────────────────
        for method, path in [("GET", "/market/moneyflow/" + QUOTE_SYM),
                             ("GET", "/market/technical/" + QUOTE_SYM),
                             ("GET", "/indicator/realtime/" + QUOTE_SYM),
                             ("GET", "/t/build/candidates?source=pool&limit=5"),
                             ("GET", "/t/build/overview"),
                             ("GET", "/etf/kline/SH510300"),
                             ("GET", "/api/v1/definitely/not/a/path".replace("/api/v1", "")),
                             ("POST", "/t/build/position"),
                             ("POST", "/t/build/auto-gen"),
                             ("POST", "/t/build/rebalance"),
                             ("POST", "/trades"),
                             ("POST", "/t/backtest")]:
            s, j, h = call(B, method, path, {} if method == "POST" else None)
            want = "backtest as-of API: /api/v1%s not implemented" % path.split("?")[0]
            check("501 %s %s" % (method, path.split("?")[0]),
                  s == 501 and (j or {}).get("error") == want and bool(j.get("reason")),
                  "status=%s error=%s reason=%s" % (s, str((j or {}).get("error"))[:70], str(j.get("reason"))[:50]))

        # ── 15. 409：as-of 状态不可用（缺失/过期/数据日不符） ───────────
        s, j, h = call(B, "GET", "/market/quote/" + FUTURE_SYM)
        check("409 本地无该 symbol 当日数据（day 与数据日不符）",
              s == 409 and (j or {}).get("error") == "no as-of state",
              "status=%s detail=%s" % (s, str((j or {}).get("detail"))[:80]))
        os.rename(state, state + ".bak")
        s, j, h = call(B, "GET", "/market/quote/" + QUOTE_SYM)
        check("409 状态文件缺失", s == 409 and (j or {}).get("error") == "no as-of state",
              "status=%s detail=%s" % (s, str((j or {}).get("detail"))[:70]))
        s, j, h = call(B, "GET", "/health")
        check("状态缺失时 /health 仍 200（服务活着，只拒数据端点）",
              s == 200 and j.get("ok") is True and (j.get("asof_state") or {}).get("valid") is False,
              "status=%s asof=%s valid=%s" % (s, j.get("asof"), (j.get("asof_state") or {}).get("valid")))
        os.rename(state + ".bak", state)
        s, j, h = call(B, "GET", "/market/quote/" + QUOTE_SYM)
        check("状态恢复后立刻可用（按 mtime 失效缓存，无粘滞）", s == 200 and j.get("current") == exp_m1,
              "status=%s current=%s" % (s, j.get("current")))
        st = os.stat(state)
        os.utime(state, (st.st_atime, st.st_mtime - 7200))
        s, j, h = call(B, "GET", "/market/quote/" + QUOTE_SYM)
        check("409 状态文件过期（mtime 超 --state-max-age）",
              s == 409 and (j or {}).get("error") == "no as-of state" and "过期" in str((j or {}).get("detail")),
              "status=%s detail=%s" % (s, str((j or {}).get("detail"))[:70]))
        os.utime(state, (st.st_atime, st.st_mtime))

        # ── 15b. as-of 引擎自洽：同一 day 换 bar → 结果随 as-of 变 ───────
        def _write_state(bar: str) -> None:
            with open(state, "w", encoding="utf-8") as f:
                json.dump({"run": "smoke", "day": DAY, "bar": bar, "account": "stock",
                           "session": "t-agent-smoke"}, f)

        _write_state("15:00")
        s, j, h = call(B, "GET", "/market/quote/" + QUOTE_SYM)
        check("同一 day 换 bar=15:00 → current == 当日日线收盘（as-of 放开到收盘）",
              s == 200 and exp_day is not None and j.get("current") == exp_day,
              "status=%s current=%s 当日日线收盘=%s" % (s, j.get("current"), exp_day))
        s, j, h = call(B, "GET", "/market/kline/%s?freq=1" % QUOTE_SYM)
        check("bar=15:00 时 freq=1 给全天 241 根", s == 200 and (j.get("count") or 0) == 241,
              "count=%s" % j.get("count"))
        _write_state(BAR)
        s, j, h = call(B, "GET", "/market/quote/" + QUOTE_SYM)
        check("改回 bar=%s → 又回到 as-of 值（按 mtime 失效，无粘滞）" % BAR,
              s == 200 and j.get("current") == exp_m1, "current=%s" % j.get("current"))

        # ── 16. 400：写端点的入参校验（不静默接受） ────────────────────
        s, j, h = call(B, "POST", "/t/conditions", {"trigger_kind": "custom"})
        check("POST /t/conditions 缺 symbol → 400", s == 400, "status=%s error=%s" % (s, str(j.get("error"))[:40]))
        s, j, h = call(B, "POST", "/t/conditions",
                       {"symbol": QUOTE_SYM, "trigger_kind": "custom",
                        "expression": {"and": [{"field": "quote.nope", "op": "<=", "value": 1}]}})
        check("POST /t/conditions 非法字段 → 400（用生产 t_expr 校验）",
              s == 400 and "非法字段" in str(j.get("error")), "status=%s error=%s" % (s, str(j.get("error"))[:60]))
        s, j, h = call(B, "POST", "/t/build/no-rebuild", {"action": "add"})
        check("POST /t/build/no-rebuild add 缺 symbol → 400", s == 400, "status=%s" % s)

        # ── 17. 请求日志（审计：生产请求数为 0） ───────────────────────
        rows = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        try:
                            rows.append(json.loads(ln))
                        except Exception as _e_sil3:
                            _silent_alert("bt_asof_api_smoke.py:461", _e_sil3)
        reqs = [r for r in rows if r.get("method")]
        check("请求日志 JSONL 逐行可解析且有记录", len(reqs) >= 20, "lines=%d reqs=%d" % (len(rows), len(reqs)))
        check("日志行含 ts/method/path/status/asof/耗时",
              all(all(k in r for k in ("ts", "method", "path", "status", "asof", "duration_ms")) for r in reqs),
              "bad=%d" % sum(1 for r in reqs if not all(k in r for k in ("ts", "method", "path", "status",
                                                                         "asof", "duration_ms"))))
        check("**所有日志行 upstream_http == 0（生产请求数为 0 的证据）**",
              all(r.get("upstream_http") == 0 for r in rows),
              "distinct=%s" % sorted({r.get("upstream_http") for r in rows}))
        check("日志里能看到 501 与 409 的失败留痕",
              any(r.get("status") == 501 for r in reqs) and any(r.get("status") == 409 for r in reqs),
              "501=%d 409=%d 200=%d" % (sum(1 for r in reqs if r.get("status") == 501),
                                        sum(1 for r in reqs if r.get("status") == 409),
                                        sum(1 for r in reqs if r.get("status") == 200)))

        # ── 18. --created-at-policy=trade-date（放宽口径另起实例） ──────
        s2 = Server(py, script, ["--root", repo, "--port", 0, "--state-file", state,
                                 "--log", log, "--sandbox-root", sandbox,
                                 "--created-at-policy", "trade-date"], "trade-date")
        check("trade-date 口径实例启动", s2.port is not None, "port=%s" % s2.port)
        if s2.port:
            B2 = s2.base
            s, j, h = call(B2, "GET", "/t/conditions?status=any&limit=5")
            check("trade-date：能取到本地 PG 历史条件（含 expression_summary）",
                  s == 200 and (j.get("count") or 0) > 0
                  and all(c.get("expression_summary") for c in (j.get("conditions") or [])),
                  "status=%s n=%s policy=%s" % (s, j.get("count"), j.get("created_at_policy")))
            s, j, h = call(B2, "GET", "/market/market-state")
            check("trade-date：market-state 返回真诊断（不是 unknown 占位）",
                  s == 200 and j.get("state") != "unknown" and j.get("score") is not None,
                  "status=%s state=%s label=%s" % (s, j.get("state"), j.get("label")))
    finally:
        for s_ in (srv, s2):
            if s_ is not None:
                s_.stop()

    ok = sum(1 for _, o, _ in RESULTS if o)
    bad_n = len(RESULTS) - ok
    print("=" * 100)
    print("SMOKE %s | %d/%d passed, %d failed" % ("OK" if bad_n == 0 else "FAILED", ok, len(RESULTS), bad_n))
    for n, o, d in RESULTS:
        if not o:
            print("  FAILED: %s | %s" % (n, d))
    print("自测目录: %s（state=%s log=%s sandbox=%s）" % (smoke, state, log, sandbox))
    print("=" * 100, flush=True)
    return 0 if bad_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
