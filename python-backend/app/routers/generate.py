"""Endpoint der får Claude til at skrive udkast: POST /api/generate."""

import anthropic
import httpx
from fastapi import APIRouter, Depends

from app.core.config import Settings
from app.core.deps import CurrentUser, get_claude, get_http, get_limits, get_settings
from app.core.errors import ApiError
from app.core.rate_limit import Limits, describe_wait
from app.models import replies, restaurants, reviews, usage
from app.schemas.generate import GenerateRequest, GenerateResponse
from app.services import claude_service, usage_service

router = APIRouter()


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

    restaurant = await restaurants.find_by_id(restaurant_id)
    if restaurant is None:
        raise ApiError(404, "Din café findes ikke.")

    # Nødbremserne: for alle (AI_PAUSED=1 i .env) og for én café ('ai-off' i admin-værktøjet).
    if settings.ai_paused:
        raise ApiError(503, "Udkast er midlertidigt slået fra. Prøv igen senere.")
    if not restaurant["ai_enabled"]:
        raise ApiError(403, "Udkast er slået fra for din café. Kontakt Stamgæst.")

    # Hvert udkast koster penge, så der er et loft pr. bruger pr. time.
    wait = limits.generate.check(str(user["user_id"]), len(review_ids))
    if wait:
        raise ApiError(429, f"Du har lavet mange udkast på kort tid. Prøv igen om {describe_wait(wait)}.")
    limits.generate.record(str(user["user_id"]), len(review_ids))

    # Lofterne pr. café og for hele Stamgæst, pr. dag og pr. måned. Pladsen reserveres i
    # databasen FØR Claude kaldes, så samtidige klik ikke kan komme over loftet.
    event_ids = await usage_service.reserve(
        kind=usage.DRAFT, restaurant=restaurant, user_id=user["user_id"],
        count=len(review_ids), settings=settings, http=http,
    )

    # Husk den valgte tone til næste gang.
    if body.tone:
        await restaurants.update_default_tone(restaurant_id, body.tone)

    results = []
    for index, review_id in enumerate(review_ids):
        # Anmeldelsens indhold og caféens navn kommer fra databasen, ikke fra browseren.
        review = found[review_id]
        reviewer_name = review["reviewer_name"] or ""
        review_text = review["review_text"] or ""

        try:
            draft = await claude_service.draft_review_reply(
                claude,
                model=settings.claude_model,
                business_name=restaurant["name"],
                business_type=restaurant["business_type"] or "",
                reviewer_name=reviewer_name,
                rating=review["rating"],
                review_text=review_text,
                tone=body.tone,
            )
        except Exception:
            # Dette kald fejlede, men tæller stadig (det kan have kostet penge). De resterende blev
            # aldrig sendt, så de frigives.
            await usage.fail(event_ids[index : index + 1])
            await usage.skip(event_ids[index + 1 :])
            raise
        await usage.finish(
            event_ids[index], model=draft.model,
            input_tokens=draft.input_tokens, output_tokens=draft.output_tokens,
        )

        # Udkastet gemmes med det samme, ét ad gangen. Fejler nummer 4 af 5, er de
        # første tre stadig gemt (og betalt for), så de går ikke tabt.
        await replies.insert_draft(
            review_id=review_id,
            draft_text=draft.text,
            tone=body.tone,
            model=draft.model,
        )

        results.append(
            {
                "id": review_id,
                "reviewer_name": reviewer_name,
                "rating": review["rating"],
                "review_text": review_text,
                "draft": draft.text,
                "model": draft.model,
            }
        )

    return {"results": results}
