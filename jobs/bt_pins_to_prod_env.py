# -*- coding: utf-8 -*-
"""bt_pins_to_prod_env.py —— 把回测 pins 的开关**分类**，生成"生产候选 env"（默认 dry-run）

用户 2026-10-07：「将本次回测的配置同步到生产吧…**同时注意不要将回测的专用配置同步到生产**，
生产应当能查询所有工具，**不存在未来函数的概念**」✓

本脚本只做三件事 ✓（**绝不自动改生产** ✗）：
 ① 读 `jobs/bt_env_pins.sh` 的 `export K=V` ✓
 ② 按**名字规则**分三类 ✓：
      · SYNC ✓   —— 策略/口径类（`WOLF_` 且不含回测专用词 ✓）⇒ **建议同步到生产** ✓
      · BT ✗     —— 回测专用（`BT_*` / 含 ASOF/REPLAY/SIM/OFFLINE/ADJ_PRICE/QT 等 ✓）⇒ **绝不同步** ✗
      · ASK ✓    —— 拿不准（路径/数据根/限流/超时等 ✓）⇒ **列出来让用户定** ✓
 ③ 打印三类清单 ＋ 把 SYNC 类写成候选文件 `docker/.env.prod.candidate` ✓（**只是文件**，不生效 ✓）

未来函数相关 ✓（用户点名 ✓）：
   `BT_ASOF_*`、`WOLF_SIM_DAY`、`BT_AGENT_LOOP`、`WOLF_ADJ_PRICE`（复权价空间）等
   **一律归入 BT ✗** ⇒ 生产不会有"as-of / 防未来函数"这套东西 ✓（生产本来就是当日实时 ✓）

用法 ✓：
  .venv/bin/python jobs/bt_pins_to_prod_env.py            # 只看分类（默认 ✓）
  .venv/bin/python jobs/bt_pins_to_prod_env.py --write    # 另写候选文件（仍不生效 ✓）
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Tuple

PINS = "jobs/bt_env_pins.sh"
OUT = "docker/.env.prod.candidate"

# 回测专用关键词（命中即 **绝不同步** ✗）
BT_KEYS = (
    "ASOF", "REPLAY", "SIM_", "SIMUL", "OFFLINE", "ADJ_PRICE", "QT_", "BACKTEST", "BT_",
    "AGENT_LOOP", "MEMBER_BATCH", "CARRY_STATE", "NET_ALLOW", "NET_", "DASH", "FARM",
    "SANDBOX", "TAPE", "FULL_", "YEAR", "PROD_RESET", "RESUME", "SKIP_SEED", "SEED",
    "LLM_REPLAY", "PROMPT_DUMP",
)
# 拿到就同步 ✓（明确的策略/口径类）
SYNC_PREFIX = "WOLF_"
# 拿不准 ⇒ 让用户定 ✓
ASK_KEYS = ("ROOT", "DIR", "PATH", "DATA", "TIMEOUT", "MAX_", "LIMIT", "TOPK", "TOPK_", "PWD")


def parse_pins(path: str) -> List[Tuple[str, str, int]]:
    out: List[Tuple[str, str, int]] = []
    for i, ln in enumerate(open(path, encoding="utf-8"), 1):
        m = re.match(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$", ln)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        v = v.strip("\"'")
        out.append((k, v, i))
    return out


def classify(k: str, v: str) -> str:
    ku = k.upper()
    if any(t in ku for t in BT_KEYS):
        return "BT"
    if any(t in ku for t in ASK_KEYS):
        return "ASK"
    if ku.startswith(SYNC_PREFIX):
        return "SYNC"
    return "ASK"


def main() -> int:
    ap = argparse.ArgumentParser(description="回测 pins → 生产候选（默认 dry-run ✓）")
    ap.add_argument("--pins", default=PINS)
    ap.add_argument("--write", action="store_true", help="写候选文件（仍不生效 ✓）")
    a = ap.parse_args()
    if not os.path.exists(a.pins):
        print("  ✗ 找不到 %s" % a.pins)
        return 2
    items = parse_pins(a.pins)
    buckets: Dict[str, List[Tuple[str, str, int]]] = {"SYNC": [], "BT": [], "ASK": []}
    for k, v, i in items:
        buckets[classify(k, v)].append((k, v, i))
    print("  pins 共 %d 条 export ✓" % len(items))
    for name, title in (("SYNC", "★ 建议**同步到生产** ✓（策略/口径类）"),
                        ("BT", "✗ **绝不同步**（回测专用 / 未来函数相关）"),
                        ("ASK", "? **拿不准，请你定** ✓")):
        lst = buckets[name]
        print("\n  ── %s ⇒ %d 条" % (title, len(lst)))
        for k, v, i in lst:
            print("     %-34s = %-34s (pins:%d)" % (k, v[:34], i))
    if a.write:
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write("# 由 jobs/bt_pins_to_prod_env.py 生成（**候选**，未生效）\n")
            fh.write("# 只含 SYNC 类（策略/口径 ✓）；BT 类（回测专用 / 未来函数）已剔除 ✗\n\n")
            for k, v, _i in buckets["SYNC"]:
                fh.write("%s=%s\n" % (k, v))
        print("\n  ✅ 候选文件已写 ⇒ %s（%d 条 SYNC ✓；**不会自动生效** ✓）" % (OUT, len(buckets["SYNC"])))
    else:
        print("\n  （dry-run ✓ 未写文件；要写候选加 --write ✓）")
    # 生产工具可用性提醒 ✓
    print("\n  ── ★ 上线前必查（用户点名 ✓）:")
    print("     · `docker/docker-compose.yml` 是否把**空白**的 `bt_tools_allow.txt` 挂进容器 ✗")
    print("       （空文件 ⇒ 只注册 0 个工具 ✗ ⇒ 生产查不到任何工具 ✓ 需改成\"不挂\"⇒ 全工具 ✓）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
