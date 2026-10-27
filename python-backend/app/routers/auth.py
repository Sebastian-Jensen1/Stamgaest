"""Endpoints til login: log ind, log ud, hvem er jeg, skift adgangskode, glemt adgangskode og skift e-mail."""

from datetime import datetime, timezone

import logging

import httpx
import psycopg
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from app.core.config import Settings
from app.core.deps import CurrentUser, get_http, get_limits, get_settings
from app.core.errors import ApiError
from app.core.rate_limit import Limits, describe_wait
from app.models import email_changes, password_tokens, restaurants, sessions, users
from app.schemas.auth import (
    ChangeEmailRequest,
    ChangePasswordRequest,
    ConfirmEmailRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    ResetPasswordRequest,
)
from app.schemas.reviews import OkResponse
from app.services import auth_service, email_service
from app.services.auth_service import SESSION_COOKIE, SESSION_LIFETIME

logger = logging.getLogger("review_assistant")

router = APIRouter()


def _client_ip(request: Request) -> str:
    """Giver IP-adressen på den der kalder (bruges til at begrænse gætte-forsøg).

    Bag en kendt proxy er det kundens rigtige IP, ikke proxyens (se ProxyHeadersMiddleware i main.py).
    """
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


async def _send_password_changed_mail(http: httpx.AsyncClient, settings: Settings, email: str) -> None:
    """Sender mailen "din adgangskode er ændret". Køres efter svaret, så en langsom mail ikke venter."""
    try:
        await email_service.send_password_changed(http, settings, to=email, when=datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001 - en baggrundsopgave har ingen at svare til, så fejlen logges
        logger.exception("Kunne ikke sende mail om ændret adgangskode")


@router.post("/auth/password", response_model=OkResponse)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    background: BackgroundTasks,
    limits: Limits = Depends(get_limits),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http),
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
    # Et ventende skift af e-mail kan være startet af en anden med den gamle adgangskode.
    await email_changes.delete_for_user(user["user_id"])
    # Har en anden fået fat i en åben browser og skiftet koden, opdager den rigtige ejer det her.
    background.add_task(_send_password_changed_mail, http, settings, stored["email"])
    return OkResponse()


async def _send_reset_mail(http: httpx.AsyncClient, settings: Settings, user: dict) -> None:
    """Laver et nulstillingslink til brugeren og sender det på mail.

    Køres EFTER svaret er sendt til browseren. Så tager svaret lige lang tid, uanset om e-mailen
    findes (og dermed om der skal sendes en mail), og man kan ikke gætte hvem der er kunde.
    """
    try:
        token = auth_service.new_reset_token()
        await password_tokens.delete_expired()
        await password_tokens.create(
            token_hash=auth_service.hash_token(token),
            user_id=user["id"],
            expires_at=datetime.now(timezone.utc) + auth_service.RESET_TOKEN_LIFETIME,
        )
        await email_service.send_password_reset(
            http,
            settings,
            to=user["email"],
            token=token,
            minutes=int(auth_service.RESET_TOKEN_LIFETIME.total_seconds() // 60),
        )
    except Exception:  # noqa: BLE001 - en baggrundsopgave har ingen at svare til, så fejlen logges
        logger.exception("Kunne ikke sende nulstillingsmail")


@router.post("/auth/forgot-password", response_model=OkResponse)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    limits: Limits = Depends(get_limits),
    http: httpx.AsyncClient = Depends(get_http),
):
    """POST /api/auth/forgot-password  Sender et link til at vælge ny adgangskode, hvis e-mailen findes.

    Svarer ALTID ens, uanset om e-mailen findes, så man ikke kan gætte hvem der er kunde.
    """
    email = auth_service.normalize_email(body.email)
    ip = _client_ip(request)

    # Grænsen tæller alle forsøg, også på e-mails der ikke findes. Så siger den ikke noget om hvem
    # der er kunde, og ingen kan fylde en andens indbakke med mails.
    waits = [w for w in (limits.forgot_email.check(email), limits.forgot_ip.check(ip)) if w]
    if waits:
        raise _too_many_attempts(max(waits))
    limits.forgot_email.record(email)
    limits.forgot_ip.record(ip)

    user = await users.find_by_email(email)
    # Er kunden inviteret men har ikke valgt en adgangskode endnu, sendes der intet. Kun vi kan
    # sende en ny invitation. Ellers kunne en fejlindtastet e-mail overtage en café ad omvejen
    # efter at vi har trukket invitationen tilbage.
    if user is not None and user["is_active"] and not user["invite_pending"]:
        background.add_task(_send_reset_mail, http, settings, user)
    return OkResponse()


@router.post("/auth/reset-password", response_model=OkResponse)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    limits: Limits = Depends(get_limits),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http),
):
    """POST /api/auth/reset-password  Vælger ny adgangskode med nøglen fra mailen og logger ud alle steder."""
    ip = _client_ip(request)
    wait = limits.reset_ip.check(ip)
    if wait:
        raise _too_many_attempts(wait)

    # Kravene til adgangskoden tjekkes FØR linket bruges. Ellers ville en for kort adgangskode
    # opbruge linket, og kunden skulle bestille et nyt.
    problem = auth_service.validate_new_password(body.new_password)
    if problem:
        raise ApiError(400, problem)

    user = await password_tokens.use(auth_service.hash_token(body.token))
    if user is None:
        limits.reset_ip.record(ip)
        # Samme besked uanset om linket er ukendt, udløbet eller brugt.
        raise ApiError(400, "Linket virker ikke længere. Bed om et nyt.")

    await users.update_password(user["user_id"], await auth_service.hash_password(body.new_password))
    # Hvem der end var logget ind (måske en der havde fået adgang), er det ikke længere. Og er
    # kunden blevet spærret af for mange forkerte logins, kan de logge ind igen med det samme.
    await users.set_invite_pending(user["user_id"], False)  # kunden har nu valgt en adgangskode
    await sessions.delete_for_user(user["user_id"])
    await password_tokens.delete_for_user(user["user_id"])
    # Tager kunden kontoen tilbage, må et skift af e-mail som en anden har startet, ikke kunne fuldføres.
    await email_changes.delete_for_user(user["user_id"])
    limits.login_email.reset(auth_service.normalize_email(user["email"]))
    # En ny kunde der vælger sin FØRSTE adgangskode fra invitationen, har ikke ændret noget.
    if not user["invite_pending"]:
        background.add_task(_send_password_changed_mail, http, settings, user["email"])
    return OkResponse()


async def _send_email_change_mail(http: httpx.AsyncClient, settings: Settings, user_id, new_email: str) -> None:
    """Sender bekræftelseslinket til den nye adresse, eller en besked hvis adressen allerede er i brug.

    Køres EFTER svaret er sendt. Så tager svaret lige lang tid, uanset om adressen er ledig, og man
    kan ikke bruge "skift e-mail" til at gætte hvem der er kunde.
    """
    try:
        if await users.find_by_email(new_email) is not None:
            await email_service.send_email_in_use(http, settings, to=new_email)
            return
        token = auth_service.new_email_token()
        await email_changes.delete_expired()
        await email_changes.create(
            token_hash=auth_service.hash_token(token),
            user_id=user_id,
            new_email=new_email,
            expires_at=datetime.now(timezone.utc) + auth_service.EMAIL_CHANGE_LIFETIME,
        )
        await email_service.send_email_change_confirmation(
            http,
            settings,
            to=new_email,
            token=token,
            hours=int(auth_service.EMAIL_CHANGE_LIFETIME.total_seconds() // 3600),
        )
    except Exception:  # noqa: BLE001 - en baggrundsopgave har ingen at svare til, så fejlen logges
        logger.exception("Kunne ikke sende mail om skift af e-mail")


@router.post("/auth/email", response_model=OkResponse)
async def change_email(
    body: ChangeEmailRequest,
    user: CurrentUser,
    background: BackgroundTasks,
    limits: Limits = Depends(get_limits),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http),
):
    """POST /api/auth/email  Sender et link til den nye e-mail. Den gælder først, når linket er åbnet.

    Svarer ens, uanset om den nye adresse er ledig (se _send_email_change_mail).
    """
    email = user["email"]
    user_key = str(user["user_id"])

    new_email = auth_service.normalize_email(body.new_email)
    problem = auth_service.validate_email(new_email)
    if problem:
        raise ApiError(400, problem)
    if new_email == auth_service.normalize_email(email):
        raise ApiError(400, "Det er allerede din e-mail.")

    wait = limits.change_email.check(user_key) or limits.login_email.check(email)
    if wait:
        raise _too_many_attempts(wait)

    # Adgangskoden kræves, så en der har fået fat i en åben browser ikke kan flytte kontoen til sig selv.
    stored = await users.find_by_email(email)
    if stored is None or not await auth_service.verify_password(body.current_password, stored["password_hash"]):
        limits.login_email.record(email)
        # 400 og ikke 401: en 401 ville få siden til at tro man var logget ud.
        raise ApiError(400, "Adgangskoden er forkert.")

    limits.change_email.record(user_key)
    background.add_task(_send_email_change_mail, http, settings, user["user_id"], new_email)
    return OkResponse()


async def _send_email_changed_mail(http: httpx.AsyncClient, settings: Settings, old_email: str, new_email: str) -> None:
    """Sender mailen "din e-mail er skiftet" til den gamle adresse. Køres efter svaret."""
    try:
        await email_service.send_email_changed(
            http, settings, to=old_email, new_email=new_email, when=datetime.now(timezone.utc)
        )
    except Exception:  # noqa: BLE001 - en baggrundsopgave har ingen at svare til, så fejlen logges
        logger.exception("Kunne ikke sende mail om skiftet e-mail")


@router.post("/auth/confirm-email", response_model=OkResponse)
async def confirm_email(
    body: ConfirmEmailRequest,
    request: Request,
    background: BackgroundTasks,
    limits: Limits = Depends(get_limits),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http),
):
    """POST /api/auth/confirm-email  Skifter e-mail med nøglen fra mailen. Kræver ikke login.

    Kunden åbner måske linket på telefonen, hvor de ikke er logget ind. Nøglen er beviset.
    """
    ip = _client_ip(request)
    wait = limits.reset_ip.check(ip)
    if wait:
        raise _too_many_attempts(wait)

    try:
        changed = await email_changes.use(auth_service.hash_token(body.token))
    except psycopg.errors.UniqueViolation:
        # Sjældent: en anden konto har fået adressen, efter linket blev sendt. Linket er ikke brugt op.
        raise ApiError(400, "Den e-mail bruges allerede af en anden konto.") from None
    if changed is None:
        limits.reset_ip.record(ip)
        # Samme besked uanset om linket er ukendt, udløbet eller brugt.
        raise ApiError(400, "Linket virker ikke længere. Bed om et nyt.")

    # Links til "glemt adgangskode" blev sendt til den gamle adresse, som kunden måske ikke har længere.
    await password_tokens.delete_for_user(changed["user_id"])
    await email_changes.delete_for_user(changed["user_id"])
    limits.login_email.reset(auth_service.normalize_email(changed["old_email"]))
    background.add_task(_send_email_changed_mail, http, settings, changed["old_email"], changed["new_email"])
    return OkResponse()
