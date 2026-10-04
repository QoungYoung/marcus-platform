# -*- coding: utf-8 -*-
"""bt_gen_mainline.py — **按天现算主线**（账本 §9.418 ✓ 用户「接上去」✓）

为什么 ✓：沙箱里的 `main_line_state.json` 是**预烘焙语料** ✗（`main_line_source=mainline_select_20260915` ✓、
  `date=2026-09-13` ✓）⇒ 0303 判成"半导体/芯片" ✗，而**现算**（`WOLF_MAINLINE_SELECT=1` ✓）判 **新能源/电池** ✓
  （候选前 3：新能源 1245.2 ✓ ＞ AI/算力 724.1 ✓ ＞ 半导体 683.6 ✓）⇒ 方向错 ⇒ 选票错 ✓

设计（用户 2026-10-02 拍板 ✓）：
  · **可缓存、可共享** ✓：历史主线**不会变** ✓（除非**主线判定代码**变化 ✓）
  · 缓存键 ＝ **判定代码的指纹**（把相关源文件内容 hash ✓）⇒ 代码一变 ⇒ 目录变 ⇒ **自动重算** ✓
  · 落盘 ✓：`data/_bt_leader/mainline_cache/<代码指纹>/<day>/main_line_state.json` ✓（**跨臂共享** ✓）

用法 ✓：python jobs/bt_gen_mainline.py --day 20260303 --root data/_bt_t35 [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import traceback


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
CACHE = os.path.join(REPO, "data", "_bt_leader", "mainline_cache")
# 参与指纹的源文件 ✓（这些一变 ⇒ 主线的算法/口径就变了 ⇒ 缓存必须失效 ✓）
FINGER_FILES = [
    "backend/app/services/wolf_mainline_select.py",
    "apps/main_line/mainline_state_inject.py",
    "apps/main_line/fusion_mainline.py",
]


def code_fingerprint() -> str:
    h = hashlib.sha1()
    for rel in FINGER_FILES:
        p = os.path.join(REPO, rel)
        try:
            h.update(open(p, "rb").read())
        except Exception:
            h.update(rel.encode())
    # 口径类 env 也进指纹 ✓（换口径 ⇒ 主线可能变 ⇒ 必须重算 ✓）
    for k in ("WOLF_MAINLINE_SCORE", "WOLF_MS_POOL_K", "WOLF_MS_USE_GATE"):
        h.update(("%s=%s;" % (k, os.getenv(k, ""))).encode())
    return h.hexdigest()[:10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--root", default=os.path.join(REPO, "data", "_bt_t35"))
    ap.add_argument("--bars-db", default=os.path.join(REPO, "data", "_bt_full", "bars.sqlite"))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    day = str(a.day).replace("-", "")
    fp = code_fingerprint()
    cdir = os.path.join(CACHE, fp, day)
    cfile = os.path.join(cdir, "main_line_state.json")
    root = a.root if os.path.isabs(a.root) else os.path.join(REPO, a.root)
    day_dir = os.path.join(root, day)
    os.makedirs(day_dir, exist_ok=True)
    for f in ("stock_pool.db", "stock_pool_local.db"):
        lp = os.path.join(day_dir, f)
        if not os.path.exists(lp):
            try:
                os.symlink(os.path.join(REPO, "data", f), lp)
            except Exception as _e_sil1:
                _silent_alert("bt_gen_mainline.py:69", _e_sil1)
    os.chdir(REPO)
    sys.path.insert(0, os.path.join(REPO, "jobs"))
    sys.path.insert(0, os.path.join(REPO, "backend"))
    log = lambda m: print("[gen_mainline] %s" % m, flush=True)  # noqa: E731
    if os.path.exists(cfile) and not a.force:
        shutil.copyfile(cfile, os.path.join(day_dir, "main_line_state.json"))
        log("✅ 命中缓存 %s/%s ✓（指纹 %s ✓）⇒ 已放入沙箱 ✓" % (fp, day, fp))
        return 0
    try:
        os.environ["WOLF_MAINLINE_SELECT"] = "1"
        os.environ["DATA_DIR"] = day_dir
        os.environ["WOLF_MS_UNIVERSE_DB"] = os.path.join(REPO, "data", "stock_pool.db")
        import bt_run_pinned as brp
        brp.install_relay_shim(a.bars_db, day)
        try:
            brp.install_gzcloud_shim(a.bars_db, day)
        except Exception as _e_sil2:
            _silent_alert("bt_gen_mainline.py:87", _e_sil2)
        from app.services import wolf_mainline_select as WMS
        r = WMS.run(save=True, date8=day)
        if not r or not r.get("ok"):
            log("❌ 现算失败: %s" % json.dumps(r, ensure_ascii=False)[:160])
            return 1
        src = os.path.join(day_dir, "main_line_state.json")
        if not os.path.exists(src):
            log("❌ 服务未写出 main_line_state.json ✓")
            return 1
        os.makedirs(cdir, exist_ok=True)
        shutil.copyfile(src, cfile)
        log("✅ 现算完成 ✓ %s ⇒ 主线=**%s** ✓（次 %s ✓）｜指纹 %s ✓｜已入缓存 ✓"
            % (day, r.get("mainline"), r.get("second"), fp))
        return 0
    except Exception as e:
        log("❌ 异常: %s\n%s" % (str(e)[:160], traceback.format_exc()[-600:]))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
