"""Endpoints til login: log ind, log ud, hvem er jeg, og skift adgangskode."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response

from app.core.config import Settings
from app.core.deps import CurrentUser, get_limits, get_settings
from app.core.errors import ApiError
from app.core.rate_limit import Limits, describe_wait
from app.models import restaurants, sessions, users
from app.schemas.auth import ChangePasswordRequest, LoginRequest, MeResponse
from app.schemas.reviews import OkResponse
from app.services import auth_service
from app.services.auth_service import SESSION_COOKIE, SESSION_LIFETIME

router = APIRouter()


def _client_ip(request: Request) -> str:
    """Giver IP-adressen på den der kalder (bruges til at begrænse gætte-forsøg)."""
    return request.client.host if request.client else "ukendt"


def _too_many_attempts(wait_seconds: int) -> ApiError:
    """Bygger fejlen "for mange forsøg" med en ventetid."""
    return ApiError(429, f"For mange forsøg. Prøv igen om {describe_wait(wait_seconds)}.")


async def _me(user: dict) -> dict:
    """Bygger svaret "hvem er jeg" ud fra en bruger."""
    restaurant = await restaurants.find_by_id(user["restaurant_id"])
    return {
        "email": user["email"],
        "restaurant_id": user["restaurant_id"],
        "business_name": restaurant["name"] if restaurant else "",
    }


@router.post("/auth/login", response_model=MeResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    limits: Limits = Depends(get_limits),
):
    """POST /api/auth/login  Logger ind og sætter en cookie. Svarer med hvem der er logget ind."""
    email = auth_service.normalize_email(body.email)
    ip = _client_ip(request)

    # Er der allerede prøvet for mange gange, afvises der FØR adgangskoden tjekkes. Så
    # kan man ikke gætte videre, selv ikke med den rigtige adgangskode, før tiden er gået.
    waits = [w for w in (limits.login_email.check(email), limits.login_ip.check(ip)) if w]
    if waits:
        raise _too_many_attempts(max(waits))

    user = await auth_service.authenticate(email, body.password)
    if user is None:
        limits.login_email.record(email)
        limits.login_ip.record(ip)
        # Samme besked uanset om e-mailen findes, så man ikke kan gætte hvem der er kunde.
        raise ApiError(401, "Forkert e-mail eller adgangskode.")
    limits.login_email.reset(email)

    # Hver login får en helt ny nøgle. En gammel nøgle (fx fra en anden bruger på samme
    # computer) bliver slettet, så den ikke kan genbruges.
    old_token = request.cookies.get(SESSION_COOKIE)
    if old_token:
        await sessions.delete(auth_service.hash_token(old_token))
    await sessions.delete_expired()

    token = auth_service.new_session_token()
    await sessions.create(
        token_hash=auth_service.hash_token(token),
        user_id=user["id"],
        expires_at=datetime.now(timezone.utc) + SESSION_LIFETIME,
    )
    await users.touch_login(user["id"])

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,  # JavaScript på siden kan ikke læse cookien, så et XSS-hul kan ikke stjæle den
        samesite="lax",  # sendes ikke med når en fremmed side sender formularer til os
        secure=settings.cookie_secure,
        path="/",
    )
    return await _me({"email": user["email"], "restaurant_id": user["restaurant_id"]})


@router.post("/auth/logout", response_model=OkResponse)
async def logout(request: Request, response: Response):
    """POST /api/auth/logout  Sletter sessionen og cookien. Virker også hvis man ikke er logget ind."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await sessions.delete(auth_service.hash_token(token))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return OkResponse()


@router.get("/auth/me", response_model=MeResponse)
async def me(user: CurrentUser):
    """GET /api/auth/me  Hvem er logget ind? Svarer 401 hvis ingen er. Siden bruger den ved opstart."""
    return await _me(user)


@router.post("/auth/password", response_model=OkResponse)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    limits: Limits = Depends(get_limits),
):
    """POST /api/auth/password  Skifter adgangskode og logger alle ANDRE enheder ud."""
    email = user["email"]

    # Samme grænse som ved login, så en der har stjålet en åben browser ikke kan gætte
    # den nuværende adgangskode i det uendelige.
    wait = limits.login_email.check(email)
    if wait:
        raise _too_many_attempts(wait)

    stored = await users.find_by_email(email)
    if stored is None or not await auth_service.verify_password(body.current_password, stored["password_hash"]):
        limits.login_email.record(email)
        # 400 og ikke 401: en 401 ville få siden til at tro man var logget ud.
        raise ApiError(400, "Den nuværende adgangskode er forkert.")

    problem = auth_service.validate_new_password(body.new_password)
    if problem:
        raise ApiError(400, problem)
    if body.new_password == body.current_password:
        raise ApiError(400, "Den nye adgangskode skal være en anden end den gamle.")

    await users.update_password(user["user_id"], await auth_service.hash_password(body.new_password))
    await sessions.delete_for_user(user["user_id"], except_token_hash=user["token_hash"])
    return OkResponse()
