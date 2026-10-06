"""Afhængigheder: ting som routerne får leveret, i stedet for selv at skulle finde dem.

En route skriver fx `claude = Depends(get_claude)`, og FastAPI kalder så
`get_claude` og giver den resultatet.
"""

from typing import Annotated

import anthropic
import httpx
from fastapi import Depends, Request

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.rate_limit import Limits
from app.models import sessions
from app.services.auth_service import SESSION_COOKIE, hash_token


def get_settings(request: Request) -> Settings:
    """Giver indstillingerne (dem der blev lavet da appen startede)."""
    return request.app.state.settings


def get_limits(request: Request) -> Limits:
    """Giver grænserne for hvor ofte noget må ske (gætte-forsøg, udkast)."""
    return request.app.state.limits


def get_http(request: Request) -> httpx.AsyncClient:
    """Giver den delte HTTP-klient, som bruges til at kalde Google."""
    return request.app.state.http


def get_claude(request: Request) -> anthropic.AsyncAnthropic:
    """Giver klienten til Claude. Fejler med en tydelig besked hvis ANTHROPIC_API_KEY mangler."""
    client = request.app.state.anthropic
    if client is None:
        raise ApiError(500, "Mangler ANTHROPIC_API_KEY. Opret en .env-fil ud fra .env.example.")
    return client


def get_places_key(settings: Settings = Depends(get_settings)) -> str:
    """Giver Google-nøglen. Fejler med en tydelig besked hvis GOOGLE_PLACES_API_KEY mangler."""
    if not settings.google_places_api_key:
        raise ApiError(500, "Mangler GOOGLE_PLACES_API_KEY i .env.")
    return settings.google_places_api_key


async def get_current_user(request: Request) -> dict:
    """Finder ud af hvem der er logget ind, ud fra cookien. Svarer 401 hvis ingen er.

    Returnerer en dict med user_id, email, restaurant_id og token_hash. `restaurant_id` er
    DEN ENESTE kilde til hvilken café brugeren må se. Den kommer fra databasen, aldrig fra
    noget browseren har sendt med.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise ApiError(401, "Du skal logge ind.")

    user = await sessions.find_valid(hash_token(token))
    if user is None:
        raise ApiError(401, "Din login er udløbet. Log ind igen.")
    return user


# Skriv `user: CurrentUser` i en route for at kræve login og få brugeren udleveret.
CurrentUser = Annotated[dict, Depends(get_current_user)]
