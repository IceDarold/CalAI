"""Food database search — looks up USDA foods by name.

Uses simple ILIKE matching with scoring.
In future: vector embeddings for semantic search.
"""

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def search_food(
    session: AsyncSession,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """Search for foods matching the query.

    Returns list of {id, name, kcal_per_100g, protein_per_100g, fat_per_100g,
                      carbs_per_100g, source, score}
    Score is 0-100 based on match quality.
    """
    if not query or len(query.strip()) < 2:
        return []

    query_lower = query.strip().lower()

    # Strategy: try exact match first, then word-by-word, then partial
    results = await _search_by_words(session, query_lower, limit)
    return results


async def _search_by_words(
    session: AsyncSession,
    query_lower: str,
    limit: int,
) -> list[dict]:
    """Search by splitting query into words and matching each."""
    words = [w for w in re.split(r'[\s,;]+', query_lower) if len(w) >= 2]
    if not words:
        words = [query_lower]

    # Build a query that scores by number of matching words and position
    conditions = []
    params: dict[str, str] = {}
    for i, word in enumerate(words):
        param_name = f"w{i}"
        params[param_name] = f"%{word}%"
        conditions.append(
            f"(CASE WHEN name_lower LIKE :{param_name} THEN 1 ELSE 0 END)"
        )

    score_expr = " + ".join(conditions)
    where_clause = " OR ".join(f"name_lower LIKE :w{i}" for i in range(len(words)))

    sql = text(f"""
        SELECT id, name, kcal_per_100g, protein_per_100g,
               fat_per_100g, carbs_per_100g, source,
               ({score_expr}) * 30 +
               CASE WHEN name_lower = :exact THEN 100 ELSE 0 END +
               CASE WHEN name_lower LIKE :prefix THEN 50 ELSE 0 END
               AS score
        FROM food_items
        WHERE {where_clause}
        ORDER BY score DESC, name_lower ASC
        LIMIT :limit
    """)

    params["exact"] = query_lower
    params["prefix"] = f"{query_lower}%"
    params["limit"] = limit

    result = await session.execute(sql, params)
    rows = result.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "kcal_per_100g": row[2] or 0,
            "protein_per_100g": row[3] or 0,
            "fat_per_100g": row[4] or 0,
            "carbs_per_100g": row[5] or 0,
            "source": row[6] or "usda",
            "score": row[7] or 0,
        }
        for row in rows
    ]


async def get_food_by_id(session: AsyncSession, food_id: int) -> dict | None:
    """Get a single food item by its database ID."""
    sql = text("SELECT id, name, kcal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g, source FROM food_items WHERE id = :id")
    result = await session.execute(sql, {"id": food_id})
    row = result.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "kcal_per_100g": row[2] or 0,
        "protein_per_100g": row[3] or 0,
        "fat_per_100g": row[4] or 0,
        "carbs_per_100g": row[5] or 0,
        "source": row[6],
    }
