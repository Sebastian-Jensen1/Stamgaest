"""Databasen: tabellen `email_changes` (links til at bekræfte en ny e-mail).

Hele filen arbejder med et FINGERAFTRYK (token_hash) af nøglen i linket, aldrig selve
nøglen. Se 009_email_changes.sql.
"""

from datetime import datetime
from uuid import UUID

from app.db.pool import fetch_all, fetch_one


async def create(*, token_hash: str, user_id: UUID, new_email: str, expires_at: datetime) -> None:
    """Gemmer et nyt ønske om at skifte e-mail og gør brugerens tidligere ønsker ubrugelige.

    Så er det altid kun det nyeste link der virker, og kun den sidst valgte adresse kan bekræftes.
    """
    await delete_for_user(user_id)
    await fetch_all(
        """
        INSERT INTO email_changes (token_hash, user_id, new_email, expires_at)
        VALUES (%s, %s, %s, %s)
        RETURNING token_hash
        """,
        (token_hash, user_id, new_email, expires_at),
    )


async def use(token_hash: str) -> dict | None:
    """Bruger et link og skifter brugerens e-mail. Giver user_id, old_email og new_email, eller None.

    Et link kan ikke bruges hvis det er ukendt, udløbet, allerede brugt, eller brugeren er slået fra.
    Alt sker i ÉN SQL-sætning: to der klikker på samme tid kan ikke begge slippe igennem, og linket
    bliver ikke brugt op, hvis skiftet fejler. Er adressen nået at blive taget af en anden konto,
    fejler sætningen med UniqueViolation (se users_email_key i 002_auth.sql).

    Alle dele af sætningen ser databasen som den var FØR ændringen, så `old` giver den gamle e-mail.
    """
    return await fetch_one(
        """
        WITH used AS (
          UPDATE email_changes c
             SET used_at = now()
            FROM users u
           WHERE c.user_id = u.id
             AND c.token_hash = %s
             AND c.used_at IS NULL
             AND c.expires_at > now()
             AND u.is_active
          RETURNING c.user_id, c.new_email, u.email AS old_email
        )
        UPDATE users u
           SET email = used.new_email
          FROM used
         WHERE u.id = used.user_id
        RETURNING u.id AS user_id, used.old_email, u.email AS new_email
        """,
        (token_hash,),
    )


async def delete_for_user(user_id: UUID) -> None:
    """Sletter alle en brugers ønsker om at skifte e-mail (brugte som ubrugte)."""
    await fetch_all("DELETE FROM email_changes WHERE user_id = %s RETURNING token_hash", (user_id,))


async def delete_expired() -> None:
    """Rydder udløbne links op, så tabellen ikke vokser for evigt."""
    await fetch_all("DELETE FROM email_changes WHERE expires_at <= now() RETURNING token_hash")
