"""
Database Query API - A股票据库查询接口

双后端路由：
- stock_pool / etf_pool → PostgreSQL（主数据）
- trades / news / cache → SQLite（旧数据 / 业务日志）
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

# 这些数据集已迁移至 PostgreSQL，通用查询接口对其读取 PG
PG_DATABASES = {"stock_pool", "etf_pool"}

class DbQueryResponse(BaseModel):
    rows: list
    columns: list

class DbSchemaResponse(BaseModel):
    schema: list

def _pg_conn():
    """PostgreSQL 连接（psycopg2，来自 DATABASE_URL）。"""
    import os
    import psycopg2
    url = os.environ.get("DATABASE_URL", "postgresql://marcus:marcus123@localhost:5432/marcus_trading")
    return psycopg2.connect(url)

def open_db(db_name: str):
    """打开数据库连接：stock_pool/etf_pool 走 PostgreSQL，其余走 SQLite。"""
    if db_name in PG_DATABASES:
        return _pg_conn()

    # SQLite 路径（自动处理 .db 后缀重复问题）
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
def query_table(
    db: str = Query(..., description="数据库名: stock_pool(PG), etf_pool(PG), trades, news, cache"),
    table: str = Query(..., description="表名"),
    columns: Optional[str] = Query(None, description="要查询的列，逗号分隔"),
    where: Optional[str] = Query(None, description="WHERE条件"),
    order_by: Optional[str] = Query(None, description="排序字段"),
    limit: Optional[int] = Query(100, ge=1, le=1000, description="返回条数"),
) -> DbQueryResponse:
    """查询数据库表（PG 主数据 / SQLite 旧数据）"""
    try:
        conn = open_db(db)
        is_pg = db in PG_DATABASES
        cursor = conn.cursor()

        cols = columns or "*"
        sql = f"SELECT {cols} FROM {table}"
        params = []

        if where:
            # 安全处理，防止SQL注入
            sql += f" WHERE {where}"

        if order_by:
            sql += f" ORDER BY {order_by}"

        placeholder = "%s" if is_pg else "?"
        sql += f" LIMIT {placeholder}"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        # 获取列名
        if is_pg:
            col_names = [d[0] for d in cursor.description] if cursor.description else []
            if not col_names:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s ORDER BY ordinal_position",
                    (table,)
                )
                col_names = [r[0] for r in cursor.fetchall()]
            result = {
                "rows": [dict(zip(col_names, row)) for row in rows],
                "columns": col_names
            }
        else:
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
        print(f"[DB] SQLite error on {table}: {e}", flush=True)
        raise HTTPException(status_code=500, detail=f"数据库错误: {str(e)}")
    except Exception as e:
        print(f"[DB] Unexpected error on {table}: {e}", flush=True)
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/schema/{db_name}")
def get_schema(db_name: str) -> DbSchemaResponse:
    """获取数据库表结构（PG 用 information_schema，SQLite 用 sqlite_master）"""
    try:
        conn = open_db(db_name)
        cursor = conn.cursor()

        if db_name in PG_DATABASES:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            table_names = [t[0] for t in cursor.fetchall()]

            schema = []
            for table_name in table_names:
                cursor.execute(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s "
                    "ORDER BY ordinal_position",
                    (table_name,)
                )
                cols = cursor.fetchall()
                cursor.execute(
                    "SELECT a.attname "
                    "FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indrelid "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                    "WHERE c.relname = %s AND i.indisprimary",
                    (table_name,)
                )
                pk_cols = {r[0] for r in cursor.fetchall()}
                columns = [
                    {
                        "name": r[0],
                        "type": r[1],
                        "notnull": r[2] == "NO",
                        "default": r[3],
                        "pk": r[0] in pk_cols,
                    }
                    for r in cols
                ]
                schema.append({
                    "table": table_name,
                    "columns": columns
                })
        else:
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
def write_db(
    db: str = Query(..., description="数据库名"),
    sql: str = Query(..., description="SQL语句"),
) -> dict:
    """执行写入操作（INSERT/UPDATE/DELETE）"""
    # PostgreSQL 主数据集只读，不允许通过接口写入
    if db in PG_DATABASES:
        raise HTTPException(status_code=400, detail="PostgreSQL 数据集只读，不支持写入")

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
