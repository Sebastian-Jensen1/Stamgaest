"""Lofter for betalte kald: tjekker dem, afviser med en venlig besked, og advarer os på mail.

Routerne kalder `reserve()` FØR de kalder Claude eller Google, og `usage.finish()`/`usage.fail()`
bagefter. Så kan regningen aldrig løbe over lofterne, heller ikke hvis mange klikker samtidig.
"""

import logging
import math

import httpx

from app.core.config import Caps, Settings
from app.core.errors import ApiError
from app.models import usage
from app.services import email_service

logger = logging.getLogger("review_assistant")

_KIND_LABEL = {
    usage.DRAFT: "udkast fra Claude",
    usage.FETCH: "hentninger fra Google",
    usage.PROFILE: "profiler bygget fra hjemmesider",
}
_SCOPE_LABEL = {
    "cafe_day": "pr. café pr. dag",
    "cafe_month": "pr. café pr. måned",
    "all_day": "for hele Stamgæst pr. dag",
    "all_month": "for hele Stamgæst pr. måned",
}


def caps_for(kind: str, settings: Settings) -> Caps:
    """Giver lofterne for en slags kald."""
    return {usage.DRAFT: settings.draft_caps, usage.FETCH: settings.fetch_caps, usage.PROFILE: settings.profile_caps}[kind]


def refusal_message(kind: str, scope: str, *, used: int, cap: int) -> str:
    """Teksten kunden ser, når et loft er nået. Lofterne for hele Stamgæst nævnes ikke for kunden."""
    left = max(0, cap - used)
    if kind == usage.DRAFT:
        if scope == "cafe_day":
            return f"Din café har {left} udkast tilbage i dag. Vælg færre anmeldelser, eller prøv igen i morgen."
        if scope == "cafe_month":
            return f"Din café har {left} udkast tilbage denne måned. Kontakt Stamgæst, hvis I har brug for flere."
        return "Udkast er midlertidigt ikke tilgængelige. Prøv igen senere."
    if kind == usage.PROFILE:
        if scope.startswith("cafe"):
            period = "i dag" if scope == "cafe_day" else "denne måned"
            return f"Profilen er bygget mange gange {period}. Ret i teksten selv, eller prøv igen senere."
        return "Det er midlertidigt ikke muligt at bygge en profil. Prøv igen senere."
    if scope == "cafe_day":
        return "Anmeldelserne er hentet mange gange i dag. Prøv igen i morgen."
    if scope == "cafe_month":
        return "Anmeldelserne er hentet mange gange denne måned. Kontakt Stamgæst."
    return "Hentning fra Google er midlertidigt ikke tilgængelig. Prøv igen senere."


async def reserve(
    *,
    kind: str,
    restaurant: dict,
    user_id,
    count: int,
    settings: Settings,
    http: httpx.AsyncClient,
) -> list[int]:
    """Reserverer plads til `count` betalte kald. Afviser med 429, hvis et loft ville blive overskredet.

    Advarer os på mail, når et loft når 80 % og 100 %.
    """
    caps = caps_for(kind, settings)
    result = await usage.reserve(
        kind=kind, restaurant_id=restaurant["id"], user_id=user_id, count=count, caps=caps
    )

    if result.refused:
        scope = result.refused
        cap = getattr(caps, scope)
        logger.warning("Loft nået: %s %s (%s af %s)", kind, scope, result.used_before[scope], cap)
        await _alert(http, settings, kind=kind, scope=scope, level=100, restaurant=restaurant,
                     used=result.used_before[scope], cap=cap)
        raise ApiError(429, refusal_message(kind, scope, used=result.used_before[scope], cap=cap))

    for scope in usage.SCOPES:
        cap = getattr(caps, scope)
        before = result.used_before[scope]
        after = before + count
        for level in (80, 100):
            threshold = math.ceil(cap * level / 100)
            if before < threshold <= after:
                await _alert(http, settings, kind=kind, scope=scope, level=level, restaurant=restaurant,
                             used=after, cap=cap)
    return result.ids


async def _alert(
    http: httpx.AsyncClient,
    settings: Settings,
    *,
    kind: str,
    scope: str,
    level: int,
    restaurant: dict,
    used: int,
    cap: int,
) -> None:
    """Sender os en advarsel om et loft, højst én gang pr. loft, niveau og dag. Fejler aldrig."""
    per_cafe = scope.startswith("cafe")
    key = f"{scope}:{restaurant['id']}" if per_cafe else scope
    try:
        if not await usage.claim_alert(kind=kind, scope=key, level=level):
            return
        who = f"{restaurant['name']} (id {restaurant['id']})" if per_cafe else "hele Stamgæst"
        what = "NÅET" if level >= 100 else f"{level} %"
        subject = f"Stamgæst: loft {what} for {_KIND_LABEL[kind]} ({who})"
        text = (
            f"Loftet for {_KIND_LABEL[kind]} {_SCOPE_LABEL[scope]} er {what}.\n\n"
            f"Hvem: {who}\n"
            f"Brugt: {used} af {cap}\n\n"
            "Er det forventet, kan loftet hæves i .env (genstart serveren bagefter).\n"
            "Er det mistænkeligt, kan udkast slås fra for én café med 'ai-off', eller for alle med\n"
            "AI_PAUSED=1 i .env. Se forbruget med 'usage'.\n"
        )
        logger.warning("%s", subject)
        if settings.admin_email:
            await email_service.send_email(http, settings, to=settings.admin_email, subject=subject, text=text)
    except Exception:  # noqa: BLE001 - en advarsel må aldrig få selve kaldet til at fejle
        logger.exception("Kunne ikke sende advarsel om loft")
