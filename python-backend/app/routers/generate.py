"""Endpoints der får Claude til at skrive udkast.

- POST /api/generate          skriver og GEMMER et udkast til hver af de valgte anmeldelser
- POST /api/generate/preview  "Prøv et udkast" på indstillingssiden: skriver ét udkast med de
                              indstillinger, der står på siden lige nu (også ugemte), og gemmer intet

Begge går gennem de samme kontroller (_allow_drafts), så nødbremser, grænsen pr. time og lofterne
altid gælder ens.
"""

import anthropic
import httpx
from fastapi import APIRouter, Depends

from app.core.config import Settings
from app.core.deps import CurrentUser, get_claude, get_http, get_limits, get_settings
from app.core.errors import ApiError
from app.core.rate_limit import Limits, describe_wait
from app.models import replies, reply_settings, restaurants, reviews, usage
from app.schemas.generate import GenerateRequest, GenerateResponse, PreviewRequest, PreviewResponse
from app.services import claude_service, settings_service, usage_service

router = APIRouter()


async def _allow_drafts(
    *, user: dict, count: int, settings: Settings, limits: Limits, http: httpx.AsyncClient
) -> tuple[dict, list[int]]:
    """Tjekker at der må laves `count` udkast nu, og reserverer pladsen under lofterne.

    Returnerer (caféen, de reserverede id'er i forbrugsloggen). Kaster ApiError, hvis noget
    stopper det. Alt, der kan fejle, sker FØR reservationen, så en reservation aldrig hænger.
    """
    restaurant = await restaurants.find_by_id(user["restaurant_id"])
    if restaurant is None:
        raise ApiError(404, "Din café findes ikke.")

    # Nødbremserne: for alle (AI_PAUSED=1 i .env) og for én café ('ai-off' i admin-værktøjet).
    if settings.ai_paused:
        raise ApiError(503, "Udkast er midlertidigt slået fra. Prøv igen senere.")
    if not restaurant["ai_enabled"]:
        raise ApiError(403, "Udkast er slået fra for din café. Kontakt Stamgæst.")

    # Hvert udkast koster penge, så der er et loft pr. bruger pr. time.
    wait = limits.generate.check(str(user["user_id"]), count)
    if wait:
        raise ApiError(429, f"Du har lavet mange udkast på kort tid. Prøv igen om {describe_wait(wait)}.")
    limits.generate.record(str(user["user_id"]), count)

    # Lofterne pr. café og for hele Stamgæst, pr. dag og pr. måned. Pladsen reserveres i
    # databasen FØR Claude kaldes, så samtidige klik ikke kan komme over loftet.
    event_ids = await usage_service.reserve(
        kind=usage.DRAFT, restaurant=restaurant, user_id=user["user_id"], count=count, settings=settings, http=http,
    )
    return restaurant, event_ids


async def _draft(
    claude: anthropic.AsyncAnthropic,
    *,
    settings: Settings,
    restaurant: dict,
    review: dict,
    style: claude_service.ReplyStyle,
    event_id: int,
) -> claude_service.Draft:
    """Skriver ét udkast og noterer det i forbrugsloggen (også hvis det fejler, for det kan have kostet)."""
    try:
        draft = await claude_service.draft_review_reply(
            claude,
            model=settings.claude_model,
            business_name=restaurant["name"],
            business_type=restaurant["business_type"] or "",
            reviewer_name=review["reviewer_name"] or "",
            rating=review["rating"],
            review_text=review["review_text"] or "",
            style=style,
        )
    except Exception:
        await usage.fail([event_id])
        raise
    await usage.finish(event_id, model=draft.model, input_tokens=draft.input_tokens, output_tokens=draft.output_tokens)
    return draft


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    user: CurrentUser,
    claude: anthropic.AsyncAnthropic = Depends(get_claude),
    settings: Settings = Depends(get_settings),
    limits: Limits = Depends(get_limits),
    http: httpx.AsyncClient = Depends(get_http),
):
    """POST /api/generate  Skriver et udkast til hver af de valgte anmeldelser og gemmer dem."""
    restaurant_id = user["restaurant_id"]

    # Samme anmeldelse to gange ville give to betalte udkast, så dubletter fjernes
    # (rækkefølgen bevares).
    review_ids = list(dict.fromkeys(item.id for item in body.reviews))

    # Slå ALLE anmeldelser op FØR vi kalder Claude, og kun dem der hører til brugerens café.
    # Et id fra en anden café giver derfor "findes ikke", og vi når ikke at betale for noget.
    found = await reviews.find_many_owned(review_ids, restaurant_id)
    if len(found) != len(review_ids):
        raise ApiError(404, "En eller flere anmeldelser findes ikke.")

    # Caféens gemte profil (svarstil, godkendte fakta, eksempler og de seneste sendte svar).
    # Hentes én gang for alle udkastene. Claudes ikke-godkendte forslag bruges aldrig.
    style = await settings_service.style_for(restaurant_id)

    restaurant, event_ids = await _allow_drafts(
        user=user, count=len(review_ids), settings=settings, limits=limits, http=http
    )

    results = []
    for index, review_id in enumerate(review_ids):
        # Anmeldelsens indhold og caféens navn kommer fra databasen, ikke fra browseren.
        review = found[review_id]
        try:
            draft = await _draft(
                claude, settings=settings, restaurant=restaurant, review=review, style=style,
                event_id=event_ids[index],
            )
        except Exception:
            # De resterende blev aldrig sendt til Claude, så de frigives.
            await usage.skip(event_ids[index + 1 :])
            raise

        # Udkastet gemmes med det samme, ét ad gangen. Fejler nummer 4 af 5, er de
        # første tre stadig gemt (og betalt for), så de går ikke tabt.
        await replies.insert_draft(
            review_id=review_id, draft_text=draft.text, tone=style.tone or None, model=draft.model
        )
        results.append(
            {
                "id": review_id,
                "reviewer_name": review["reviewer_name"] or "",
                "rating": review["rating"],
                "review_text": review["review_text"] or "",
                "draft": draft.text,
                "model": draft.model,
            }
        )

    return {"results": results}


@router.post("/generate/preview", response_model=PreviewResponse)
async def preview(
    body: PreviewRequest,
    user: CurrentUser,
    claude: anthropic.AsyncAnthropic = Depends(get_claude),
    settings: Settings = Depends(get_settings),
    limits: Limits = Depends(get_limits),
    http: httpx.AsyncClient = Depends(get_http),
):
    """POST /api/generate/preview  Ét prøve-udkast med indstillingerne fra siden. Gemmes ikke.

    Indstillingerne i kaldet bruges kun til denne ene prøve og renses som alt andet. Anmeldelsen
    skal høre til brugerens egen café. Prøven tæller som et udkast under lofterne.
    """
    review = await reviews.find_owned(body.review_id, user["restaurant_id"])
    if review is None:
        raise ApiError(404, "Anmeldelsen findes ikke.")

    values = body.settings.model_dump()
    values["about"] = settings_service.clean_about(values["about"])
    recent = await reply_settings.recent_sent_replies(user["restaurant_id"], claude_service.MAX_RECENT_REPLIES)
    style = settings_service.to_style(values, recent)

    restaurant, event_ids = await _allow_drafts(user=user, count=1, settings=settings, limits=limits, http=http)
    draft = await _draft(
        claude, settings=settings, restaurant=restaurant, review=review, style=style, event_id=event_ids[0]
    )
    return {"draft": draft.text}
