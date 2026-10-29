"""Databasen: tabellerne `usage_events` (forbrug af betalte kald) og `usage_alerts`.

Lofterne tælles i databasen, ikke i hukommelsen, så de holder efter en genstart og på tværs af
flere serverprocesser. Dag og måned regnes i dansk tid.
"""

from dataclasses import dataclass
from uuid import UUID

from app.core.config import Caps
from app.db.pool import fetch_all, fetch_one, transaction

DRAFT = "claude_draft"
FETCH = "places_fetch"
PROFILE = "claude_profile"
TOPICS = "claude_topics"

# Rækkefølgen bruges, når der skal vælges hvilket loft der blev ramt først.
SCOPES = ("cafe_day", "cafe_month", "all_day", "all_month")

# Hvor meget er der brugt i dag og i denne måned, for én café og for alle? Alt tæller med undtagen
# kald der aldrig blev sendt ("skipped"). Også fejlede kald tæller: de kan koste penge, og ellers
# kunne man prøve igen og igen uden at ramme loftet. Reserverede tæller, så samtidige kald ikke
# kan snyde.
_COUNT_SQL = """
    WITH bounds AS (
      SELECT date_trunc('day', now() AT TIME ZONE 'Europe/Copenhagen') AT TIME ZONE 'Europe/Copenhagen' AS day_start,
             date_trunc('month', now() AT TIME ZONE 'Europe/Copenhagen') AT TIME ZONE 'Europe/Copenhagen' AS month_start
    )
    SELECT count(e.id) FILTER (WHERE e.restaurant_id = %(restaurant_id)s AND e.created_at >= b.day_start) AS cafe_day,
           count(e.id) FILTER (WHERE e.restaurant_id = %(restaurant_id)s) AS cafe_month,
           count(e.id) FILTER (WHERE e.created_at >= b.day_start) AS all_day,
           count(e.id) AS all_month
      FROM bounds b
      LEFT JOIN usage_events e
        ON e.kind = %(kind)s AND e.status <> 'skipped' AND e.created_at >= b.month_start
"""


@dataclass(frozen=True)
class Reservation:
    """Resultatet af at bede om plads til et antal kald.

    `ids` er de reserverede rækker (tom hvis det blev afvist). `used_before` er hvor meget der var
    brugt FØR reservationen, pr. loft. `refused` er det loft der stoppede det, eller None.
    """

    ids: list[int]
    used_before: dict[str, int]
    refused: str | None


async def reserve(*, kind: str, restaurant_id: UUID, user_id: UUID | None, count: int, caps: Caps) -> Reservation:
    """Reserverer plads til `count` kald, hvis ingen lofter bliver overskredet. Ellers reserveres intet.

    Tjek og reservation sker i ÉN transaktion under en lås for denne slags kald. To samtidige
    forespørgsler må derfor vente på hinanden og kan ikke begge slippe forbi loftet.
    """
    async with transaction() as conn:
        # Låsen holdes til transaktionen slutter. Den er kort, så ventetiden er lille.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"usage:{kind}",))
        cursor = await conn.execute(_COUNT_SQL, {"restaurant_id": restaurant_id, "kind": kind})
        used = {scope: int(value) for scope, value in (await cursor.fetchone()).items()}

        for scope in SCOPES:
            if used[scope] + count > getattr(caps, scope):
                return Reservation(ids=[], used_before=used, refused=scope)

        cursor = await conn.execute(
            """
            INSERT INTO usage_events (kind, restaurant_id, user_id)
            SELECT %s, %s, %s FROM generate_series(1, %s)
            RETURNING id
            """,
            (kind, restaurant_id, user_id, count),
        )
        ids = [row["id"] for row in await cursor.fetchall()]
    return Reservation(ids=ids, used_before=used, refused=None)


async def finish(event_id: int, *, model: str | None = None, input_tokens: int | None = None, output_tokens: int | None = None) -> None:
    """Noterer at et reserveret kald lykkedes, og hvor mange tokens det brugte."""
    await fetch_all(
        """
        UPDATE usage_events SET status = 'done', model = %s, input_tokens = %s, output_tokens = %s
         WHERE id = %s RETURNING id
        """,
        (model, input_tokens, output_tokens, event_id),
    )


async def fail(event_ids: list[int]) -> None:
    """Noterer at kald blev sendt, men fejlede. De tæller stadig med i lofterne."""
    await _set_status(event_ids, "failed")


async def skip(event_ids: list[int]) -> None:
    """Noterer at reserverede kald aldrig blev sendt. De frigives, så de ikke tæller med."""
    await _set_status(event_ids, "skipped")


async def _set_status(event_ids: list[int], status: str) -> None:
    """Sætter status på reserverede rækker (rækker der allerede er afsluttet, røres ikke)."""
    if event_ids:
        await fetch_all(
            "UPDATE usage_events SET status = %s WHERE id = ANY(%s) AND status = 'reserved' RETURNING id",
            (status, event_ids),
        )


async def record_done(*, kind: str, restaurant_id: UUID | None) -> None:
    """Noterer et kald der allerede er sket (fx når vi selv opretter en kunde i admin-værktøjet)."""
    await fetch_all(
        "INSERT INTO usage_events (kind, restaurant_id, status) VALUES (%s, %s, 'done') RETURNING id",
        (kind, restaurant_id),
    )


async def current(kind: str, restaurant_id: UUID | None = None) -> dict[str, int]:
    """Hvor meget er brugt i dag og i denne måned (for én café, og for alle)."""
    row = await fetch_one(_COUNT_SQL, {"restaurant_id": restaurant_id, "kind": kind})
    return {scope: int(value) for scope, value in row.items()}


async def claim_alert(*, kind: str, scope: str, level: int) -> bool:
    """Reserverer retten til at sende en advarsel i dag. Returnerer False hvis den allerede er sendt."""
    row = await fetch_one(
        """
        INSERT INTO usage_alerts (kind, scope, level, day)
        VALUES (%s, %s, %s, (now() AT TIME ZONE 'Europe/Copenhagen')::date)
        ON CONFLICT DO NOTHING
        RETURNING level
        """,
        (kind, scope, level),
    )
    return row is not None


async def summary(days: int) -> list[dict]:
    """Forbrug pr. café de sidste `days` dage. Bruges af admin-værktøjet."""
    return await fetch_all(
        """
        SELECT r.id AS restaurant_id, coalesce(r.name, '(slettet café)') AS restaurant_name, r.ai_enabled,
               count(*) FILTER (WHERE e.kind = 'claude_draft') AS drafts,
               coalesce(sum(e.input_tokens) FILTER (WHERE e.kind = 'claude_draft'), 0) AS input_tokens,
               coalesce(sum(e.output_tokens) FILTER (WHERE e.kind = 'claude_draft'), 0) AS output_tokens,
               count(*) FILTER (WHERE e.kind = 'places_fetch') AS fetches,
               count(*) FILTER (WHERE e.kind = 'claude_profile') AS profiles,
               coalesce(sum(e.input_tokens) FILTER (WHERE e.kind = 'claude_profile'), 0) AS profile_input_tokens,
               coalesce(sum(e.output_tokens) FILTER (WHERE e.kind = 'claude_profile'), 0) AS profile_output_tokens,
               count(*) FILTER (WHERE e.kind = 'claude_topics') AS topics,
               coalesce(sum(e.input_tokens) FILTER (WHERE e.kind = 'claude_topics'), 0) AS topics_input_tokens,
               coalesce(sum(e.output_tokens) FILTER (WHERE e.kind = 'claude_topics'), 0) AS topics_output_tokens
          FROM usage_events e
          LEFT JOIN restaurants r ON r.id = e.restaurant_id
         WHERE e.status <> 'skipped' AND e.created_at >= now() - make_interval(days => %s)
         GROUP BY r.id, r.name, r.ai_enabled
         ORDER BY drafts DESC, fetches DESC
        """,
        (days,),
    )
