"""Sender mails via Resend (https://resend.com/docs/api-reference/emails/send-email).

Resend modtager mailen over HTTPS og leverer den. Vi bruger den delte HTTP-klient, så der
kommer ingen ny afhængighed. En mail der fejler må aldrig vælte det der bad om den, og
fejlen fra Resend må aldrig nå browseren, så alt her logges og sluges.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.core.config import Settings

logger = logging.getLogger("review_assistant")

RESEND_URL = "https://api.resend.com/emails"
# Hvor kunden kan skrive til os, når noget ikke kan klares på siden (fx hvis en anden har overtaget kontoen).
SUPPORT_EMAIL = "kontakt@stamgaest.com"


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


async def send_login_link(http: httpx.AsyncClient, settings: Settings, *, to: str, token: str, minutes: int) -> bool:
    """Sender mailen med et link, der logger kunden direkte ind.

    Nøglen står efter # i linket, ligesom ved "glemt adgangskode", så den aldrig sendes til en
    server eller står i en log. Siden bruger først nøglen, når kunden trykker "Log ind" (se
    routers/auth.py, use_login_link).
    """
    link = f"{settings.app_base_url}/#login={token}"
    text = (
        "Hej\n\n"
        "Her er dit link til at logge ind på Stamgæst.\n\n"
        f"Det virker i {minutes} minutter og kun én gang:\n"
        f"{link}\n\n"
        "Har du ikke bedt om det, kan du bare slette denne mail. Ingen kan logge ind uden linket.\n\n"
        "Venlig hilsen\nStamgæst"
    )
    return await send_email(http, settings, to=to, subject="Log ind på Stamgæst", text=text)


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


async def send_password_changed(http: httpx.AsyncClient, settings: Settings, *, to: str, when: datetime) -> bool:
    """Fortæller kunden at adgangskoden lige er ændret.

    Er det ikke kunden selv, er det tegn på at en anden har fået adgang. Mailen går til kontoens
    adresse, som den anden ikke kan ændre, så den rigtige ejer opdager det og kan tage kontoen
    tilbage med "Glemt adgangskode". Mailen indeholder bevidst intet link der giver adgang.
    """
    local = when.astimezone(ZoneInfo("Europe/Copenhagen"))
    text = (
        "Hej\n\n"
        f"Adgangskoden til din Stamgæst-konto blev ændret den {local:%d.%m.%Y} kl. {local:%H:%M}.\n\n"
        "Var det dig, behøver du ikke gøre noget.\n\n"
        "Var det ikke dig, så gå til siden herunder med det samme og tryk \"Glemt adgangskode\". "
        "Når du vælger en ny adgangskode, bliver alle andre logget ud:\n"
        f"{settings.app_base_url}\n\n"
        "Venlig hilsen\nStamgæst"
    )
    return await send_email(http, settings, to=to, subject="Din adgangskode til Stamgæst er ændret", text=text)


async def send_email_change_confirmation(
    http: httpx.AsyncClient, settings: Settings, *, to: str, token: str, hours: int
) -> bool:
    """Sender linket der bekræfter en ny e-mail. Mailen går til den NYE adresse.

    Først når linket er åbnet, skiftes e-mailen. Så ved vi, at kunden faktisk kan få mail dér.
    Samme slags link som ved "glemt adgangskode": nøglen står efter # (se send_password_reset).
    """
    link = f"{settings.app_base_url}/#skiftmail={token}"
    text = (
        "Hej\n\n"
        "Du har bedt om at bruge denne e-mailadresse til din Stamgæst-konto.\n\n"
        f"Brug dette link for at bekræfte den (det virker i {hours} timer og kun én gang):\n"
        f"{link}\n\n"
        "Indtil du har bekræftet, logger du ind med din gamle e-mail.\n\n"
        "Har du ikke bedt om det, kan du bare slette denne mail. Intet er ændret.\n\n"
        "Venlig hilsen\nStamgæst"
    )
    return await send_email(http, settings, to=to, subject="Bekræft din nye e-mail til Stamgæst", text=text)


async def send_email_in_use(http: httpx.AsyncClient, settings: Settings, *, to: str) -> bool:
    """Fortæller den nye adresse, at den allerede hører til en anden konto.

    Svaret i browseren er det samme som når adressen er ledig. Ellers kunne man bruge "skift e-mail"
    til at gætte hvem der er kunde. Kun den der ejer adressen, får det at vide her.
    """
    text = (
        "Hej\n\n"
        "Nogen har bedt om at bruge denne e-mailadresse til en Stamgæst-konto. Adressen hører allerede "
        "til en anden Stamgæst-konto, så intet er ændret.\n\n"
        f"Var det dig, og har du brug for hjælp, så skriv til {SUPPORT_EMAIL}. "
        "Ellers kan du bare slette denne mail.\n\n"
        "Venlig hilsen\nStamgæst"
    )
    return await send_email(http, settings, to=to, subject="Din e-mail bruges allerede hos Stamgæst", text=text)


async def send_email_changed(
    http: httpx.AsyncClient, settings: Settings, *, to: str, new_email: str, when: datetime
) -> bool:
    """Fortæller den GAMLE adresse, at kontoens e-mail er skiftet.

    Var det ikke kunden selv, kan de ikke bruge "Glemt adgangskode" længere, for den gamle adresse
    virker ikke til login. Derfor beder mailen dem skrive til os, så vi kan lukke adgangen.
    """
    local = when.astimezone(ZoneInfo("Europe/Copenhagen"))
    text = (
        "Hej\n\n"
        f"E-mailen til din Stamgæst-konto blev skiftet den {local:%d.%m.%Y} kl. {local:%H:%M} til:\n"
        f"{new_email}\n\n"
        "Fremover logger du ind med den nye adresse. Var det dig, behøver du ikke gøre noget.\n\n"
        f"Var det ikke dig, så skriv med det samme til {SUPPORT_EMAIL}, så lukker vi adgangen.\n\n"
        "Venlig hilsen\nStamgæst"
    )
    return await send_email(http, settings, to=to, subject="E-mailen til din Stamgæst-konto er skiftet", text=text)
