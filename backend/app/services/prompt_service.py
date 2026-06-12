# -*- coding: utf-8 -*-
"""
Prompt 管理服务 — CRUD 操作。
"""
from typing import List, Optional, Dict
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.prompt import Prompt


def get_prompt(db: Session, name: str) -> Optional[Prompt]:
    """获取单个 prompt。"""
    return db.query(Prompt).filter(Prompt.name == name, Prompt.is_active == True).first()


def get_all_prompts(db: Session) -> List[Prompt]:
    """获取所有启用中的 prompt。"""
    return db.query(Prompt).filter(Prompt.is_active == True).order_by(Prompt.name).all()


def get_all_prompts_as_dict(db: Session) -> Dict[str, str]:
    """以 {name: content} 字典返回所有启用中的 prompt（供 pi-server 消费）。"""
    prompts = get_all_prompts(db)
    return {p.name: p.content for p in prompts}


def get_prompts_by_group(db: Session, prefix: str) -> List[Prompt]:
    """按名称前缀获取一组 prompt（如 'PANEL_'）。"""
    return db.query(Prompt).filter(
        Prompt.name.startswith(prefix),
        Prompt.is_active == True
    ).order_by(Prompt.name).all()


def upsert_prompt(
    db: Session,
    name: str,
    content: str,
    label: Optional[str] = None,
) -> Prompt:
    """创建或更新 prompt（不存在则创建，存在则更新 content 并升版本）。"""
    prompt = db.query(Prompt).filter(Prompt.name == name).first()
    if prompt:
        prompt.content = content
        prompt.version += 1
        prompt.updated_at = datetime.utcnow()
        if label:
            prompt.label = label
    else:
        prompt = Prompt(
            name=name,
            label=label or name,
            content=content,
            version=1,
        )
        db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return prompt


def delete_prompt(db: Session, name: str) -> bool:
    """软删除 prompt（设为 inactive）。"""
    prompt = db.query(Prompt).filter(Prompt.name == name).first()
    if prompt:
        prompt.is_active = False
        prompt.updated_at = datetime.utcnow()
        db.commit()
        return True
    return False


def seed_prompts(db: Session, prompts_data: Dict[str, Dict[str, str]]) -> int:
    """
    初始化 prompts 表（幂等：只插入不存在的）。
    返回本次新插入的数量。
    """
    count = 0
    for name, data in prompts_data.items():
        existing = db.query(Prompt).filter(Prompt.name == name).first()
        if not existing:
            prompt = Prompt(
                name=name,
                label=data.get("label", name),
                content=data["content"],
                version=1,
            )
            db.add(prompt)
            count += 1
    if count > 0:
        db.commit()
    return count
