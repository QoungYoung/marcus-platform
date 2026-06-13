#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键同步 PROMPT_SEEDS 到数据库（更新已存在的记录，幂等安全）。
运行：python scripts/reseed_prompts.py
"""

import sys
from pathlib import Path

# 确保 backend 在 path 中
backend_root = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(backend_root))

from app.database import SessionLocal
from app.db.prompt_seeds import PROMPT_SEEDS
from app.models.prompt import Prompt

db = SessionLocal()
updated = 0
skipped = 0

for name, data in PROMPT_SEEDS.items():
    existing = db.query(Prompt).filter(Prompt.name == name).first()
    if existing:
        if existing.content != data["content"]:
            existing.content = data["content"]
            existing.label = data.get("label", name)
            existing.version = (existing.version or 0) + 1
            updated += 1
            print(f"✅ 已更新: {name} (v{existing.version})")
        else:
            skipped += 1
            print(f"⏭️ 内容未变，跳过: {name}")
    else:
        prompt = Prompt(
            name=name,
            label=data.get("label", name),
            content=data["content"],
            version=1,
        )
        db.add(prompt)
        updated += 1
        print(f"🆕 新建: {name}")

if updated > 0:
    db.commit()
    print(f"\n总计: {updated} 条更新/新建, {skipped} 条跳过")
else:
    print(f"\n无变化，{skipped} 条已是最新")
    db.rollback()

db.close()
