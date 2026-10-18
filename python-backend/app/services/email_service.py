"""Sender mails via Resend (https://resend.com/docs/api-reference/emails/send-email).

Resend modtager mailen over HTTPS og leverer den. Vi bruger den delte HTTP-klient, så der
kommer ingen ny afhængighed. En mail der fejler må aldrig vælte det der bad om den, og
fejlen fra Resend må aldrig nå browseren, så alt her logges og sluges.
"""

import logging

import httpx

from app.core.config import Settings

logger = logging.getLogger("review_assistant")

RESEND_URL = "https://api.resend.com/emails"


async def send_email(http: httpx.AsyncClient, settings: Settings, *, to: str, subject: str, text: str) -> bool:
    """Sender en mail som ren tekst. Returnerer True hvis Resend tog imod den, ellers False.

    Mangler RESEND_API_KEY, sendes der ikke noget (så serveren stadig kan køre lokalt uden). Selve
    teksten skrives aldrig i loggen, for den kan indeholde et link der giver adgang til en konto.
    """
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY mangler i .env, så mailen %r blev ikke sendt.", subject)
        return False

    try:
        response = await http.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.mail_from, "to": [to], "subject": subject, "text": text},
        )
    except httpx.HTTPError as exc:
        logger.error("Kunne ikke nå Resend: %s", type(exc).__name__)
        return False

    if response.status_code >= 300:
        # Svaret fra Resend nævner typisk modtagerens adresse, så kun statuskoden logges.
        logger.error("Resend afviste mailen %r (status %s).", subject, response.status_code)
        return False
    return True


async def send_password_reset(http: httpx.AsyncClient, settings: Settings, *, to: str, token: str, minutes: int) -> bool:
    """Sender mailen med linket til at vælge en ny adgangskode.

    Nøglen står efter # i linket. Alt efter # sendes aldrig til en server og kommer ikke i
    adgangslogs eller i "Referer"-headeren, så nøglen bliver kun i brugerens browser.
    """
    link = f"{settings.app_base_url}/#nulstil={token}"
    text = (
        "Hej\n\n"
        "Vi har fået en anmodning om at vælge en ny adgangskode til din Stamgæst-konto.\n\n"
        f"Brug dette link for at vælge en ny (det virker i {minutes} minutter og kun én gang):\n"
        f"{link}\n\n"
        "Har du ikke bedt om det, kan du bare slette denne mail. Din adgangskode er ikke ændret.\n\n"
        "Venlig hilsen\nStamgæst"
    )
    return await send_email(http, settings, to=to, subject="Vælg ny adgangskode til Stamgæst", text=text)


async def send_invitation(
    http: httpx.AsyncClient, settings: Settings, *, to: str, token: str, business_name: str, days: int
) -> bool:
    """Sender velkomstmailen til en ny kunde, med linket til at vælge sin første adgangskode.

    Samme link som ved "glemt adgangskode": nøglen står efter # (se send_password_reset).
    """
    link = f"{settings.app_base_url}/#nulstil={token}"
    text = (
        "Velkommen til Stamgæst\n\n"
        f"Der er oprettet en konto til dig, så du kan se og besvare anmeldelserne for {business_name}.\n\n"
        f"Brug dette link for at vælge din adgangskode (det virker i {days} dage og kun én gang):\n"
        f"{link}\n\n"
        "Bagefter logger du ind med denne e-mailadresse og den adgangskode, du har valgt.\n\n"
        "Har du ikke forventet denne mail, kan du bare slette den.\n\n"
        "Venlig hilsen\nStamgæst"
    )
    return await send_email(http, settings, to=to, subject="Velkommen til Stamgæst: vælg din adgangskode", text=text)
