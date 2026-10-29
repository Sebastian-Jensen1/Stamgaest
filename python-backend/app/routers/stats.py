"""Endpoints for statistiksiden.

- GET  /api/stats         tal, udvikling over tid og emner for DIN café
- POST /api/stats/topics  starter mærkningen af de anmeldelser, der mangler emner (i baggrunden)

Hvilken café det gælder, kommer ALTID fra login (`user["restaurant_id"]`), og alle opslag
filtrerer på caféen i selve SQL'en.
"""

import anthropic
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.config import Settings
from app.core.deps import CurrentUser, get_claude_optional, get_http, get_settings
from app.core.errors import ApiError
from app.models import restaurants
from app.schemas.stats import StatsResponse, TopicsStartResponse
from app.services import stats_service, topics_service

router = APIRouter()


async def _own_restaurant(user: dict) -> dict:
    """Henter den indloggede kundes café."""
    restaurant = await restaurants.find_by_id(user["restaurant_id"])
    if restaurant is None:
        raise ApiError(404, "Din café findes ikke.")
    return restaurant


@router.get("/stats", response_model=StatsResponse)
async def my_stats(user: CurrentUser, settings: Settings = Depends(get_settings)):
    """GET /api/stats  Statistik for din café. Henter intet fra Google og kalder ikke Claude."""
    return await stats_service.build(await _own_restaurant(user), settings)


@router.post("/stats/topics", response_model=TopicsStartResponse)
async def start_topics(
    user: CurrentUser,
    background: BackgroundTasks,
    claude: anthropic.AsyncAnthropic | None = Depends(get_claude_optional),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http),
):
    """POST /api/stats/topics  Mærker de anmeldelser, der mangler emner, i baggrunden.

    Siden kalder den, når statistikken viser, at nogle mangler. Det er et POST og ikke en del af
    GET, fordi det kan koste penge: et GET kan udløses af en fremmed hjemmeside (et link), et POST
    afvises af Origin-tjekket. Kun umærkede anmeldelser sendes til Claude, så gentagne kald koster
    ikke ekstra, og lofterne gælder altid.
    """
    analyzing = await topics_service.start_if_needed(
        background, client=claude, settings=settings, http=http,
        restaurant=await _own_restaurant(user), user_id=user["user_id"],
    )
    return {"analyzing": analyzing}
