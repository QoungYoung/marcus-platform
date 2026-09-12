# -*- coding: utf-8 -*-
"""wolf_map_dirs_to_themes.py — 把他的**自由文本方向**映射到我们的 13 主题（**用 LLM，不用关键词**）。

用户明确要求：涉及语料/结构化的开发，先评估用 dsh 直读，**拒绝关键词提取**。
本脚本把 `wolf_actual_mainline.doing[].dir` 里的**唯一方向串**一次性交给 dsh，
要求返回 {"mapping": {"方向串": "13主题之一 或 非主题"}}，并落表 `wolf_dir_theme_map` 供复用/审计。

用法：python jobs/wolf_map_dirs_to_themes.py [--force]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")

CHAT_URL = os.getenv("MAIN_LINE_CHAT_URL", "http://marcus-dsh:3001/chat")
THEMES = ["AI/算力/科技", "半导体/芯片", "医药", "新能源/电池", "机器人/智能制造", "汽车/智驾",
          "消费/内需", "农业", "电力/公用", "稳增长/基建", "资源/周期", "金融", "军工/航天"]

DDL = """
CREATE TABLE IF NOT EXISTS wolf_dir_theme_map (
    dir_text   TEXT PRIMARY KEY,
    theme      TEXT,
    raw_reply  TEXT,
    mapped_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _extract_json(reply: str) -> Dict[str, Any]:
    s = (reply or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if m:
        s = m.group(1)
    else:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j + 1]
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else {}
    except Exception:
        return {}


def unique_dirs(db) -> List[str]:
    from sqlalchemy import text
    rows = db.execute(text("SELECT payload FROM wolf_actual_mainline WHERE status='ok'")).mappings().all()
    dirs: Dict[str, None] = {}
    for r in rows:
        p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"] or "{}")
        for x in (p.get("doing") or []):
            d = (x.get("dir") or "").strip()
            if d:
                dirs.setdefault(d, None)
    return list(dirs.keys())


def map_via_llm(dirs: List[str], batch: int = 60) -> Dict[str, str]:
    """分批送 dsh（每批 ≤60 个方向串），返回 {dir: theme}。"""
    import requests
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    out: Dict[str, str] = {}
    for i in range(0, len(dirs), batch):
        chunk = dirs[i:i + batch]
        msg = ("下面是一位A股交易者（狼大）自己操作方向的原始说法列表。请把**每一个**映射到给定的 13 个主题之一；"
               "如果它不是一个板块/主题（例如是仓位管理、指数、个股泛指、打野票名、宏观叙事），请映射为『非主题』。\n"
               "只输出一个 JSON 对象，不要 markdown、不要解释：\n"
               '{"mapping": {"原始说法": "主题名或非主题"}}\n\n'
               "13 个主题：" + "、".join(THEMES) + "\n\n原始说法列表：\n" +
               "\n".join("- " + d for d in chunk))
        for att in (1, 2):
            try:
                r = requests.post(CHAT_URL, json={"message": msg, "session_id": "wolfmap_%d" % i},
                                  headers={"Content-Type": "application/json"}, timeout=240, verify=False)
                r.raise_for_status()
                obj = _extract_json(str((r.json() or {}).get("reply") or ""))
                m = obj.get("mapping") or {}
                got = {k: v for k, v in m.items() if isinstance(k, str) and isinstance(v, str)}
                if got:
                    out.update(got)
                    break
            except Exception as e:
                print("[map] 批次 %d 第 %d 次失败: %s: %s" % (i // batch + 1, att, type(e).__name__, str(e)[:70]), flush=True)
                time.sleep(3)
        time.sleep(1.0)
    return out


def main() -> int:
    from app.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text(DDL))
        db.commit()
        dirs = unique_dirs(db)
        if not dirs:
            print("[map] 还没有 doing 数据（先跑 wolf_actual_mainline_llm.py）")
            return 1
        print("[map] 唯一方向串 %d 个，送 dsh 映射…" % len(dirs), flush=True)
        mapping = map_via_llm(dirs)
        n_ok = n_other = 0
        for d, th in mapping.items():
            th = th.strip()
            if th not in THEMES:
                th = "非主题"
            if th == "非主题":
                n_other += 1
            else:
                n_ok += 1
            db.execute(text("""
                INSERT INTO wolf_dir_theme_map (dir_text, theme, raw_reply, mapped_at)
                VALUES (:d, :t, NULL, now())
                ON CONFLICT (dir_text) DO UPDATE SET theme=EXCLUDED.theme, mapped_at=now()
            """), {"d": d, "t": th})
        db.commit()
        print("[map] 完成：映射到主题 %d 个 / 非主题 %d 个（共 %d）" % (n_ok, n_other, len(mapping)), flush=True)
        for d, th in list(mapping.items())[:15]:
            print("   %-30s → %s" % (d[:30], th), flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
