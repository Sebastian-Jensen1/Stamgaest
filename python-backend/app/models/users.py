"""Databasen: tabellen `users` (kunder der kan logge ind)."""

from datetime import datetime
from uuid import UUID

from app.db.pool import fetch_all, fetch_one, fetch_one_required


async def create(*, email: str, password_hash: str, restaurant_id: UUID, invite_pending: bool = False) -> dict:
    """Opretter en bruger. Fejler med UniqueViolation hvis e-mailen allerede findes."""
    return await fetch_one_required(
        """
        INSERT INTO users (email, password_hash, restaurant_id, invite_pending)
        VALUES (%s, %s, %s, %s)
        RETURNING id, email, restaurant_id, is_active, invite_pending, created_at
        """,
        (email, password_hash, restaurant_id, invite_pending),
    )


async def find_by_email(email: str) -> dict | None:
    """Finder en bruger ud fra e-mail (store/små bogstaver er ligegyldige)."""
    return await fetch_one("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,))


async def update_password(user_id: UUID, password_hash: str) -> None:
    """Gemmer en ny adgangskode-hash."""
    await fetch_all("UPDATE users SET password_hash = %s WHERE id = %s RETURNING id", (password_hash, user_id))


async def set_invite_pending(user_id: UUID, pending: bool) -> None:
    """Noterer om kunden er inviteret men endnu ikke har valgt en adgangskode."""
    await fetch_all("UPDATE users SET invite_pending = %s WHERE id = %s RETURNING id", (pending, user_id))


async def set_active(user_id: UUID, active: bool) -> None:
    """Slår en bruger til eller fra. En slået-fra bruger kan ikke logge ind."""
    await fetch_all("UPDATE users SET is_active = %s WHERE id = %s RETURNING id", (active, user_id))


async def touch_login(user_id: UUID) -> None:
    """Noterer at brugeren lige har logget ind."""
    await fetch_all("UPDATE users SET last_login_at = now() WHERE id = %s RETURNING id", (user_id,))


async def start_or_continue_visit(user_id: UUID, pause_minutes: int) -> datetime | None:
    """Noterer at brugeren er på dashboardet, og giver hvornår det forrige besøg sluttede.

    Har brugeren ikke været aktiv i `pause_minutes`, begynder et nyt besøg: det gamle "sidst
    set" bliver til "forrige besøg". Ellers er det samme besøg, og "forrige besøg" står stille,
    så "Nye siden sidst" ikke springer til 0, bare fordi siden genindlæses.

    Sker i én UPDATE, så to faner der åbnes samtidig ikke kan rykke markøren forkert. I SET
    ser Postgres de GAMLE værdier, så CASE sammenligner med det tidligere "sidst set".
    None betyder at brugeren aldrig har været inde før (alt er nyt).
    """
    row = await fetch_one_required(
        """
        UPDATE users
           SET previous_reviews_seen_at = CASE
                 WHEN reviews_seen_at IS NULL OR reviews_seen_at < now() - make_interval(mins => %s)
                 THEN reviews_seen_at
                 ELSE previous_reviews_seen_at
               END,
               reviews_seen_at = now()
         WHERE id = %s
        RETURNING previous_reviews_seen_at
        """,
        (pause_minutes, user_id),
    )
    return row["previous_reviews_seen_at"]


async def list_with_restaurants() -> list[dict]:
    """Alle brugere med navnet på deres café. Bruges af admin-værktøjet."""
    return await fetch_all(
        """
        SELECT u.email, u.is_active, u.invite_pending, u.last_login_at, u.created_at,
               r.id AS restaurant_id, r.name AS restaurant_name, r.google_place_id,
               EXISTS (
                 SELECT 1 FROM password_tokens t
                  WHERE t.user_id = u.id AND t.used_at IS NULL AND t.expires_at > now()
               ) AS has_valid_link
          FROM users u
          JOIN restaurants r ON r.id = u.restaurant_id
         ORDER BY r.name, u.email
        """
    )
