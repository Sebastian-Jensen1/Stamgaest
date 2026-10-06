"""Databasen: tabellen `sessions` (hvem der er logget ind lige nu).

Hele filen arbejder med et FINGERAFTRYK (token_hash) af den nøgle browseren har, aldrig
selve nøglen. Se 002_auth.sql.
"""

from datetime import datetime
from uuid import UUID

from app.db.pool import fetch_all, fetch_one


async def create(*, token_hash: str, user_id: UUID, expires_at: datetime) -> None:
    """Gemmer en ny session."""
    await fetch_all(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s) RETURNING token_hash",
        (token_hash, user_id, expires_at),
    )


async def find_valid(token_hash: str) -> dict | None:
    """Finder brugeren bag en session, hvis sessionen ikke er udløbet og brugeren stadig er aktiv."""
    return await fetch_one(
        """
        SELECT s.token_hash, u.id AS user_id, u.email, u.restaurant_id
          FROM sessions s
          JOIN users u ON u.id = s.user_id
         WHERE s.token_hash = %s AND s.expires_at > now() AND u.is_active
        """,
        (token_hash,),
    )


async def delete(token_hash: str) -> None:
    """Sletter én session (log ud)."""
    await fetch_all("DELETE FROM sessions WHERE token_hash = %s RETURNING token_hash", (token_hash,))


async def delete_for_user(user_id: UUID, *, except_token_hash: str | None = None) -> None:
    """Sletter alle en brugers sessioner, evt. bortset fra den aktuelle (bruges ved skift af adgangskode)."""
    await fetch_all(
        "DELETE FROM sessions WHERE user_id = %s AND token_hash IS DISTINCT FROM %s RETURNING token_hash",
        (user_id, except_token_hash),
    )


async def delete_expired() -> None:
    """Rydder udløbne sessioner op, så tabellen ikke vokser for evigt."""
    await fetch_all("DELETE FROM sessions WHERE expires_at <= now() RETURNING token_hash")
