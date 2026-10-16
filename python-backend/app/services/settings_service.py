"""Caféens indstillinger for svar: læs dem, rens fakta om stedet, og byg Claudes profil ud fra dem.

Alt, der ender i prompten, går gennem clean_about(), uanset om det kommer fra ejeren, fra
Claudes forslag eller fra databasen. Så er lofterne på længde og antal altid de samme.
"""

from uuid import UUID

from app.models import reply_settings
from app.services.claude_service import MAX_RECENT_REPLIES, ReplyStyle

# Lofter for fakta om stedet. Holder prompten kort og skarp, og prisen nede.
CONCEPT_CHARS = 300
ATMOSPHERE_CHARS = 300
ITEM_CHARS = 80
MAX_ITEMS = 20
TRAIT_CHARS = 150
MAX_TRAITS = 12

REPLY_LENGTHS = ("auto", "meget_kort", "kort", "mellem", "lang")

EMPTY_ABOUT = {"concept": "", "items": [], "traits": [], "atmosphere": ""}

DEFAULTS = {
    "reply_length": "auto",
    "tone": "",
    "signature": "",
    "avoid": "",
    "contact": "",
    "examples": "",
    "about": EMPTY_ABOUT,
    "about_suggestion": None,
    "facts_approved_at": None,
    "profile_status": "none",
    "profile_message": "",
}


def _line(value: object, limit: int) -> str:
    """Gør en værdi til ren tekst på én linje (mellemrum samlet), højst `limit` tegn."""
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _unique(values: object, *, limit: int, count: int) -> list[str]:
    """Renser en liste: tekst på én linje, ingen tomme, ingen dubletter (store/små ens), højst `count`."""
    result, seen = [], set()
    for value in values if isinstance(values, list) else []:
        text = _line(value, limit)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
        if len(result) == count:
            break
    return result


def clean_about(data: object) -> dict:
    """Renser fakta om stedet til den faste form. Ukendte felter smides væk."""
    data = data if isinstance(data, dict) else {}
    return {
        "concept": _line(data.get("concept"), CONCEPT_CHARS),
        "items": _unique(data.get("items"), limit=ITEM_CHARS, count=MAX_ITEMS),
        "traits": _unique(data.get("traits"), limit=TRAIT_CHARS, count=MAX_TRAITS),
        "atmosphere": _line(data.get("atmosphere"), ATMOSPHERE_CHARS),
    }


def is_empty(about: dict) -> bool:
    """Er der ingen fakta om stedet?"""
    return not (about["concept"] or about["items"] or about["traits"] or about["atmosphere"])


def about_from_legacy_text(text: str) -> dict:
    """Flytter fakta, der blev gemt som én tekst (før 007), over i den nye form.

    Teksten havde typisk linjerne "Koncept: ...", "Kendte retter og drikke: a, b",
    "Kendetegn:" med "- punkter" og "Stemning og beliggenhed: ...". Alt, der ikke passer i
    et felt, bliver et kendetegn, så intet går tabt.
    """
    about = {"concept": "", "items": [], "traits": [], "atmosphere": ""}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.rstrip(":").casefold() == "kendetegn":
            continue
        head, _, rest = line.partition(":")
        key = head.strip().casefold()
        if rest and key == "koncept":
            about["concept"] = rest.strip()
        elif rest and key in ("kendte retter og drikke", "retter og drikke"):
            about["items"].extend(part.strip() for part in rest.split(","))
        elif rest and key in ("stemning og beliggenhed", "stemning"):
            about["atmosphere"] = rest.strip()
        else:
            about["traits"].append(line.lstrip("-•* ").strip())
    return clean_about(about)


async def load(restaurant_id: UUID) -> dict:
    """Henter caféens indstillinger med standardværdier, fakta i den nye form og evt. Claudes forslag."""
    row = await reply_settings.get(restaurant_id)
    if row is None:
        return {"restaurant_id": restaurant_id, **DEFAULTS}

    if row.get("about") is not None:
        about = clean_about(row["about"])
    elif (row.get("facts") or "").strip():
        about = about_from_legacy_text(row["facts"])
    else:
        about = dict(EMPTY_ABOUT)
    suggestion = clean_about(row["about_suggestion"]) if row.get("about_suggestion") is not None else None

    return {
        "restaurant_id": restaurant_id,
        "reply_length": row["reply_length"] if row["reply_length"] in REPLY_LENGTHS else "auto",
        "tone": row.get("tone") or "",
        "signature": row["signature"],
        "avoid": row["avoid"],
        "contact": row["contact"],
        "examples": row["examples"],
        "about": about,
        "about_suggestion": suggestion,
        "facts_approved_at": row["facts_approved_at"],
        "profile_status": row["profile_status"],
        "profile_message": row["profile_message"],
    }


def to_style(settings: dict, recent_replies: list[str] | tuple[str, ...] = ()) -> ReplyStyle:
    """Laver indstillingerne om til den profil, Claude får. Claudes ikke-godkendte forslag kommer aldrig med."""
    return ReplyStyle(
        reply_length=settings["reply_length"],
        tone=settings["tone"],
        signature=settings["signature"],
        avoid=settings["avoid"],
        contact=settings["contact"],
        examples=settings["examples"],
        about=clean_about(settings["about"]),
        recent_replies=tuple(recent_replies),
    )


async def style_for(restaurant_id: UUID) -> ReplyStyle:
    """Caféens gemte profil, inkl. de seneste svar de har sendt. Det, udkastene bruges med."""
    settings = await load(restaurant_id)
    recent = await reply_settings.recent_sent_replies(restaurant_id, MAX_RECENT_REPLIES)
    return to_style(settings, recent)
