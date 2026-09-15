# -*- coding: utf-8 -*-
"""bt_wave_asof.py — 按 **as-of** 重放「波浪判定 agent」（全拟真回测的 08:10 那一步）。

生产行为（`apps/main_line/wave_agent.py`，cron 08:10 mon-fri）：
  取上证指数结构/点位/量能 → 交给 dsh `/chat` 按狼大多级别 Elliott 出
  `(level, sub_level, operation)` → 写 `data/wave_state.json`（次日所有闸门读它）。

回测为什么要单独包一层（**三处必须钉住的非 PIT 行为**）：
  1. `main()` 不带日期时用 CSV 最后一根 = "最近收盘" → 必须显式传 `--date`（生产 08:10 跑时 = 昨日收盘）；
  2. `_ensure_index_fresh()` 会**自愈拉取到最新收盘并回写 CSV** → 回测里必须禁用（否则有前视）；
  3. `read_main_line()` 读**当日覆盖型** `main_line_state.json` → 必须指向 as-of 那天的版本
     （`MAIN_LINE_STATE_FILE`，缺该日文件时报错，不静默用当期文件）；
  4. LLM 调用走 `bt_llm_replay` 的录制/回放（可复现）。

用法（容器内）：
  python jobs/bt_wave_asof.py --as-of 20260910 --mode record \
      --main-line-state /app/data/_bt_runs/20260910/main_line_state.json \
      --out /app/data/_bt_runs/20260911/wave_state.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path[:0] = ["/app", "/app/apps/main_line", "/app/jobs"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True,
                    help="判定基准日（= 生产 08:10 跑时看到的『昨日收盘』，如 20260910）")
    ap.add_argument("--mode", default=os.getenv("BT_LLM_MODE", "record"), choices=["record", "replay"])
    ap.add_argument("--cache", default=os.getenv("BT_LLM_CACHE", ""), help="LLM 缓存根目录")
    ap.add_argument("--main-line-state", default="", help="as-of 那天的 main_line_state.json（**必填**）")
    ap.add_argument("--out", default="", help="结果落盘路径（默认 <DATA_DIR>/_bt_runs/<as_of+1>/wave_state.json）")
    a = ap.parse_args()

    os.environ["BT_LLM_MODE"] = a.mode
    if a.cache:
        os.environ["BT_LLM_CACHE"] = a.cache
    if a.main_line_state:
        if not os.path.exists(a.main_line_state):
            print("MAIN_LINE_STATE_MISSING %s —— 拒绝回放（不静默用当期文件）" % a.main_line_state,
                  file=sys.stderr)
            return 2
        os.environ["MAIN_LINE_STATE_FILE"] = a.main_line_state

    import importlib
    import bt_llm_replay
    WA = importlib.import_module("wave_agent")

    llm = bt_llm_replay.LLMReplay(agent="wave", as_of=a.as_of, mode=a.mode).install(WA)
    WA._ensure_index_fresh = lambda: None            # ② 禁用自愈（否则拉当日数据 = 前视）
    d8 = a.as_of if "-" in a.as_of else "%s-%s-%s" % (a.as_of[:4], a.as_of[4:6], a.as_of[6:])

    f = WA.index_features(d8)                        # ① 显式 as-of（内部按 date 截断）
    if f is None:
        print("WAVE_NO_FEATURES as_of=%s（指数历史不足 200 根？）" % d8, file=sys.stderr)
        return 3
    prompt = WA.build_prompt(f)
    reply = WA.call_agent(prompt)
    res = WA.parse(reply)
    res["date"] = f["date"]
    res["features"] = f
    if not res.get("operation"):
        res["operation"] = "side"
    res["_asof_replay"] = {"script": "jobs/bt_wave_asof.py", "as_of": a.as_of, "llm": llm.summary()}

    out = a.out or os.path.join(os.environ.get("DATA_DIR", "/app/data"), "_bt_runs", a.as_of, "wave_state.json")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fp:
            json.dump(res, fp, ensure_ascii=False, indent=1)
    except Exception as e:
        print("WAVE_WRITE_ERR %s" % str(e)[:80], file=sys.stderr)
    print("WAVE_ASOF date=%s level=%s sub=%s op=%s conf=%s | llm=%s | out=%s"
          % (res.get("date"), res.get("level"), res.get("sub_level"), res.get("operation"),
             res.get("confidence"), llm.summary(), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
