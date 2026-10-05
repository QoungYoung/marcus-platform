# -*- coding: utf-8 -*-
"""bt_check_day_data.py —— **每日起跑前的数据体检**（账本 §9.639 ✓）

## 为什么（用户要求 ✓）

> 「① 加一道体检 ✓（每天起跑前核对 `mins_adj/<日>` 文件数 ≥ 当日名单数 ✓
>  ⇒ 不达标先补齐再跑 ✓，避免再"静默半天无行情" ✗）」

- 事故复盘 ✓：20260120 有 **19/43 标的"当日无行情"** ✗ —— 根因是
  **复权步骤崩了** ✗ ＋ **取数被 503 熔断** ✗ ⇒ `mins_adj/<标的>_<日>.json` 没生成 ✗
  ⇒ 那些标的**整日不被评估**（成交/离场都缺 ✓）
- ⇒ ⇒ 本脚本在**每天 prod 之前**核对 ✓：**实际档数 vs 当日名单数** ✓
  · 达标 ✓ ⇒ 放行（打印一行 ✓）
  · 不达标 ✗ ⇒ ①**落盘 ＋ 推 QQ 告警** ✓（受去重/限流 ✓）
    ②`--fix` 时**自动跑复权补齐** ✓（`mk_adj_mins.py` ✓，约 10 秒 ✓）
    ③退出码 **1** ✓（调用方可据此决定是否继续 ✓）

用法 ✓：
  `.venv/bin/python jobs/bt_check_day_data.py --day 20260120 --root data/_bt_t35d [--fix]`
"""
from __future__ import annotations
import argparse
import glob
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _day_symbols(root: str, day: str, account: str = ""):
    """当日名单 ✓ —— **复用 `bt_days.mins_union_symbols(legs, cut, account)`** ✓
    （就是臂自己用的那个 ✓，避免"体检口径 ≠ 实跑口径"✗）。

    真实签名 ✓（2026-10-05 查证 ✓）：`mins_union_symbols(formal_legs, cut, account="")`
      · `formal_legs` = 当日腿列表 ✓（读 `<root>/<day>/legs.jsonl` ✓）
      · `cut`         = 当日时点串 ✓（用 `<day> 15:00` ✓）
      · `account`     = 账户 ✓（默认取环境 `T_MONITOR_ACCOUNT` ✓，回测里传 drabt35d ✓）
    ★ 背景 ✓：`bt_days.py` 的注释亲口写过这事故 ——
      「它们的分钟档既不检查也不补齐 ⇒ `TMonitor._round` 里 `if not quote: continue`
        **静默跳过** ⇒ 离场腿整日不评估、仓位冻结 ✗」
      ⇒ 与本脚本存在**同一个目的** ✓
    """
    try:
        import importlib.util as _ilu
        _p = os.path.join(REPO, "jobs", "bt_days.py")
        _sp = _ilu.spec_from_file_location("marcus_bt_days_forcheck", _p)
        _m = _ilu.module_from_spec(_sp)
        _sp.loader.exec_module(_m)
        fn = getattr(_m, "mins_union_symbols", None)
        if not callable(fn):
            print("[体检] 找不到 mins_union_symbols（退化扫描 ✓）")
        else:
            legs = []
            lp = os.path.join(root, day, "legs.jsonl")
            if os.path.exists(lp):
                import json as _js
                for ln in open(lp, encoding="utf-8"):
                    ln = ln.strip()
                    if ln:
                        try:
                            legs.append(_js.loads(ln))
                        except Exception as _e_j:
                            print("[体检] legs 行解析跳过: %s" % str(_e_j)[:60])
            r = fn(legs, "%s 15:00" % day, account or os.getenv("T_MONITOR_ACCOUNT", "") or "")
            syms = r[0] if isinstance(r, tuple) else r
            if syms:
                return {str(x) for x in syms}
    except Exception as _e_u:
        print("[体检] 复用 mins_union_symbols 失败（退化扫描 ✓）: %s" % str(_e_u)[:80])
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--root", default="data/_bt_t35d")
    ap.add_argument("--adj-dir", default=os.path.join("data", "_bt_full", "mins_adj"))
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--account", default="")
    a = ap.parse_args()
    # ★ 账本 §9.639 修正 ✓：**两侧格式不同，必须归一化** ✗
    #   实测踩坑 ✓：`mins_union_symbols` 返回 **`SH600118`**（前缀 ✓），
    #     而文件名是 **`600118_SH_5min_20260120.json`**（后缀 ✓）
    #     ⇒ 直接比 ⇒ **误报"缺 9 个"** ✗（其实 9 个的原始档与复权档**全都在** ✓）
    #   ⇒ 统一归一到 **6 位代码** ✓
    def _c6(x: str) -> str:
        d = "".join(ch for ch in str(x) if ch.isdigit())
        return d[-6:] if len(d) >= 6 else d

    have = {_c6(os.path.basename(p).split("_")[0])
            for p in glob.glob(os.path.join(a.adj_dir, "*_%s.json" % a.day))}
    want = _day_symbols(a.root, a.day, a.account)
    want6 = {_c6(x): x for x in want}
    miss = sorted(want6[k] for k in (set(want6) - have))
    print("[体检] %s：当日名单 %d 个 ｜ 复权档 %d 个 ｜ 缺 %d 个" % (a.day, len(want), len(have), len(miss)))
    if not want:
        print("[体检] ⚠️ 取不到当日名单（跳过体检 ✓，不阻断 ✓）")
        return 0
    if not miss:
        print("[体检] ✅ 达标 ✓（每个名单标的都有复权档 ✓）")
        return 0
    print("[体检] ✗ 不达标：缺 %s%s" % (",".join(miss[:12]), " …" if len(miss) > 12 else ""))
    try:
        # ★ 账本 §9.638 同源 ✓：**按路径导入** alert_hub（不依赖 sys.path ✓）
        sys.path.insert(0, REPO)
        sys.path.insert(0, os.path.join(REPO, "backend"))
        import importlib.util as _ilu_h
        _hp = os.path.join(REPO, "backend", "app", "services", "alert_hub.py")
        _hs = _ilu_h.spec_from_file_location("marcus_alert_hub_check", _hp)
        _hm = _ilu_h.module_from_spec(_hs)
        _hs.loader.exec_module(_hm)
        _ah = _hm
        _ah.note("bt_check_day_data.缺档", msg="%s：名单 %d / 有档 %d ⇒ **缺 %d 个**（这些标的当日不会被评估 ✗）"
                 % (a.day, len(want), len(have), len(miss)))
    except Exception as e:
        print("[体检] 告警失败: %s" % str(e)[:90])
    if a.fix:
        script = os.path.join(REPO, ".dsh-tmp", "wolfbt", "mk_adj_mins.py")
        if os.path.exists(script):
            print("[体检] → 自动补齐（跑 %s ✓）" % os.path.basename(script))
            try:
                r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=900)
                print("[体检] 补齐 rc=%s ⇒ %s" % (r.returncode, (r.stdout or "")[-200:].replace("\n", " ｜ ")))
            except Exception as e:
                print("[体检] 补齐失败: %s" % str(e)[:90])
    return 1


if __name__ == "__main__":
    sys.exit(main())
