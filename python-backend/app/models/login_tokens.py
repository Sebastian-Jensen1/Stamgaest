"""Databasen: tabellen `login_tokens` (links der logger direkte ind).

Hele filen arbejder med et FINGERAFTRYK (token_hash) af nøglen i linket, aldrig selve
nøglen. Se 011_login_tokens.sql.
"""

from datetime import datetime
from uuid import UUID

from app.db.pool import fetch_all, fetch_one


async def create(*, token_hash: str, user_id: UUID, expires_at: datetime) -> None:
    """Gemmer et nyt login-link og gør brugerens tidligere login-links ubrugelige.

    Så er det altid kun det nyeste link i indbakken der virker.
    """
    await delete_for_user(user_id)
    await fetch_all(
        "INSERT INTO login_tokens (token_hash, user_id, expires_at) VALUES (%s, %s, %s) RETURNING token_hash",
        (token_hash, user_id, expires_at),
    )


async def use(token_hash: str) -> dict | None:
    """Bruger et login-link: giver brugerens id, e-mail og café, eller None hvis linket ikke kan bruges.

    Et link kan ikke bruges hvis det er ukendt, udløbet, allerede brugt, brugeren er slået fra,
    eller kunden stadig kun er inviteret. Det hele sker i ÉN SQL-sætning, så to der klikker på
    samme link på samme tid ikke begge bliver logget ind.
    """
    return await fetch_one(
        """
        UPDATE login_tokens t
           SET used_at = now()
          FROM users u
         WHERE t.user_id = u.id
           AND t.token_hash = %s
           AND t.used_at IS NULL
           AND t.expires_at > now()
           AND u.is_active
           AND NOT u.invite_pending
        RETURNING u.id AS user_id, u.email, u.restaurant_id
        """,
        (token_hash,),
    )


async def delete_for_user(user_id: UUID) -> None:
    """Sletter alle en brugers login-links (brugte som ubrugte)."""
    await fetch_all("DELETE FROM login_tokens WHERE user_id = %s RETURNING token_hash", (user_id,))


async def delete_expired() -> None:
    """Rydder udløbne links op, så tabellen ikke vokser for evigt."""
    await fetch_all("DELETE FROM login_tokens WHERE expires_at <= now() RETURNING token_hash")
