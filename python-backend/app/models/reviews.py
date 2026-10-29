"""Databasen: tabellen `reviews` (anmeldelser).

Al SQL for anmeldelser står her. Alle funktioner der slår op ud fra et id kræver også
`restaurant_id`, og SQL'en tjekker det. Sådan kan en kunde aldrig nå en anden café's
anmeldelser, selv hvis et fremmed id bliver sendt til serveren.
"""

from typing import Iterable, Sequence
from uuid import UUID

from app.db.pool import fetch_all, fetch_one, transaction


def to_client_status(row: dict) -> str:
    """Bestemmer statussen frontenden skal se: "new", "ready" eller "sent".

    Databasen kender kun "new" og "answered". "ready" (der findes et udkast) udleder
    vi ved at se om der er et udkast, så den ikke kan komme i modstrid med replies.
    """
    if row["status"] == "answered":
        return "sent"
    if row.get("draft_text"):
        return "ready"
    return "new"


def to_client_review(row: dict) -> dict:
    """Laver en database-række om til det format frontenden forventer.

    Alt der sendes til browseren går gennem denne, så formen er ens overalt og
    kolonner frontenden ikke skal bruge ikke følger med.
    """
    return {
        "id": row["id"],
        "external_id": row["external_id"],
        "reviewer_name": row["reviewer_name"] or "",
        "rating": row["rating"],
        "review_text": row["review_text"] or "",
        "published_at": row["published_at"],
        "status": to_client_status(row),
        # Den tekst ejeren sendte vinder over udkastet fra Claude.
        "draft": row.get("final_text") or row.get("draft_text") or "",
    }


async def upsert_many(restaurant_id: UUID, reviews: Sequence) -> list[dict]:
    """Gemmer en hel hentning fra Google. Nye oprettes, kendte opdateres.

    Det sker i én transaktion: enten bliver alle gemt, eller ingen. En halv
    hentning er værre end ingen, fordi man ikke kan se at noget mangler.
    """
    if not reviews:
        return []

    saved = []
    async with transaction() as conn:
        for review in reviews:
            cursor = await conn.execute(
                """
                INSERT INTO reviews
                  (restaurant_id, external_id, reviewer_name, rating, review_text, published_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (restaurant_id, external_id) DO UPDATE SET
                  reviewer_name = EXCLUDED.reviewer_name,
                  rating        = EXCLUDED.rating,
                  review_text   = EXCLUDED.review_text,
                  published_at  = EXCLUDED.published_at
                  -- status og answered_at røres BEVIDST IKKE. Google sender de samme
                  -- anmeldelser hver gang, og ellers ville "Hent anmeldelser" sætte
                  -- alt besvaret arbejde tilbage til "ny".
                RETURNING *
                """,
                (
                    restaurant_id,
                    review.external_id,
                    review.reviewer_name or None,
                    review.rating,
                    review.review_text or None,
                    review.published_at,
                ),
            )
            saved.append(await cursor.fetchone())
        # Den første hentning med anmeldelser er caféens import. now() er det samme tidspunkt i
        # hele transaktionen, så created_at på importens anmeldelser er præcis lig first_import_at.
        await conn.execute(
            "UPDATE restaurants SET first_import_at = COALESCE(first_import_at, now()) WHERE id = %s",
            (restaurant_id,),
        )
    return saved


async def list_by_restaurant(restaurant_id: UUID) -> list[dict]:
    """Henter alle anmeldelser for en café, nyeste først, med det nyeste udkast hæftet på.

    Udkastet hentes i samme forespørgsel (LATERAL JOIN) i stedet for ét kald pr.
    anmeldelse, for hvert kald til databasen koster ventetid.
    """
    return await fetch_all(
        """
        SELECT rv.*, rp.draft_text, rp.final_text
          FROM reviews rv
          LEFT JOIN LATERAL (
            SELECT draft_text, final_text
              FROM replies
             WHERE review_id = rv.id
             ORDER BY created_at DESC
             LIMIT 1
          ) rp ON true
         WHERE rv.restaurant_id = %s
         ORDER BY rv.published_at DESC NULLS LAST, rv.created_at DESC
        """,
        (restaurant_id,),
    )


async def find_owned(review_id: UUID, restaurant_id: UUID) -> dict | None:
    """Henter én anmeldelse, men KUN hvis den hører til denne café. Ellers None."""
    return await fetch_one(
        "SELECT * FROM reviews WHERE id = %s AND restaurant_id = %s", (review_id, restaurant_id)
    )


async def find_many_owned(review_ids: Iterable[UUID], restaurant_id: UUID) -> dict[UUID, dict]:
    """Henter flere anmeldelser på én gang, kun dem der hører til caféen. Returnerer {id: række}."""
    rows = await fetch_all(
        "SELECT * FROM reviews WHERE id = ANY(%s) AND restaurant_id = %s",
        (list(review_ids), restaurant_id),
    )
    return {row["id"]: row for row in rows}


async def mark_answered(review_id: UUID, restaurant_id: UUID) -> dict | None:
    """Sætter en anmeldelse til "besvaret" uden at gemme en svartekst.

    Bruges når ejeren allerede har svaret direkte på Google. Returnerer None hvis
    anmeldelsen ikke findes hos denne café.
    """
    return await fetch_one(
        """
        UPDATE reviews SET status = 'answered', answered_at = now()
         WHERE id = %s AND restaurant_id = %s
        RETURNING *
        """,
        (review_id, restaurant_id),
    )


async def rating_summary(restaurant_id: UUID) -> dict:
    """Tal for caféens anmeldelser hos os: antal, stjernefordeling og hvor mange der er besvaret."""
    return await fetch_one(
        """
        SELECT count(*)                                   AS total,
               avg(rating)                                AS average,
               count(*) FILTER (WHERE rating = 1)         AS stars_1,
               count(*) FILTER (WHERE rating = 2)         AS stars_2,
               count(*) FILTER (WHERE rating = 3)         AS stars_3,
               count(*) FILTER (WHERE rating = 4)         AS stars_4,
               count(*) FILTER (WHERE rating = 5)         AS stars_5,
               count(*) FILTER (WHERE status = 'answered') AS answered
          FROM reviews
         WHERE restaurant_id = %s
        """,
        (restaurant_id,),
    )


async def response_hours(restaurant_id: UUID, first_import_at) -> dict:
    """Median-tid fra en anmeldelse kom ind hos os, til den blev besvaret, i timer.

    Kun anmeldelser der kom ind EFTER caféens import tæller. Dem caféen havde i forvejen, kan være
    flere år gamle, og ville gøre tallet meningsløst. Vi regner fra hvornår vi fik den (created_at),
    for det er først der, ejeren kan svare gennem os.
    """
    if first_import_at is None:
        return {"median": None, "sample": 0}
    return await fetch_one(
        """
        SELECT percentile_cont(0.5) WITHIN GROUP (
                 ORDER BY extract(epoch FROM answered_at - created_at) / 3600
               ) AS median,
               count(*) AS sample
          FROM reviews
         WHERE restaurant_id = %s AND status = 'answered' AND answered_at IS NOT NULL
           AND created_at > %s
        """,
        (restaurant_id, first_import_at),
    )


async def monthly(restaurant_id: UUID, months: int) -> list[dict]:
    """Antal anmeldelser og gennemsnit pr. måned (efter hvornår de blev skrevet), de seneste `months` måneder.

    Måneder uden anmeldelser kommer med (antal 0), så en graf ikke springer dem over. Måneder
    regnes i dansk tid.
    """
    return await fetch_all(
        """
        WITH months AS (
          SELECT generate_series(
                   date_trunc('month', now() AT TIME ZONE 'Europe/Copenhagen') - make_interval(months => %s::int - 1),
                   date_trunc('month', now() AT TIME ZONE 'Europe/Copenhagen'),
                   interval '1 month'
                 ) AS month
        )
        SELECT to_char(m.month, 'YYYY-MM') AS month,
               count(rv.id)                AS count,
               avg(rv.rating)              AS average
          FROM months m
          LEFT JOIN reviews rv
            ON rv.restaurant_id = %s
           AND rv.rating IS NOT NULL
           AND date_trunc('month', rv.published_at AT TIME ZONE 'Europe/Copenhagen') = m.month
         GROUP BY m.month
         ORDER BY m.month
        """,
        (months, restaurant_id),
    )
