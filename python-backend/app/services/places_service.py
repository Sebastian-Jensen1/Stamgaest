"""Snakker med Google Places API: søg efter steder og hent deres anmeldelser.

Google svarer med en stor JSON-blok. Funktionerne her henter den, og de små
funktioner med _ foran laver den om til pæne Python-objekter (PlaceSummary,
GoogleReview, PlaceDetails). Alt fra Google behandles som utroværdigt: det kan
mangle felter eller have den forkerte type, uden at programmet må gå i stykker.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import UpstreamError

logger = logging.getLogger("review_assistant")

PLACES_BASE_URL = "https://places.googleapis.com/v1"

# Et Google place_id består kun af bogstaver, tal, "_" og "-". Alt andet afvises, før det
# kommer i nærheden af en adresse vi bygger til Google.
PLACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,300}$")

# Google sender højst 5 anmeldelser. Loftet er kun en sikring, hvis det ændrer sig.
MAX_REVIEWS = 10


def is_valid_place_id(place_id: str) -> bool:
    """Ser dette ud som et rigtigt Google place_id? (Tjekker kun tegnene, ikke om stedet findes.)"""
    return bool(PLACE_ID_PATTERN.fullmatch(place_id))


@dataclass(frozen=True)
class PlaceSummary:
    """Et søgeresultat: nok til at brugeren kan vælge det rigtige sted."""

    place_id: str
    name: str
    address: str


@dataclass(frozen=True)
class GoogleReview:
    """Én anmeldelse fra Google, renset og klar til at blive gemt."""

    external_id: str
    reviewer_name: str
    rating: int | None
    review_text: str
    published_at: datetime | None


@dataclass(frozen=True)
class PlaceDetails:
    """Et sted med dets score og anmeldelser."""

    business_name: str
    address: str
    rating: float | None
    user_rating_count: int | None
    reviews: list[GoogleReview]


def _as_dict(value: Any) -> dict:
    """Giver værdien tilbage hvis den er en dict, ellers en tom dict."""
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    """Giver værdien tilbage hvis den er tekst, ellers en tom tekst."""
    return value if isinstance(value, str) else ""


def _parse_time(value: Any) -> datetime | None:
    """Laver Googles tidspunkt (fx "2026-01-01T12:00:00Z") om til en dato. None hvis det ikke kan."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _rating(value: Any) -> int | None:
    """Laver en score om til et helt tal mellem 1 og 5. Alt andet bliver None.

    Databasen afviser scorer udenfor 1-5, og det ville vælte hele hentningen.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    rounded = round(value)
    return rounded if 1 <= rounded <= 5 else None


def _to_place_summary(place: dict) -> PlaceSummary:
    """Laver ét søgeresultat fra Google om til en PlaceSummary."""
    return PlaceSummary(
        place_id=_text(place.get("id")),
        name=_text(_as_dict(place.get("displayName")).get("text")),
        address=_text(place.get("formattedAddress")),
    )


def _to_review(raw: dict) -> GoogleReview:
    """Laver én anmeldelse fra Google om til en GoogleReview."""
    return GoogleReview(
        external_id=_text(raw.get("name")),
        reviewer_name=_text(_as_dict(raw.get("authorAttribution")).get("displayName")),
        rating=_rating(raw.get("rating")),
        review_text=_text(_as_dict(raw.get("text")).get("text"))
        or _text(_as_dict(raw.get("originalText")).get("text")),
        published_at=_parse_time(raw.get("publishTime")),
    )


def _to_place_details(data: dict) -> PlaceDetails:
    """Laver Googles svar om et sted til en PlaceDetails."""
    rating = data.get("rating")
    count = data.get("userRatingCount")
    raw_reviews = data.get("reviews")
    reviews = [
        _to_review(raw)
        for raw in (raw_reviews if isinstance(raw_reviews, list) else [])[:MAX_REVIEWS]
        if isinstance(raw, dict)
    ]
    return PlaceDetails(
        business_name=_text(_as_dict(data.get("displayName")).get("text")),
        address=_text(data.get("formattedAddress")),
        rating=float(rating) if isinstance(rating, (int, float)) and not isinstance(rating, bool) else None,
        user_rating_count=count if isinstance(count, int) and not isinstance(count, bool) else None,
        # Anmeldelser uden id springes over: vi kunne ikke genkende dem næste gang.
        reviews=[r for r in reviews if r.external_id],
    )


async def _request_json(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> dict:
    """Kalder Google og returnerer svaret som en dict. Al fejlhåndtering er samlet her.

    Hvad Google faktisk svarede skrives kun i loggen. Brugeren får en tekst vi selv
    har skrevet, så interne detaljer ikke lækker.
    """
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("Google Places kunne ikke nås: %s", type(exc).__name__)
        raise UpstreamError("Kunne ikke få forbindelse til Google. Prøv igen om lidt.") from None

    if response.status_code != 200:
        logger.warning("Google Places svarede %s: %.500s", response.status_code, response.text)
        if response.status_code == 400:
            # Google bruger 400 både til en forkert nøgle og et forkert sted-id. Vi
            # kigger i deres tekst kun for at vælge MELLEM vores egne beskeder.
            if "API_KEY_INVALID" in response.text:
                raise UpstreamError("Google afviste API-nøglen. Tjek GOOGLE_PLACES_API_KEY i .env.")
            if "Place ID" in response.text:
                raise UpstreamError(
                    "Google kender ikke det sted-id. Tjek at det er kopieret rigtigt "
                    "(find det med: uv run python -m app.admin search).",
                    400,
                )
        if response.status_code == 404:
            raise UpstreamError("Stedet blev ikke fundet hos Google.")
        if response.status_code == 429:
            raise UpstreamError("Google har for mange forespørgsler lige nu. Prøv igen om lidt.", 503)
        raise UpstreamError(
            f"Google Places afviste kaldet ({response.status_code}). Tjek GOOGLE_PLACES_API_KEY, "
            "at 'Places API (New)' er aktiveret, og at projektet har en billing-konto."
        )

    try:
        data = response.json()
    except ValueError:
        logger.warning("Google Places svarede med noget der ikke er JSON")
        raise UpstreamError("Google svarede med noget uventet. Prøv igen om lidt.") from None
    return _as_dict(data)


async def search_places(client: httpx.AsyncClient, query: str, api_key: str) -> list[PlaceSummary]:
    """Søger efter steder ud fra en tekst, fx "Café Nordvest København"."""
    data = await _request_json(
        client,
        "POST",
        f"{PLACES_BASE_URL}/places:searchText",
        headers={
            # Nøglen sendes i en header og ikke i URL'en, for URL'er havner i logfiler.
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress",
        },
        json={"textQuery": query, "languageCode": "da"},
    )
    places = data.get("places")
    return [_to_place_summary(p) for p in (places if isinstance(places, list) else []) if isinstance(p, dict)]


async def get_place_details(client: httpx.AsyncClient, place_id: str, api_key: str) -> PlaceDetails:
    """Henter et steds oplysninger og dets seneste anmeldelser.

    OBS: Google giver højst 5 anmeldelser pr. sted, uanset hvor mange der findes.
    """
    data = await _request_json(
        client,
        "GET",
        # quote() gør at tegn som "/" i place_id ikke kan ændre hvilken adresse hos
        # Google vi kalder. Routeren afviser også mærkelige id'er, så dette er
        # den anden sikring.
        f"{PLACES_BASE_URL}/places/{quote(place_id, safe='')}",
        params={"languageCode": "da"},
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "id,displayName,formattedAddress,rating,userRatingCount,reviews",
        },
    )
    return _to_place_details(data)
