"""Login: adgangskoder, sessioner og godkendelse.

Adgangskoder gemmes aldrig som tekst, men som en argon2-hash. Det er en envejs-udregning,
der med vilje er langsom, så en tyv med databasen ikke kan prøve millioner af gæt i
sekundet. Læs mere: https://en.wikipedia.org/wiki/Argon2
"""

import asyncio
import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.models import users

SESSION_COOKIE = "session"
SESSION_LIFETIME = timedelta(days=7)

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128  # loft, så ingen kan sende en gigantisk "adgangskode" for at spilde CPU

# Standardindstillingerne er argon2id, som er det der anbefales i dag.
_hasher = PasswordHasher()

# Bogstaver og tal der ikke ligner hinanden (ingen 0/O eller 1/l/I), så en genereret
# adgangskode kan læses op i telefonen.
_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# En hash til en adgangskode ingen kender. Bruges til at bruge lige lang tid på at afvise
# en e-mail der ikke findes som en der findes, så man ikke kan gætte hvem der er kunde.
_DUMMY_HASH = _hasher.hash("en-adgangskode-som-ingen-har")


def normalize_email(email: str) -> str:
    """Gør en e-mail ens: fjerner mellemrum og laver små bogstaver."""
    return email.strip().lower()


def validate_new_password(password: str) -> str | None:
    """Tjekker en ny adgangskode. Returnerer en fejltekst, eller None hvis den er god nok."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Adgangskoden skal være mindst {MIN_PASSWORD_LENGTH} tegn."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Adgangskoden må højst være {MAX_PASSWORD_LENGTH} tegn."
    if password.strip() != password:
        return "Adgangskoden må ikke starte eller slutte med mellemrum."
    if len(set(password)) < 5:
        return "Adgangskoden er for nem at gætte."
    return None


def generate_password() -> str:
    """Laver en tilfældig adgangskode, fx "k7Fpq-Tm3nX-h9RdA" (15 tegn, ca. 87 bit)."""
    parts = ["".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(5)) for _ in range(3)]
    return "-".join(parts)


def new_session_token() -> str:
    """Laver en tilfældig nøgle til en ny session. Den ligger kun i brugerens cookie."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Laver et fingeraftryk af en session-nøgle. Det er kun det, der gemmes i databasen."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def hash_password(password: str) -> str:
    """Laver en hash af en adgangskode. Køres i en tråd, fordi den er langsom med vilje."""
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password: str, stored_hash: str) -> bool:
    """Tjekker om en adgangskode passer til en gemt hash."""
    try:
        return await asyncio.to_thread(_hasher.verify, stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def authenticate(email: str, password: str) -> dict | None:
    """Tjekker e-mail og adgangskode. Returnerer brugeren, eller None hvis noget er forkert.

    Ved en ukendt e-mail eller en slået-fra bruger regnes der alligevel på en hash, så
    svartiden ikke afslører om e-mailen findes.
    """
    user = await users.find_by_email(normalize_email(email))
    if user is None or not user["is_active"]:
        await verify_password(password, _DUMMY_HASH)
        return None

    if not await verify_password(password, user["password_hash"]):
        return None

    # Bliver argon2's anbefalede indstillinger skærpet en dag, opgraderes hashen stille og roligt
    # næste gang brugeren logger ind.
    if _hasher.check_needs_rehash(user["password_hash"]):
        await users.update_password(user["id"], await hash_password(password))
    return user
