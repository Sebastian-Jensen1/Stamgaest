"""Databasen: tabellen `users` (kunder der kan logge ind)."""

from uuid import UUID

from app.db.pool import fetch_all, fetch_one, fetch_one_required


async def create(*, email: str, password_hash: str, restaurant_id: UUID) -> dict:
    """Opretter en bruger. Fejler med UniqueViolation hvis e-mailen allerede findes."""
    return await fetch_one_required(
        """
        INSERT INTO users (email, password_hash, restaurant_id)
        VALUES (%s, %s, %s)
        RETURNING id, email, restaurant_id, is_active, created_at
        """,
        (email, password_hash, restaurant_id),
    )


async def find_by_email(email: str) -> dict | None:
    """Finder en bruger ud fra e-mail (store/små bogstaver er ligegyldige)."""
    return await fetch_one("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,))


async def update_password(user_id: UUID, password_hash: str) -> None:
    """Gemmer en ny adgangskode-hash."""
    await fetch_all("UPDATE users SET password_hash = %s WHERE id = %s RETURNING id", (password_hash, user_id))


async def set_active(user_id: UUID, active: bool) -> None:
    """Slår en bruger til eller fra. En slået-fra bruger kan ikke logge ind."""
    await fetch_all("UPDATE users SET is_active = %s WHERE id = %s RETURNING id", (active, user_id))


async def touch_login(user_id: UUID) -> None:
    """Noterer at brugeren lige har logget ind."""
    await fetch_all("UPDATE users SET last_login_at = now() WHERE id = %s RETURNING id", (user_id,))


async def list_with_restaurants() -> list[dict]:
    """Alle brugere med navnet på deres café. Bruges af admin-værktøjet."""
    return await fetch_all(
        """
        SELECT u.email, u.is_active, u.last_login_at, u.created_at,
               r.id AS restaurant_id, r.name AS restaurant_name, r.google_place_id
          FROM users u
          JOIN restaurants r ON r.id = u.restaurant_id
         ORDER BY r.name, u.email
        """
    )
