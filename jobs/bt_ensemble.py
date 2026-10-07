#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""年跑**多次重复取均值**（集成跑）：串行驱动 K 次完整年跑 + 聚合出均值/中位数/离散度。

用户 2026-09-17 拍板："多跑取均值吧"。
为什么需要它：record 模式下 **LLM 采样不可复现** —— 同一个触发在不同跑次能给出 exec/wait/abandon
四种回复（实测同一 prompt 骨架 7 次录制），所以"收益"是**一次实现**，不是策略的确定属性。
要给出可信的数，必须①重复 K 次拿到分布 ②每次都必须是**独立采样**。

⚠️ 独立性的关键坑（本脚本存在的理由）：`jobs/bt_llm_replay.py` 在 record 模式下会**先查缓存**，
同一 prompt 的第二跑会直接命中第一跑的录制 ⇒ 多跑得到的是**同一条回复**，差异被人为抹平、
"均值"是假的。故本脚本对 k>=2 强制 `BT_LLM_FRESH=1`（不读缓存、仍写缓存）：
每次都是新的 LLM 采样，采样结果按 `prompt_sha1` 多条并存于同一缓存文件里（既能看分布，也能冻结一套回放）。

隔离清单（每次跑开始前必须成立的"同一外部条件"）：
  · 账户：`--prod-reset-first` 清空 `paper_*` + `t_conditions` + `t_triggers` + 本次年跑起点及以后的 `t_ai_actions`
  · 沙箱种子：**复用**（`--reuse-seeded`）——选股/布腿/波浪这些**外生输入**在各跑之间保持一致，
    只让"AI 决策"这一层重新采样（否则方差里混进了输入差异，均值没有意义）
  · 口径：环境开关随 `bt_env_from_prod.env` 固化；入口只走 `jobs/bt_run_year.sh`（启动器自带口径回显与硬门控）

用法：
  python jobs/bt_ensemble.py plan   --runs 3            # 只打印将要做什么
  python jobs/bt_ensemble.py run    --runs 3 --attach   # 把当前在跑的一跑当成 run_1，之后再跑 2 次
  python jobs/bt_ensemble.py report --runs 3            # 聚合（月收益/均值/离散度/分腿型已实现）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict


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


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "jobs"))
SUMMARY = os.path.join(REPO, "data/_bt_year/_summary")
ENS = os.path.join(REPO, "data/_bt_ens")
LOGDIR = os.path.join(REPO, ".dsh-tmp/wolfbt/logs")
YEARLOG = os.path.join(LOGDIR, "year_prod_run.log")
INITIAL = 250000.0


# ── 进程/进度探测（无 ps 权限：扫 /proc）──
def _bt_days_pids():
    out = []
    for p in glob.glob("/proc/[0-9]*"):
        try:
            with open(os.path.join(p, "cmdline"), "rb") as f:
                c = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except Exception as _e_sil1:
            _silent_alert("bt_ensemble.py:56", _e_sil1)
            continue
        if "bt_days.py" in c:
            out.append(p.rsplit("/", 1)[-1])
    return out


def _last_day():
    ds = sorted(os.path.basename(f)[5:13] for f in glob.glob(os.path.join(SUMMARY, "prod_2026*.json")))
    return ds[-1] if ds else None


def archive_run(k: int, meta: dict) -> str:
    """把 `_summary` 里这一跑的产物整体搬进 data/_bt_ens/run_<k>/（互不覆盖）。"""
    dst = os.path.join(ENS, "run_%d" % k)
    os.makedirs(dst, exist_ok=True)
    if os.path.isdir(SUMMARY):
        for fn in os.listdir(SUMMARY):
            src = os.path.join(SUMMARY, fn)
            if os.path.isfile(src):
                shutil.move(src, os.path.join(dst, fn))
    meta = dict(meta)
    meta.update({"run": k, "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "files": len(os.listdir(dst))})
    json.dump(meta, open(os.path.join(dst, "run_meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[ens] run_%d 归档 %d 个文件 → %s" % (k, meta["files"], dst), flush=True)
    return dst


def llm_totals(run_dir: str) -> dict:
    """该跑所有日终快照里的 LLM 命中/录制合计（用于证明"这一跑是独立采样"）。"""
    hit = rec = miss = 0
    for f in glob.glob(os.path.join(run_dir, "prod_2026*.json")):
        try:
            llm = ((json.load(open(f, encoding="utf-8")).get("agent") or {}).get("llm") or {})
        except Exception as _e_sil2:
            _silent_alert("bt_ensemble.py:92", _e_sil2)
            continue
        hit += int(llm.get("hit") or 0)
        rec += int(llm.get("recorded") or 0)
        miss += int(llm.get("miss") or 0)
    return {"hit": hit, "recorded": rec, "miss": miss}


def wait_run(end_day: str, timeout_h: float = 30.0, stall_min: float = 25.0) -> bool:
    """等到年跑结束。用"进程消失 + 日志出现完成行"判定；长时间无进展则判失败（不静默继续）。"""
    t0 = time.time()
    last_size, last_change = -1, time.time()
    while True:
        pids = _bt_days_pids()
        try:
            size = os.path.getsize(YEARLOG)
        except Exception:
            size = 0
        if size != last_size:
            last_size, last_change = size, time.time()
        try:
            tail = open(YEARLOG, encoding="utf-8", errors="replace").read()[-4000:]
        except Exception:
            tail = ""
        done = ("[days] 完成：" in tail) or (_last_day() or "") >= end_day
        if not pids and done:
            print("[ens] 年跑已结束（末日 %s）" % _last_day(), flush=True)
            return True
        if not pids and not done:
            print("[ens] ⛔ 年跑进程消失但未见完成行 → 判为失败（末日 %s）" % _last_day(), flush=True)
            return False
        if (time.time() - last_change) > stall_min * 60:
            print("[ens] ⛔ 日志 %.0f 分钟无增长 → 判为卡死（末日 %s）"
                  % (stall_min, _last_day()), flush=True)
            return False
        if (time.time() - t0) > timeout_h * 3600:
            print("[ens] ⛔ 超时 %.0fh" % timeout_h, flush=True)
            return False
        time.sleep(60)


def launch(fresh: bool, start: str, end: str) -> None:
    env = dict(os.environ)
    env.update({"BT_START": start, "BT_END": end, "BT_RESET_FIRST": "1", "BT_AGENT": "on"})
    if fresh:
        env["BT_LLM_FRESH"] = "1"
    print("[ens] 启动年跑：start=%s end=%s fresh=%s" % (start, end, fresh), flush=True)
    # 启动器自己会 setsid 放后台并退出；这里等它把 run 起来
    subprocess.run(["bash", os.path.join(REPO, "jobs/bt_run_year.sh")], cwd=REPO, env=env, timeout=600)


def cmd_run(a) -> int:
    os.makedirs(ENS, exist_ok=True)
    attached = False
    if a.attach and _bt_days_pids():
        print("[ens] 检测到在跑的年跑 → 当作 run_1（它必须已经在用修好的口径；结束时会记录 LLM 命中数）",
              flush=True)
        if not wait_run(a.end):
            print("[ens] run_1 失败，终止集成跑", flush=True)
            return 2
        archive_run(1, {"mode": "attach", "start": a.start, "end": a.end,
                        "llm": llm_totals(SUMMARY)})
        attached = True
    for k in range(2 if attached else 1, a.runs + 1):
        launch(fresh=(k >= 2), start=a.start, end=a.end)
        # 启动器最多 10 分钟把进程拉起来
        for _ in range(60):
            time.sleep(5)
            if _bt_days_pids():
                break
        if not _bt_days_pids():
            print("[ens] ⛔ run_%d 未能启动" % k, flush=True)
            return 3
        if not wait_run(a.end):
            print("[ens] run_%d 失败，终止集成跑（已归档的跑次仍可用）" % k, flush=True)
            return 4
        archive_run(k, {"mode": "launched", "fresh": k >= 2, "start": a.start, "end": a.end,
                        "llm": llm_totals(SUMMARY)})
    print("[ens] 全部 %d 跑完成 → data/_bt_ens/run_*；用 `report` 聚合" % a.runs, flush=True)
    return 0


# ── 聚合：用统一重建口径（现金=日终快照；市值=持仓×当日收盘；各跑同法可比）──
def _run_days(run_dir: str):
    import bt_cmp_gens as C
    cur = C.rebuild(os.path.join(run_dir, "prod_2026*.json")) or []
    return cur


def _months(cur):
    by = {}
    for c in cur:
        by[c["day"][:6]] = c                    # 逐日覆盖 → 留下的是"该月最后一个交易日"
    return by


def cmd_report(a) -> int:
    runs = sorted(glob.glob(os.path.join(ENS, "run_*")))
    runs = [d for d in runs if os.path.isdir(d)][: a.runs if a.runs else None]
    if not runs:
        print("没有 data/_bt_ens/run_* —— 先跑 `run`")
        return 2
    print("══ 集成跑聚合（口径：现金=日终快照；市值=持仓×当日收盘；初始 %.0f）" % INITIAL)
    per_run = {}
    for d in runs:
        cur = _run_days(d)
        if not cur:
            print("!! %s 无数据" % d)
            continue
        name = os.path.basename(d)
        meta = {}
        try:
            meta = json.load(open(os.path.join(d, "run_meta.json"), encoding="utf-8"))
        except Exception as _e_sil3:
            _silent_alert("bt_ensemble.py:205", _e_sil3)
        llm = meta.get("llm") or llm_totals(d)
        per_run[name] = {"curve": cur, "meta": meta, "llm": llm}
        eq = cur[-1]["equity"]
        print("\n── %s（%s→%s，%d 天）期末 %.2f = %+.2f%% | 成交 %d | LLM 命中 %s / 录制 %s%s"
              % (name, cur[0]["day"], cur[-1]["day"], len(cur), eq, (eq / INITIAL - 1) * 100,
                 cur[-1]["n_trades"], llm.get("hit"), llm.get("recorded"),
                 "（fresh 独立采样）" if meta.get("fresh") else ""))
        for m, c in _months(cur).items():
            print("     %s 末 %.2f  %+6.2f%%  持仓 %2d  累计成交 %3d"
                  % (m, c["equity"], (c["equity"] / INITIAL - 1) * 100, c["n_pos"], c["n_trades"]))
    # 逐月均值
    print("\n══ 逐月：均值 / 中位数 / 最小 / 最大 / 标准差（各跑同月可比）")
    allm = sorted({m for r in per_run.values() for m in _months(r["curve"])})
    for m in allm:
        vals = [(r["curve"] and _months(r["curve"]).get(m) or {}).get("equity") for r in per_run.values()]
        vals = [(v / INITIAL - 1) * 100 for v in vals if v]
        if not vals:
            continue
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        print("  %s  n=%d  均值 %+6.2f%%  中位 %+6.2f%%  区间 [%+.2f%%, %+.2f%%]  σ=%.2fpp"
              % (m, len(vals), sum(vals) / len(vals), statistics.median(vals),
                 min(vals), max(vals), sd))
    finals = [((r["curve"][-1]["equity"]) / INITIAL - 1) * 100 for r in per_run.values() if r["curve"]]
    if len(finals) > 1:
        sd = statistics.pstdev(finals)
        print("\n══ 全期（各跑末日）：均值 %+.2f%%  中位 %+.2f%%  区间 [%+.2f%%, %+.2f%%]  σ=%.2fpp"
              % (sum(finals) / len(finals), statistics.median(finals), min(finals), max(finals), sd))
        print("   要 95%% 置信区间收窄到 ±1pp 约需 n ≈ (1.96σ)² ≈ %d 跑（当前 σ=%.2fpp，n=%d）"
              % (max(1, int((1.96 * sd) ** 2)), sd, len(finals)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--runs", type=int, default=3)
    p.add_argument("--start", default="20260105"); p.add_argument("--end", default="20260914")
    r = sub.add_parser("run"); r.add_argument("--runs", type=int, default=3)
    r.add_argument("--attach", action="store_true")
    r.add_argument("--start", default="20260105"); r.add_argument("--end", default="20260914")
    q = sub.add_parser("report"); q.add_argument("--runs", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "plan":
        print("将执行：%d 次年跑（run_1%s 起）" % (a.runs, " ← 当前在跑的那一跑" if _bt_days_pids() else ""))
        print("  隔离：每跑 reset（账户 + t_ai_actions 起点及以后）｜沙箱种子复用（外生输入一致）")
        print("  独立：run_2 起强制 BT_LLM_FRESH=1（不读缓存 → 每次都是新的 LLM 采样）")
        print("  产物：data/_bt_ens/run_<k>/（该跑全部日志+日终快照+run_meta.json）")
        print("  聚合：python jobs/bt_ensemble.py report")
        return 0
    if a.cmd == "run":
        return cmd_run(a)
    return cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
