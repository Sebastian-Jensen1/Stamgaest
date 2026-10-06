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
