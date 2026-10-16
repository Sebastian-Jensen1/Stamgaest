"""Endpoints for caféens indstillinger for svar og "Byg profil fra hjemmesiden".

Hvilken café det gælder, kommer ALTID fra login (`user["restaurant_id"]`). Browseren sender
aldrig et café-id, og alle opslag filtrerer på caféen i selve SQL'en.
"""

import anthropic
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.config import Settings
from app.core.deps import CurrentUser, get_claude, get_http, get_settings
from app.core.errors import ApiError
from app.models import reply_settings, restaurants, usage
from app.schemas.reply_settings import ReplySettingsOut, SaveReplySettingsRequest
from app.schemas.reviews import OkResponse
from app.services import profile_service, settings_service, usage_service

router = APIRouter()


async def _own_restaurant(user: dict) -> dict:
    """Henter den indloggede kundes café."""
    restaurant = await restaurants.find_by_id(user["restaurant_id"])
    if restaurant is None:
        raise ApiError(404, "Din café findes ikke.")
    return restaurant


async def _response(restaurant: dict) -> dict:
    """Bygger svaret: indstillingerne plus caféens hjemmeside (fra Google)."""
    settings = await settings_service.load(restaurant["id"])
    return {**settings, "website_url": restaurant.get("website_url")}


@router.get("/reply-settings", response_model=ReplySettingsOut)
async def get_reply_settings(user: CurrentUser):
    """GET /api/reply-settings  Caféens indstillinger for svar."""
    return await _response(await _own_restaurant(user))


@router.put("/reply-settings", response_model=ReplySettingsOut)
async def save_reply_settings(body: SaveReplySettingsRequest, user: CurrentUser):
    """PUT /api/reply-settings  Gemmer caféens indstillinger. At gemme fakta om stedet godkender dem."""
    restaurant = await _own_restaurant(user)
    values = body.model_dump()
    values["about"] = settings_service.clean_about(values["about"])
    await reply_settings.save(restaurant["id"], values, clear_suggestion=body.clear_suggestion)
    return await _response(restaurant)


@router.delete("/reply-settings/suggestion", response_model=OkResponse)
async def discard_suggestion(user: CurrentUser):
    """DELETE /api/reply-settings/suggestion  Kasserer Claudes forslag til fakta om stedet."""
    await reply_settings.discard_suggestion(user["restaurant_id"])
    return OkResponse()


@router.post("/reply-settings/profile", response_model=OkResponse, status_code=202)
async def build_profile(
    user: CurrentUser,
    background: BackgroundTasks,
    claude: anthropic.AsyncAnthropic = Depends(get_claude),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http),
):
    """POST /api/reply-settings/profile  Starter "Byg profil fra hjemmesiden" i baggrunden.

    Det tager typisk et halvt til et helt minut, så svaret kommer med det samme (202), og siden
    spørger efter status med GET /api/reply-settings. Forslaget skal godkendes af ejeren.
    """
    restaurant = await _own_restaurant(user)
    if not restaurant.get("website_url"):
        raise ApiError(409, "Google har ingen hjemmeside for jeres café. Skriv fakta om stedet selv.")
    if settings.ai_paused:
        raise ApiError(503, "Forslag fra hjemmesiden er midlertidigt slået fra. Prøv igen senere.")
    if not restaurant["ai_enabled"]:
        raise ApiError(403, "Forslag fra hjemmesiden er slået fra for din café. Kontakt Stamgæst.")

    # Pladsen under lofterne reserveres FØR noget startes. Kører der allerede en, frigives den igen.
    event_ids = await usage_service.reserve(
        kind=usage.PROFILE, restaurant=restaurant, user_id=user["user_id"], count=1, settings=settings, http=http,
    )
    if not await reply_settings.claim_profile_build(restaurant["id"]):
        await usage.skip(event_ids)
        raise ApiError(409, "Profilen er allerede ved at blive bygget. Vent et øjeblik.")

    background.add_task(
        profile_service.run_profile_job,
        claude,
        model=settings.claude_model,
        restaurant=restaurant,
        event_id=event_ids[0],
    )
    return OkResponse()
