"""Databasen: tabellen `restaurants` (caféer og andre kunder).

Al SQL for restauranter står her og kun her. Hver funktion kører én forespørgsel.
"""

from datetime import datetime
from uuid import UUID

from app.db.pool import fetch_all, fetch_one, fetch_one_required


async def upsert_by_place_id(
    *,
    google_place_id: str,
    name: str,
    business_type: str | None,
    address: str | None,
    google_rating: float | None,
    google_rating_count: int | None,
    website_url: str | None = None,
) -> dict:
    """Opretter stedet, eller opdaterer det hvis vi kender det i forvejen. Returnerer rækken.

    "Upsert" = update + insert. Vi henter samme sted igen og igen, og navn, adresse
    og Google-score kan have ændret sig, så vi må ikke lave et nyt sted hver gang.
    """
    return await fetch_one_required(
        """
        INSERT INTO restaurants
          (google_place_id, name, business_type, address, google_rating, google_rating_count, website_url)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (google_place_id) DO UPDATE SET
          name                = EXCLUDED.name,
          address             = EXCLUDED.address,
          google_rating       = EXCLUDED.google_rating,
          google_rating_count = EXCLUDED.google_rating_count,
          -- COALESCE = "brug den nye værdi, men behold den gamle hvis den nye er tom".
          business_type       = COALESCE(EXCLUDED.business_type, restaurants.business_type),
          website_url         = COALESCE(EXCLUDED.website_url, restaurants.website_url)
        RETURNING *
        """,
        (
            google_place_id,
            name,
            business_type or None,
            address or None,
            google_rating,
            google_rating_count,
            website_url,
        ),
    )


async def list_all() -> list[dict]:
    """Henter alle steder, med antal brugere. Bruges af admin-værktøjet."""
    return await fetch_all(
        """
        SELECT r.id, r.name, r.google_place_id, r.business_type, r.ai_enabled,
               (SELECT count(*) FROM users u WHERE u.restaurant_id = r.id) AS user_count
          FROM restaurants r
         ORDER BY r.name
        """
    )


async def set_ai_enabled(restaurant_id: UUID, enabled: bool) -> bool:
    """Slår udkast fra Claude til eller fra for én café. Returnerer False hvis caféen ikke findes."""
    row = await fetch_one(
        "UPDATE restaurants SET ai_enabled = %s WHERE id = %s RETURNING id", (enabled, restaurant_id)
    )
    return row is not None


async def find_by_id(restaurant_id: UUID) -> dict | None:
    """Henter ét sted ud fra dets id, eller None hvis det ikke findes."""
    return await fetch_one("SELECT * FROM restaurants WHERE id = %s", (restaurant_id,))


async def update_business_type(restaurant_id: UUID, business_type: str) -> None:
    """Sætter stedets branche (fx "café"), som bruges i prompten til Claude."""
    await fetch_all(
        "UPDATE restaurants SET business_type = %s WHERE id = %s RETURNING id",
        (business_type or None, restaurant_id),
    )


async def claim_refresh(restaurant_id: UUID, cooldown_seconds: int) -> bool:
    """Reserverer retten til at hente fra Google nu. Returnerer False hvis det er for tidligt.

    Tjekket og opdateringen sker i ÉN forespørgsel, så to hurtige klik på knappen
    ikke begge kan slippe igennem og koste to Google-kald.
    """
    row = await fetch_one(
        """
        UPDATE restaurants SET reviews_fetched_at = now()
         WHERE id = %s
           AND (reviews_fetched_at IS NULL
                OR reviews_fetched_at <= now() - make_interval(secs => %s))
        RETURNING id
        """,
        (restaurant_id, cooldown_seconds),
    )
    return row is not None


async def set_reviews_fetched_at(restaurant_id: UUID, value: datetime | None) -> None:
    """Sætter tidspunktet for sidste hentning. Bruges til at fortryde en reservation hvis Google fejlede."""
    await fetch_all(
        "UPDATE restaurants SET reviews_fetched_at = %s WHERE id = %s RETURNING id",
        (value, restaurant_id),
    )


async def claim_topics(restaurant_id: UUID, stale_minutes: int) -> bool:
    """Reserverer retten til at mærke caféens anmeldelser nu. False hvis en mærkning allerede kører.

    Tjek og reservation sker i ÉN forespørgsel, så to samtidige hentninger ikke begge starter
    en mærkning og betaler to gange. Er en mærkning gået i stå (fx ved en genstart midt i), må
    en ny starte efter `stale_minutes`.
    """
    row = await fetch_one(
        """
        UPDATE restaurants SET topics_started_at = now()
         WHERE id = %s
           AND (topics_started_at IS NULL
                OR topics_started_at <= now() - make_interval(mins => %s))
        RETURNING id
        """,
        (restaurant_id, stale_minutes),
    )
    return row is not None


async def release_topics(restaurant_id: UUID) -> None:
    """Noterer at mærkningen for caféen er færdig, så en ny kan startes."""
    await fetch_all(
        "UPDATE restaurants SET topics_started_at = NULL WHERE id = %s RETURNING id", (restaurant_id,)
    )
