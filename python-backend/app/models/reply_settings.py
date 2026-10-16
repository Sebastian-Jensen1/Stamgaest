"""Databasen: tabellen `reply_settings` (caféens svarstil, fakta om stedet og eksempler).

Hver funktion tager caféens id og filtrerer på det i selve SQL'en, så én café aldrig kan læse
eller ændre en andens indstillinger. Caféens id kommer altid fra login, aldrig fra browseren.

Fakta om stedet (`about`) og Claudes forslag (`about_suggestion`) gemmes som JSON. Funktionerne
her tager imod færdigrensede dicts fra services/settings_service.py og tjekker ikke indholdet selv.
"""

from uuid import UUID

from psycopg.types.json import Jsonb

from app.db.pool import fetch_all, fetch_one, fetch_one_required

# Så lang tid må en profil være "ved at blive bygget", før vi går ud fra, at serveren blev
# genstartet midt i det, og lader ejeren prøve igen.
PROFILE_BUILD_TIMEOUT_MINUTES = 10


async def get(restaurant_id: UUID) -> dict | None:
    """Henter caféens række, eller None hvis caféen ikke har gemt noget endnu."""
    return await fetch_one("SELECT * FROM reply_settings WHERE restaurant_id = %s", (restaurant_id,))


async def save(restaurant_id: UUID, values: dict, *, clear_suggestion: bool) -> dict:
    """Gemmer ejerens indstillinger. Returnerer rækken.

    At gemme fakta om stedet tæller som at godkende dem, så tidspunktet for godkendelsen
    opdateres, når de ændres. `clear_suggestion` fjerner Claudes forslag (ejeren har taget
    stilling til det).
    """
    params = {
        "restaurant_id": restaurant_id,
        "reply_length": values["reply_length"],
        "tone": values["tone"],
        "signature": values["signature"],
        "avoid": values["avoid"],
        "contact": values["contact"],
        "examples": values["examples"],
        "about": Jsonb(values["about"]),
        "clear_suggestion": clear_suggestion,
    }
    return await fetch_one_required(
        """
        INSERT INTO reply_settings
          (restaurant_id, reply_length, tone, signature, avoid, contact, examples, about, facts_approved_at)
        VALUES
          (%(restaurant_id)s, %(reply_length)s, %(tone)s, %(signature)s, %(avoid)s, %(contact)s, %(examples)s,
           %(about)s, now())
        ON CONFLICT (restaurant_id) DO UPDATE SET
          reply_length      = EXCLUDED.reply_length,
          tone              = EXCLUDED.tone,
          signature         = EXCLUDED.signature,
          avoid             = EXCLUDED.avoid,
          contact           = EXCLUDED.contact,
          examples          = EXCLUDED.examples,
          about             = EXCLUDED.about,
          facts_approved_at = CASE WHEN EXCLUDED.about IS DISTINCT FROM reply_settings.about
                                   THEN now() ELSE reply_settings.facts_approved_at END,
          about_suggestion  = CASE WHEN %(clear_suggestion)s THEN NULL ELSE reply_settings.about_suggestion END
        RETURNING *
        """,
        params,
    )


async def claim_profile_build(restaurant_id: UUID) -> bool:
    """Reserverer retten til at bygge en profil nu. Returnerer False, hvis der allerede bygges en.

    Tjek og opdatering sker i ÉN sætning, så to hurtige klik ikke begge starter (og betaler for)
    en profil.
    """
    await fetch_all(
        "INSERT INTO reply_settings (restaurant_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING restaurant_id",
        (restaurant_id,),
    )
    row = await fetch_one(
        """
        UPDATE reply_settings
           SET profile_status = 'building', profile_message = '', profile_started_at = now()
         WHERE restaurant_id = %s
           AND (profile_status <> 'building'
                OR profile_started_at < now() - make_interval(mins => %s))
        RETURNING restaurant_id
        """,
        (restaurant_id, PROFILE_BUILD_TIMEOUT_MINUTES),
    )
    return row is not None


async def finish_profile_build(restaurant_id: UUID, *, suggestion: dict | None, message: str) -> None:
    """Gemmer resultatet af at bygge en profil: et forslag (klar) eller ingenting (fejlet).

    Fejler det, bliver et eventuelt tidligere forslag stående.
    """
    status = "ready" if suggestion is not None else "failed"
    await fetch_all(
        """
        UPDATE reply_settings
           SET profile_status = %s, profile_message = %s,
               about_suggestion = COALESCE(%s, about_suggestion)
         WHERE restaurant_id = %s
        RETURNING restaurant_id
        """,
        (status, message[:300], Jsonb(suggestion) if suggestion is not None else None, restaurant_id),
    )


async def discard_suggestion(restaurant_id: UUID) -> None:
    """Kasserer Claudes forslag til fakta om stedet."""
    await fetch_all(
        "UPDATE reply_settings SET about_suggestion = NULL WHERE restaurant_id = %s RETURNING restaurant_id",
        (restaurant_id,),
    )


async def recent_sent_replies(restaurant_id: UUID, limit: int) -> list[str]:
    """De seneste svar caféen faktisk har sendt (efter at have rettet dem). Bruges som eksempler.

    Kun svar på caféens egne anmeldelser: filteret på restaurant_id står i selve SQL'en.
    """
    rows = await fetch_all(
        """
        SELECT r.final_text
          FROM replies r
          JOIN reviews v ON v.id = r.review_id
         WHERE v.restaurant_id = %s
           AND r.status = 'sent'
           AND r.final_text IS NOT NULL
           AND btrim(r.final_text) <> ''
         ORDER BY r.sent_at DESC
         LIMIT %s
        """,
        (restaurant_id, limit),
    )
    return [row["final_text"] for row in rows]
