# -*- coding: utf-8 -*-
"""
Prompt 管理 API — CRUD 接口。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, Dict, List
from app.database import get_db
from app.services import prompt_service

router = APIRouter(tags=["prompts"])


# ── Pydantic 模型 ──

class PromptResponse(BaseModel):
    id: int
    name: str
    label: Optional[str]
    content: str
    version: int
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class PromptUpsertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1)
    label: Optional[str] = Field(None, max_length=200)


class PromptDictResponse(BaseModel):
    """{name: content} 键值对响应（供 pi-server 消费）。"""
    prompts: Dict[str, str]
    count: int


# ── 路由 ──

@router.get("/prompts", response_model=PromptDictResponse)
def list_prompts_dict(db: Session = Depends(get_db)):
    """
    获取所有启用中的 prompt，返回 {name: content} 键值对。
    Pi-Server 启动时调用此接口获取所有 prompt。
    """
    prompts = prompt_service.get_all_prompts_as_dict(db)
    return PromptDictResponse(prompts=prompts, count=len(prompts))


@router.get("/prompts/all", response_model=List[PromptResponse])
def list_prompts_full(db: Session = Depends(get_db)):
    """获取所有启用中的 prompt 完整信息。"""
    prompts = prompt_service.get_all_prompts(db)
    return prompts


@router.get("/prompts/{name}", response_model=PromptResponse)
def get_prompt_by_name(name: str, db: Session = Depends(get_db)):
    """获取指定 prompt 的完整信息。"""
    prompt = prompt_service.get_prompt(db, name)
    if not prompt:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    return prompt


@router.put("/prompts", response_model=PromptResponse)
def upsert_prompt(req: PromptUpsertRequest, db: Session = Depends(get_db)):
    """创建或更新 prompt（更新时版本号 +1）。"""
    prompt = prompt_service.upsert_prompt(db, req.name, req.content, req.label)
    return prompt


@router.delete("/prompts/{name}")
def delete_prompt(name: str, db: Session = Depends(get_db)):
    """软删除 prompt（设为 inactive）。"""
    if not prompt_service.delete_prompt(db, name):
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    return {"status": "ok", "message": f"Prompt '{name}' deleted"}
