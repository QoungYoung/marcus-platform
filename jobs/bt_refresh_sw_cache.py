# -*- coding: utf-8 -*-
"""bt_refresh_sw_cache.py —— 回测开跑前**刷新申万行业缓存**（账本 §9.759）

用户 2026-10-07 拍板 ✓：「**回测开跑前自动刷新申万缓存**」

为什么需要 ✗：
  §9.758 的「排除光伏/商业航天」读 `data/sw_members_cache.json` ✓，
  但 `data/` **被 .gitignore 忽略** ✗ ⇒ 换台机 / 新 clone ⇒ 缓存缺失
  ⇒ 模块 **fail-open 静默放行** ✗（「看着有、实际没生效」✗ —— 今晚反复吃亏的形态）
  ⇒ 故：**跑批前先拉一次** ✓（几秒 ✓ 不把 1.5 MB 数据塞进 git ✓）

数据源 ✓：`relay_query('index_member_all', is_new='Y')` ⇒ 全市场 5914 行 ✓
  列：`l1_code/l1_name/l2_code/l2_name/l3_code/l3_name/ts_code/name/in_date/out_date/is_new`
  格式 ✓：与既有缓存一致 —— **一个纯 JSON 列表** ✓（模块 `universe_exclude._sw_rows` 这么读的 ✓）

安全 ✓：
  · **原子写**（先写 `.tmp` ⇒ `os.replace` ✓）⇒ 绝不写坏旧缓存 ✓
  · **校验后才覆盖**（行数 ≥ 5000 ✓ ＋ 关键列齐 ✓ ＋ `ts_code` 全非空 ✓）⇒ 防「拉了个空/半截」✗
  · ★ **失败不阻断跑批** ✓：打醒目告警 ⇒ **退出码仍 0** ✓（沿用旧缓存 ✓）
  · ✗ **不 import** 任何会 `install_net_offline()` 的模块 ✓（必须在离线守卫之前能跑 ✓）
  · 开关 ✓：`WOLF_SW_CACHE_MAX_AGE_DAYS`（默认 1 ✓）⇒ 缓存足够新就跳过 ✓

用法 ✓：
  .venv/bin/python jobs/bt_refresh_sw_cache.py --print-only     # 只拉不写（自测 ✓）
  .venv/bin/python jobs/bt_refresh_sw_cache.py                  # 正式刷新（默认 ✓）
  .venv/bin/python jobs/bt_refresh_sw_cache.py --max-age-days 0  # 强制刷新 ✓
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

REQ_COLS = ("ts_code", "l1_name", "l2_name", "l3_name", "in_date", "name")
MIN_ROWS = 5000
TAG = "[SW-CACHE]"


def _repo() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cache_path() -> str:
    return os.path.join(_repo(), "data", "sw_members_cache.json")


def _warn(msg: str) -> None:
    sys.stderr.write("%s ✗ %s\n" % (TAG, msg))
    sys.stderr.flush()
    try:
        print("%s ✗ %s" % (TAG, msg), flush=True)
    except Exception:
        pass


def _info(msg: str) -> None:
    print("%s %s" % (TAG, msg), flush=True)


def _age_days(path: str) -> Optional[float]:
    try:
        return (time.time() - os.path.getmtime(path)) / 86400.0
    except Exception:
        return None


def _fetch() -> List[Dict[str, Any]]:
    """取全市场申万分类 ✓（判空必须按 DataFrame 语义 ✗ 不许 `if not d` ✗）"""
    sys.path.insert(0, _repo())
    from core.tushare_relay import relay_query  # noqa: E402  局部导入 ✓（避免拉起重依赖 ✓）
    d = relay_query("index_member_all", is_new="Y")
    if d is None or getattr(d, "empty", True):
        raise RuntimeError("relay_query 返回空 ✓")
    rows: List[Dict[str, Any]] = []
    for _, r in d.iterrows():
        item = {c: (None if r.get(c) is None else str(r.get(c))) for c in d.columns}
        rows.append(item)
    return rows


def _validate(rows: List[Dict[str, Any]]) -> None:
    n = len(rows)
    if n < MIN_ROWS:
        raise RuntimeError("行数不足：%d < %d ✓" % (n, MIN_ROWS))
    if not rows:
        raise RuntimeError("零行 ✓")
    miss = [c for c in REQ_COLS if c not in rows[0]]
    if miss:
        raise RuntimeError("缺关键列：%s ✓" % ",".join(miss))
    bad = [r.get("ts_code") for r in rows if not str(r.get("ts_code") or "").strip()]
    if bad:
        raise RuntimeError("有 %d 行 ts_code 为空 ✓" % len(bad))


def _write_atomic(path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)     # ★ 原子替换 ✓（旧文件不会被写坏 ✓）


def main() -> int:
    ap = argparse.ArgumentParser(description="刷新申万行业缓存（失败不阻断跑批 ✓）")
    ap.add_argument("--print-only", action="store_true", help="只拉不写 ✓（自测用 ✓）")
    ap.add_argument("--max-age-days", type=float,
                    default=float(os.getenv("WOLF_SW_CACHE_MAX_AGE_DAYS", "1") or 1),
                    help="缓存足够新则跳过 ✓（默认 1 天 ✓；0 = 强制刷新 ✓）")
    a = ap.parse_args()

    path = os.getenv("WOLF_SW_CACHE") or _cache_path()
    age = _age_days(path) if os.path.exists(path) else None
    if (not a.print_only) and age is not None and a.max_age_days > 0 and age < a.max_age_days:
        _info("缓存足够新（%.2f 天 < %.2f 天 ✓）⇒ 跳过刷新 ✓ %s" % (age, a.max_age_days, path))
        return 0

    try:
        t0 = time.time()
        rows = _fetch()
        _validate(rows)
        n = len(rows)
        if a.print_only:
            _info("【自测】拉到 %d 行 ✓（用了 %.1f 秒 ✓）⇒ --print-only 不写盘 ✓" % (n, time.time() - t0))
            _info("  列 ⇒ %s" % ",".join(list(rows[0].keys())[:10]))
            return 0
        _write_atomic(path, rows)
        _info("✅ 已刷新 ⇒ %s（%d 行 ✓，耗时 %.1f 秒 ✓）" % (path, n, time.time() - t0))
        return 0
    except Exception as exc:  # noqa: BLE001
        # ★ 绝不阻断跑批 ✓：醒目告警 ⇒ 退出码仍 0 ✓
        _warn("刷新失败：%s ⇒ **沿用旧缓存**（若旧缓存缺失 ⇒ 规则将 fail-open 放行 ✗ 请注意覆盖）" % str(exc)[:140])
        return 0


if __name__ == "__main__":
    sys.exit(main())
