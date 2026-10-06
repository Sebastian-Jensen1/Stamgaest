"""Endpoint der får Claude til at skrive udkast: POST /api/generate."""

import anthropic
from fastapi import APIRouter, Depends

from app.core.config import Settings
from app.core.deps import CurrentUser, get_claude, get_limits, get_settings
from app.core.errors import ApiError
from app.core.rate_limit import Limits, describe_wait
from app.models import replies, restaurants, reviews
from app.schemas.generate import GenerateRequest, GenerateResponse
from app.services import claude_service

router = APIRouter()


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    user: CurrentUser,
    claude: anthropic.AsyncAnthropic = Depends(get_claude),
    settings: Settings = Depends(get_settings),
    limits: Limits = Depends(get_limits),
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

    # Hvert udkast koster penge, så der er et loft pr. bruger pr. time.
    wait = limits.generate.check(str(user["user_id"]), len(review_ids))
    if wait:
        raise ApiError(429, f"Du har lavet mange udkast på kort tid. Prøv igen om {describe_wait(wait)}.")
    limits.generate.record(str(user["user_id"]), len(review_ids))

    restaurant = await restaurants.find_by_id(restaurant_id)
    if restaurant is None:
        raise ApiError(404, "Din café findes ikke.")

    # Husk den valgte tone til næste gang.
    if body.tone:
        await restaurants.update_default_tone(restaurant_id, body.tone)

    results = []
    for review_id in review_ids:
        # Anmeldelsens indhold og caféens navn kommer fra databasen, ikke fra browseren.
        review = found[review_id]
        reviewer_name = review["reviewer_name"] or ""
        review_text = review["review_text"] or ""

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
