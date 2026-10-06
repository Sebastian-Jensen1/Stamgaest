"""Endpoints for den indloggede kundes egen café: anmeldelser, "Hent", "Send" og "Markér som besvaret".

Alle endpoints kræver login (`user: CurrentUser`). Hvilken café det gælder, kommer ALTID fra
`user["restaurant_id"]`, som serveren selv har slået op i databasen. Browseren nævner aldrig
en café, og et anmeldelses-id fra en anden café giver "findes ikke" (404).
"""

from datetime import datetime, timezone
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_http, get_places_key
from app.core.errors import ApiError
from app.core.rate_limit import describe_wait
from app.models import replies, restaurants, reviews
from app.schemas.reviews import DashboardResponse, OkResponse, SendReplyRequest
from app.services import places_service

router = APIRouter()

# Pause mellem to hentninger fra Google for samme café. Hver hentning koster penge, og Google
# sender alligevel de samme 5 anmeldelser, så det giver ikke noget at hente oftere.
REFRESH_COOLDOWN_SECONDS = 60


def _dashboard(restaurant: dict, rows: list[dict]) -> dict:
    """Bygger svaret med café-oplysninger og anmeldelser, klar til frontenden."""
    return {
        "restaurant_id": restaurant["id"],
        "business_name": restaurant["name"],
        "business_type": restaurant["business_type"] or "",
        "default_tone": restaurant["default_tone"] or "",
        "address": restaurant["address"] or "",
        "rating": restaurant["google_rating"],
        "user_rating_count": restaurant["google_rating_count"],
        "reviews_fetched_at": restaurant["reviews_fetched_at"],
        "reviews": [reviews.to_client_review(row) for row in rows],
    }


async def _own_restaurant(user: dict) -> dict:
    """Henter brugerens café fra databasen. Den findes altid (brugeren kan ikke oprettes uden)."""
    restaurant = await restaurants.find_by_id(user["restaurant_id"])
    if restaurant is None:
        raise ApiError(404, "Din café findes ikke.")
    return restaurant


@router.get("/reviews", response_model=DashboardResponse)
async def my_reviews(user: CurrentUser):
    """GET /api/reviews  Din cafés oplysninger og alle dens gemte anmeldelser. Henter intet fra Google."""
    restaurant = await _own_restaurant(user)
    rows = await reviews.list_by_restaurant(restaurant["id"])
    return _dashboard(restaurant, rows)


@router.post("/reviews/refresh", response_model=DashboardResponse)
async def refresh_reviews(
    user: CurrentUser,
    api_key: str = Depends(get_places_key),
    http: httpx.AsyncClient = Depends(get_http),
):
    """POST /api/reviews/refresh  Henter de nyeste anmeldelser fra Google for DIN café og gemmer dem.

    Google-stedet er det, vi selv koblede caféen til, da kunden blev oprettet. Browseren kan
    ikke pege på et andet sted.
    """
    restaurant = await _own_restaurant(user)
    if not restaurant["google_place_id"]:
        raise ApiError(409, "Din café er ikke koblet til Google endnu. Kontakt os.")

    # Reservér hentningen FØR vi kalder Google. Sker det i samme SQL-sætning som tjekket,
    # kan to hurtige klik ikke begge slippe igennem.
    previous = restaurant["reviews_fetched_at"]
    if not await restaurants.claim_refresh(restaurant["id"], REFRESH_COOLDOWN_SECONDS):
        elapsed = (datetime.now(timezone.utc) - previous).total_seconds() if previous else 0
        wait = max(1, int(REFRESH_COOLDOWN_SECONDS - elapsed) + 1)
        raise ApiError(429, f"Anmeldelserne blev lige hentet. Prøv igen om {describe_wait(wait)}.")

    try:
        details = await places_service.get_place_details(http, restaurant["google_place_id"], api_key)
        restaurant = await restaurants.upsert_by_place_id(
            google_place_id=restaurant["google_place_id"],
            name=details.business_name or restaurant["name"],
            business_type=None,  # behold den branche vi allerede har
            address=details.address,
            google_rating=details.rating,
            google_rating_count=details.user_rating_count,
        )
        await reviews.upsert_many(restaurant["id"], details.reviews)
    except Exception:
        # Lykkedes hentningen ikke, skal kunden kunne prøve igen med det samme, ikke vente et minut.
        await restaurants.set_reviews_fetched_at(restaurant["id"], previous)
        raise

    # Læs ALT tilbage fra databasen, også ældre anmeldelser som Google ikke længere sender.
    rows = await reviews.list_by_restaurant(restaurant["id"])
    fresh = await _own_restaurant(user)
    return _dashboard(fresh, rows)


@router.post("/reviews/{review_id}/send", response_model=OkResponse)
async def send_reply(review_id: UUID, body: SendReplyRequest, user: CurrentUser):
    """POST /api/reviews/{id}/send  Gemmer den tekst ejeren sendte, og flytter anmeldelsen til "Besvarede".

    Teksten kan være redigeret i forhold til Claudes udkast, og det er den redigerede
    tekst der gemmes.
    """
    if await replies.mark_sent(review_id, user["restaurant_id"], body.final_text) is None:
        raise ApiError(404, "Anmeldelsen findes ikke.")
    return OkResponse()


@router.post("/reviews/{review_id}/answered", response_model=OkResponse)
async def mark_answered(review_id: UUID, user: CurrentUser):
    """POST /api/reviews/{id}/answered  Markerer som besvaret uden svartekst (ejeren svarede selv på Google)."""
    if await reviews.mark_answered(review_id, user["restaurant_id"]) is None:
        raise ApiError(404, "Anmeldelsen findes ikke.")
    return OkResponse()
