# -*- coding: utf-8 -*-
"""
SQLAlchemy database engine & session management.
Uses synchronous SQLAlchemy — consistent with the existing codebase pattern.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import get_settings

settings = get_settings()

# 自动选择：Docker 内用 postgres 服务名，本地用 localhost
DATABASE_URL = settings.DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # 连接前检测有效性
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI 依赖：获取数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """创建所有表（如果不存在）。"""
    # 确保所有模型被导入，让它们注册到 Base.metadata
    import app.models.prompt  # noqa: F401
    Base.metadata.create_all(bind=engine)
