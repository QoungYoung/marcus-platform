# -*- coding: utf-8 -*-
"""
News API endpoints.
"""
import sys
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.models.news import NewsResponse, NewsListResponse, SentimentResponse

settings = get_settings()

router = APIRouter(prefix="/news", tags=["News"])


@router.get("", response_model=NewsListResponse)
async def get_news(
    symbol: Optional[str] = Query(None, description="Filter by stock symbol"),
    limit: int = Query(20, ge=1, le=100, description="Number of records"),
    page: int = Query(1, ge=1, description="Page number"),
):
    """
    Get news feed.
    Data source: AKShare news collection with DeepSeek AI sentiment analysis.
    """
    import sqlite3

    db_file = settings.data_dir / "news.db"
    if not db_file.exists():
        return NewsListResponse(news=[], total=0, page=page, page_size=limit)

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    curs = conn.cursor()

    # Build query
    where_clause = ""
    params = []
    if symbol:
        # Remove prefix for database lookup
        code = symbol[2:] if len(symbol) > 4 else symbol
        where_clause = "WHERE keyword = ?"
        params.append(code)

    # Get total count
    count_sql = f"SELECT COUNT(*) as cnt FROM news {where_clause}"
    curs.execute(count_sql, params)
    total = curs.fetchone()["cnt"]

    # Get paginated news
    offset = (page - 1) * limit
    sql = f"""
        SELECT id, title, content, source, publish_time, sentiment,
               category, keyword, concepts, url
        FROM news
        {where_clause}
        ORDER BY publish_time DESC
        LIMIT ? OFFSET ?
    """
    curs.execute(sql, params + [limit, offset])
    rows = curs.fetchall()

    news_list = []
    for row in rows:
        try:
            publish_time = datetime.fromisoformat(row["publish_time"].replace("Z", "+00:00"))
        except Exception:
            publish_time = datetime.now()

        # Parse sentiment score from sentiment string
        sentiment = row["sentiment"] or "neutral"
        if sentiment == "positive":
            sentiment_score = 80.0
        elif sentiment == "negative":
            sentiment_score = 20.0
        else:
            sentiment_score = 50.0

        # Parse concepts (comma-separated in DB)
        concepts_raw = row["concepts"] or ""
        concepts = [c.strip() for c in concepts_raw.split(",") if c.strip()] if concepts_raw else []

        news_list.append(NewsResponse(
            id=row["id"],
            title=row["title"],
            content=row["content"],
            source=row["source"],
            publish_time=publish_time,
            sentiment=sentiment,
            sentiment_score=sentiment_score,
            category=row["category"],        # industry sector
            industry=row["category"],         # alias for clarity
            keyword=row["keyword"],           # event type
            concepts=concepts,                # hot concepts
            symbols=[symbol] if symbol else [],
            url=row["url"],
        ))

    conn.close()

    return NewsListResponse(
        news=news_list,
        total=total,
        page=page,
        page_size=limit,
    )


@router.get("/sentiment", response_model=SentimentResponse)
async def get_market_sentiment():
    """
    Get overall market sentiment based on recent news.
    """
    try:
        from news_analyzer import get_news_sentiment_simple

        sentiment_data = get_news_sentiment_simple()

        return SentimentResponse(
            score=sentiment_data.get("score", 50),
            positive_count=sentiment_data.get("positive", 0),
            negative_count=sentiment_data.get("negative", 0),
            neutral_count=sentiment_data.get("neutral", 0),
            dominant_sentiment=sentiment_data.get("dominant", "neutral"),
            updated_at=datetime.now(),
        )
    except Exception:
        return SentimentResponse(
            score=50,
            positive_count=0,
            negative_count=0,
            neutral_count=0,
            dominant_sentiment="neutral",
            updated_at=datetime.now(),
        )
