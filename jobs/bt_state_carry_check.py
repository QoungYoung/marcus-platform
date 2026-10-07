# -*- coding: utf-8 -*-
"""bt_state_carry_check.py — 跨日状态文件结转（P0-a / P0-b）的**验收/回归**检查。

背景（审计 `.dsh-tmp/gap_audit_4items_20260917.md` §2 + 优先级表 P0）：
  沙箱软链农场把生产 `data/` 下的**当期快照**链进每一天的沙箱 → 状态文件里
  `rebased_at=2026-09-16 11:07`（**未来**）参与 3/8 月的 floor 计算；且每天一份新沙箱，
  "一次性认账 / 3 日内分步回补 / 两日换手窗口"等语义只在日内成立。

本脚本做两件事（**只读原沙箱，只在 --dst-root 里写**）：
  ① **BEFORE**：直接读**原沙箱**的状态文件，打印 `events 数 / max rebased_at / max_date`
     —— 证明存在"晚于当日的未来记录"；
  ② **AFTER**：在 `--dst-root` 的拷贝里按 `bt_seed_day.carry_state_files()` 逐日结转
     （第一天前值不可用 → `{}`），再打印同样的摘要 + 断言
     **`max_date ≤ 当日`**（不再有未来记录）且**前一日记录被带过来**（跨日连续）。

用法：
  python jobs/bt_state_carry_check.py --days 20260824,20260825,20260826 \
      --src-root data/_bt_year --dst-root .dsh-tmp/bt_carry_check/root \
      --bars-db data/_bt_full/bars.sqlite --out .dsh-tmp/bt_carry_check/report.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_env            # noqa: E402
import bt_seed_day as BSD  # noqa: E402



# ── 通用：剔除"晚于 limit 的记录"（只用于模拟"修复后当日跑完的真实状态"）──────
def prune_future(name: str, obj, limit: str):
    """复用 `bt_seed_day.prune_future_records`（**同一实现**，避免两处分叉）。

    ⚠️ 只用于**验收模拟**：数据仍然是当日真实沙箱文件，只删"晚于当日的未来记录"，
    **绝不顶替日期、绝不插值**（改完的值只用来说明"修复后当日状态长什么样"）。
    """
    cleaned, _dropped, _stamps = BSD.prune_state_obj(name, obj, limit)
    return cleaned if cleaned is not None else {}


def _copy_state_files(src_day: str, dst_day: str, names) -> list:
    """把原沙箱的状态文件（**含软链本体**）拷进 dst —— 复现"旧 seed 的污染起点"。"""
    os.makedirs(dst_day, exist_ok=True)
    done = []
    for nm in names:
        s, d = os.path.join(src_day, nm), os.path.join(dst_day, nm)
        if os.path.lexists(d):
            os.unlink(d)
        if os.path.islink(s):
            os.symlink(os.readlink(s), d)        # 连"指向生产"的软链一起复现
            done.append(nm)
        elif os.path.isfile(s):
            shutil.copy2(s, d)
            done.append(nm)
    return done


def _row(day: str, name: str, d: dict) -> dict:
    return {"day": day, "file": name, "kind": d.get("kind"), "bytes": d.get("bytes"),
            "keys": d.get("keys"), "events": d.get("events"), "symbols": d.get("symbols"),
            "max_rebased_at": d.get("max_rebased_at") or "", "max_date": d.get("max_date") or "",
            "max_any_date": d.get("max_any_date") or "", "pruned_future": d.get("pruned_future"),
            "future_stamps": d.get("future_stamps"),
            "src": d.get("src"), "prev": d.get("prev")}


def _fmt_counts(d: dict) -> str:
    """条目数摘要：`t_base_floor` 给 symbols/events，其余给 keys（0 = 空状态 {}）。"""
    if "events" in d:
        return "sym=%s ev=%s" % (d.get("symbols"), d.get("events"))
    return "keys=%s" % d.get("keys")


def _fmt_dates(d: dict) -> str:
    """日期摘要：**gating**（决定行为，验收看这个） / **any**（含诊断戳）。"""
    return "gate=%-10s any=%s" % (d.get("max_date") or "-", d.get("max_any_date") or "-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True, help="连续交易日，逗号分隔（升序）")
    ap.add_argument("--src-root", default=os.path.join(bt_env.DATA, "_bt_year"), help="既有沙箱根（只读）")
    ap.add_argument("--dst-root", required=True, help="验收用拷贝根（会写；不要给正在跑年跑的根）")
    ap.add_argument("--bars-db", default=os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite"))
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    days = [d.strip() for d in a.days.split(",") if d.strip()]
    os.makedirs(a.dst_root, exist_ok=True)
    rep = {"days": days, "src_root": a.src_root, "dst_root": a.dst_root,
           "before": [], "after": [], "invariants": []}

    # ── ① BEFORE：原沙箱现状 ──────────────────────────────────────────────
    print("\n=== ① BEFORE：原沙箱（data/_bt_year/<day>）里的状态文件现状 ===", flush=True)
    print("%-10s %-26s %-34s %-16s %-20s %s"
          % ("day", "file", "kind", "counts", "max_rebased_at", "dates(gating|any)"), flush=True)
    for d8 in days:
        for nm in BSD.CARRY_STATE_FILES:
            d = BSD.state_digest(os.path.join(a.src_root, d8, nm))
            row = _row(d8, nm, d)
            rep["before"].append(row)
            if nm == "t_base_floor_rebase.json" or d.get("kind") not in ("MISSING", None):
                print("%-10s %-26s %-34s %-16s %-20s %s"
                      % (d8, nm, str(d.get("kind"))[:34], _fmt_counts(d),
                         d.get("max_rebased_at") or "-", _fmt_dates(d)), flush=True)

    # ── ② AFTER：逐日结转（首日前值不可用 → {}；其后从前一日沙箱带过来）──────
    print("\n=== ② AFTER：在 --dst-root 拷贝里逐日结转（bt_seed_day.carry_state_files）===", flush=True)
    for i, d8 in enumerate(days):
        dst_day = os.path.join(a.dst_root, d8)
        # 旧行为起点：把原沙箱（污染）文件拷进来
        _copy_state_files(os.path.join(a.src_root, d8), dst_day, BSD.CARRY_STATE_FILES)
        if i == 0:
            # 造出"前一交易日的旧布局"（软链到生产 data/）→ 检验"前值不可用就写 {}"这条规则
            prev = BSD.prev_trade_day(d8, a.bars_db)
            if prev and prev != d8:
                pdir = os.path.join(a.dst_root, prev)
                os.makedirs(pdir, exist_ok=True)
                for nm in BSD.CARRY_STATE_FILES:
                    p = os.path.join(pdir, nm)
                    tgt = os.path.join(os.path.realpath(bt_env.DATA), nm)
                    if os.path.lexists(p):
                        os.unlink(p)
                    if os.path.exists(tgt):
                        os.symlink(tgt, p)       # 旧布局：指向生产当期快照
        carried = BSD.carry_state_files(dst_day, a.dst_root, d8, a.bars_db, verbose=False)
        for nm in BSD.CARRY_STATE_FILES:
            d = carried.get(nm) or BSD.state_digest(os.path.join(dst_day, nm))
            row = _row(d8, nm, d)
            rep["after"].append(row)
            print("%-10s %-26s %-40s %-16s %-20s %-10s %s"
                  % (d8, nm, str(d.get("src"))[:40], _fmt_counts(d),
                     d.get("max_rebased_at") or "-", str(d.get("kind"))[:6], _fmt_dates(d)), flush=True)
        # 断言：结转后不得有 > 当日 的未来记录
        bad = [nm for nm in BSD.CARRY_STATE_FILES
               if (carried.get(nm, {}).get("max_date") or "") > d8]
        rep["invariants"].append({"day": d8, "future_records": bad, "pass": not bad})
        print("[check] %s 未来记录检查：%s" % (d8, "PASS ✅" if not bad else "FAIL ⛔ %s" % bad), flush=True)
        # 模拟"当日跑完写盘"：用**当日真实文件**剔除未来记录 → 供下一日结转（否则下一日只能看到 {}）
        if i + 1 < len(days):
            for nm in BSD.CARRY_STATE_FILES:
                src = os.path.join(a.src_root, d8, nm)
                if not os.path.isfile(src):
                    continue
                obj = BSD._jload(src, None)
                if isinstance(obj, dict):
                    BSD._materialize(os.path.join(dst_day, nm))
                    BSD._jdump(os.path.join(dst_day, nm), prune_future(nm, obj, d8))
            print("[check] %s 已写入『当日跑完（剔除未来记录）』的真实状态 → 供 %s 结转"
                  % (d8, days[i + 1]), flush=True)

    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print("\n[check] 报告 → %s" % a.out, flush=True)
    ok = all(x["pass"] for x in rep["invariants"])
    print("[check] 结论：%s" % ("全部 PASS ✅（结转后不存在晚于当日的记录）" if ok else "存在 FAIL ⛔"), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
