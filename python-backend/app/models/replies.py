"""Databasen: tabellen `replies` (svar-udkast og afsendte svar).

En anmeldelse kan have flere rækker her, fordi man kan trykke "Generér igen".
Den nyeste række er den der tæller.
"""

from uuid import UUID

from app.db.pool import fetch_all, fetch_one_required, transaction


async def insert_draft(
    *, review_id: UUID, draft_text: str, tone: str | None, model: str | None
) -> dict:
    """Gemmer et nyt udkast fra Claude.

    Det bliver altid en NY række, så tidligere forsøg bliver stående som historik.
    Historikken kan senere bruges til at forbedre prompten.
    """
    return await fetch_one_required(
        """
        INSERT INTO replies (review_id, draft_text, tone, model)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        (review_id, draft_text, tone or None, model or None),
    )


async def mark_sent(review_id: UUID, restaurant_id: UUID, final_text: str) -> dict | None:
    """Gemmer det svar ejeren sendte, og markerer anmeldelsen som besvaret.

    Alt sker i samme transaktion, så det ene aldrig sker uden det andet. Hører
    anmeldelsen ikke til denne café, sker der INTET, og None returneres.
    """
    async with transaction() as conn:
        # Trin 1: sæt anmeldelsen til besvaret, men kun hvis den hører til caféen.
        cursor = await conn.execute(
            """
            UPDATE reviews SET status = 'answered', answered_at = now()
             WHERE id = %s AND restaurant_id = %s
            RETURNING id
            """,
            (review_id, restaurant_id),
        )
        if await cursor.fetchone() is None:
            return None

        # Trin 2: læg den endelige tekst på det NYESTE udkast. Ældre står urørt.
        cursor = await conn.execute(
            """
            UPDATE replies
               SET final_text = %s, status = 'sent', sent_at = now()
             WHERE id = (
               SELECT id FROM replies WHERE review_id = %s ORDER BY created_at DESC LIMIT 1
             )
            RETURNING *
            """,
            (final_text, review_id),
        )
        reply = await cursor.fetchone()

        # Trin 3: fandt vi intet udkast, har ejeren skrevet svaret selv. Så opretter
        # vi en række med model = NULL, så man kan se at det ikke kom fra Claude.
        if reply is None:
            cursor = await conn.execute(
                """
                INSERT INTO replies (review_id, draft_text, final_text, status, sent_at, model)
                VALUES (%s, %s, %s, 'sent', now(), NULL)
                RETURNING *
                """,
                (review_id, final_text, final_text),
            )
            reply = await cursor.fetchone()
        return reply


async def list_by_review(review_id: UUID) -> list[dict]:
    """Henter alle udkast og svar til én anmeldelse, nyeste først."""
    return await fetch_all(
        "SELECT * FROM replies WHERE review_id = %s ORDER BY created_at DESC",
        (review_id,),
    )
