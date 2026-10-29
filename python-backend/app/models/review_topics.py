"""Databasen: tabellen `review_topics` (emner og stemning pr. anmeldelse).

Alle funktioner tager `restaurant_id` og filtrerer på den i selve SQL'en, så en café aldrig kan
se eller ændre en anden cafés emner.
"""

from uuid import UUID

from psycopg.types.json import Jsonb

from app.db.pool import fetch_all, fetch_one, transaction

# "Skal mærkes" = ingen mærkning endnu, mærket med en ældre emneliste, eller teksten er ændret
# siden. Bruges både til at finde dem og til at tælle dem.
_PENDING_WHERE = """
    rv.restaurant_id = %(restaurant_id)s
    AND (rt.review_id IS NULL
         OR rt.taxonomy_version <> %(version)s
         OR rt.text_hash <> md5(coalesce(rv.review_text, '')))
"""


async def list_pending(restaurant_id: UUID, version: int, limit: int) -> list[dict]:
    """Anmeldelser hos caféen, der mangler at blive mærket, nyeste først."""
    return await fetch_all(
        f"""
        SELECT rv.id, rv.rating, rv.review_text
          FROM reviews rv
          LEFT JOIN review_topics rt ON rt.review_id = rv.id
         WHERE {_PENDING_WHERE}
         ORDER BY rv.published_at DESC NULLS LAST, rv.created_at DESC
         LIMIT %(limit)s
        """,
        {"restaurant_id": restaurant_id, "version": version, "limit": limit},
    )


async def count_pending(restaurant_id: UUID, version: int) -> int:
    """Hvor mange anmeldelser hos caféen der mangler at blive mærket."""
    row = await fetch_one(
        f"""
        SELECT count(*) AS n
          FROM reviews rv
          LEFT JOIN review_topics rt ON rt.review_id = rv.id
         WHERE {_PENDING_WHERE}
        """,
        {"restaurant_id": restaurant_id, "version": version},
    )
    return int(row["n"])


async def save_many(
    restaurant_id: UUID, results: list[tuple[UUID, str, dict]], *, version: int, model: str | None
) -> None:
    """Gemmer mærkningen for flere anmeldelser. `results` er (anmeldelses-id, den mærkede tekst, emner).

    Rækken oprettes ud fra reviews med caféens id i WHERE, så en anmeldelse fra en anden café
    aldrig kan få en mærkning. Fingeraftrykket regnes af den tekst, der faktisk blev mærket: er
    teksten ændret i mellemtiden, passer det ikke, og anmeldelsen mærkes igen næste gang.
    """
    if not results:
        return
    async with transaction() as conn:
        for review_id, text, topics in results:
            await conn.execute(
                """
                INSERT INTO review_topics (review_id, restaurant_id, topics, taxonomy_version, text_hash, model)
                SELECT rv.id, rv.restaurant_id, %s, %s, md5(%s), %s
                  FROM reviews rv
                 WHERE rv.id = %s AND rv.restaurant_id = %s
                ON CONFLICT (review_id) DO UPDATE SET
                  topics           = EXCLUDED.topics,
                  taxonomy_version = EXCLUDED.taxonomy_version,
                  text_hash        = EXCLUDED.text_hash,
                  model            = EXCLUDED.model
                """,
                (Jsonb(topics), version, text or "", model, review_id, restaurant_id),
            )


async def topic_counts(restaurant_id: UUID, version: int) -> list[dict]:
    """Hvor mange gange hvert emne er nævnt hos caféen, delt op efter stemning.

    Kun mærkninger med den nuværende emneliste tæller, så et emne der er fjernet fra listen,
    ikke hænger ved.
    """
    return await fetch_all(
        """
        SELECT t.key AS topic, t.value AS sentiment, count(*) AS n
          FROM review_topics rt
          CROSS JOIN LATERAL jsonb_each_text(rt.topics) AS t(key, value)
         WHERE rt.restaurant_id = %s AND rt.taxonomy_version = %s
         GROUP BY t.key, t.value
        """,
        (restaurant_id, version),
    )


async def count_analyzed(restaurant_id: UUID, version: int) -> int:
    """Hvor mange af caféens anmeldelser med tekst der er mærket med den nuværende emneliste."""
    row = await fetch_one(
        """
        SELECT count(*) AS n
          FROM review_topics rt
          JOIN reviews rv ON rv.id = rt.review_id AND rv.restaurant_id = rt.restaurant_id
         WHERE rt.restaurant_id = %s AND rt.taxonomy_version = %s
           AND coalesce(btrim(rv.review_text), '') <> ''
        """,
        (restaurant_id, version),
    )
    return int(row["n"])
