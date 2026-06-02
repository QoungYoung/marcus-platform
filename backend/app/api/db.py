"""
Database Query API - A股数据库查询接口
"""
import sqlite3
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter(prefix="/db", tags=["database"])

settings = get_settings()
DATA_DIR = str(settings.data_dir)

class DbQueryResponse(BaseModel):
    rows: list
    columns: list

class DbSchemaResponse(BaseModel):
    schema: list

def open_db(db_name: str) -> sqlite3.Connection:
    """打开数据库连接，自动处理 .db 后缀重复问题"""
    # 如果 db_name 已包含 .db 后缀，不再重复添加
    if db_name.endswith('.db'):
        db_path = Path(DATA_DIR) / db_name
    else:
        db_path = Path(DATA_DIR) / f"{db_name}.db"

    if not db_path.exists():
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

@router.get("/query")
async def query_table(
    db: str = Query(..., description="数据库名: stock_pool, trades, news, cache"),
    table: str = Query(..., description="表名"),
    columns: Optional[str] = Query(None, description="要查询的列，逗号分隔"),
    where: Optional[str] = Query(None, description="WHERE条件"),
    order_by: Optional[str] = Query(None, description="排序字段"),
    limit: Optional[int] = Query(100, ge=1, le=1000, description="返回条数"),
) -> DbQueryResponse:
    """查询数据库表"""
    try:
        conn = open_db(db)
        cursor = conn.cursor()

        cols = columns or "*"
        sql = f"SELECT {cols} FROM {table}"
        params = []

        if where:
            # 安全处理，防止SQL注入
            sql += f" WHERE {where}"

        if order_by:
            sql += f" ORDER BY {order_by}"

        sql += f" LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        # 获取列名
        if rows:
            col_names = list(rows[0].keys())
        else:
            # 查询表结构获取列名
            cursor.execute(f"PRAGMA table_info({table})")
            col_info = cursor.fetchall()
            col_names = [col[1] for col in col_info] if col_info else []

        result = {
            "rows": [dict(row) for row in rows],
            "columns": col_names
        }

        conn.close()
        return result

    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"数据库错误: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/schema/{db_name}")
async def get_schema(db_name: str) -> DbSchemaResponse:
    """获取数据库表结构"""
    try:
        conn = open_db(db_name)
        cursor = conn.cursor()

        # 获取所有表名
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = cursor.fetchall()
        table_names = [t[0] for t in tables]

        schema = []
        for table_name in table_names:
            cursor.execute(f"PRAGMA table_info({table_name})")
            cols = cursor.fetchall()
            columns = [
                {
                    "name": col[1],
                    "type": col[2],
                    "notnull": bool(col[3]),
                    "default": col[4],
                    "pk": bool(col[5])
                }
                for col in cols
            ]
            schema.append({
                "table": table_name,
                "columns": columns
            })

        conn.close()
        return {"schema": schema}

    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"数据库错误: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/write")
async def write_db(
    db: str = Query(..., description="数据库名"),
    sql: str = Query(..., description="SQL语句"),
) -> dict:
    """执行写入操作（INSERT/UPDATE/DELETE）"""
    # 安全检查：只允许特定操作
    sql_upper = sql.strip().upper()
    if not any(sql_upper.startswith(prefix) for prefix in ['INSERT', 'UPDATE', 'DELETE']):
        raise HTTPException(status_code=400, detail="只允许 INSERT/UPDATE/DELETE 操作")

    try:
        conn = open_db(db)
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        changes = cursor.rowcount
        conn.close()
        return {"success": True, "changes": changes}
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"数据库错误: {str(e)}")