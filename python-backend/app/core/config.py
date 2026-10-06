"""Indstillinger: API-nøgler, database og port.

Alt der står i .env læses her, ét sted. Resten af koden får en færdig `Settings`
i stedet for selv at kigge i .env.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]  # mappen python-backend/
REPO_DIR = BACKEND_DIR.parent  # hele projektet

# Læs python-backend/.env ind i miljøet. Er en værdi allerede sat i terminalen,
# vinder den (load_dotenv overskriver ikke noget der allerede er sat).
load_dotenv(BACKEND_DIR / ".env")

# Modellen der skriver udkastene. Kan ændres med CLAUDE_MODEL i .env.
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class Settings:
    """Alle indstillinger samlet ét sted. Kan ikke ændres efter den er lavet (frozen)."""

    port: int
    # repr=False: så nøglerne ikke bliver skrevet ud, hvis en Settings ved et uheld
    # havner i en log eller fejlbesked.
    database_url: str | None = field(repr=False)
    anthropic_api_key: str | None = field(repr=False)
    google_places_api_key: str | None = field(repr=False)
    claude_model: str
    allowed_hosts: tuple[str, ...]  # hvilke adresser serveren svarer på
    enable_docs: bool  # skal /docs være tændt?
    public_dir: Path  # mappen med index.html
    # Skal login-cookien kun sendes over HTTPS? Skal være True når siden ligger på et
    # rigtigt domæne. Lokalt (http://localhost) skal den være False, ellers virker login ikke.
    cookie_secure: bool


def _clean(value: str | None) -> str | None:
    """Fjerner mellemrum. En tom værdi bliver til None ("ikke sat")."""
    value = (value or "").strip()
    return value or None


def _port(raw: str | None) -> int:
    """Laver PORT om til et tal. Bruger 3000 hvis den ikke er sat, og fejler hvis den er ugyldig."""
    try:
        port = int(raw) if raw else 3000
    except ValueError:
        raise RuntimeError(f"PORT skal være et heltal, ikke {raw!r}.") from None
    if not 1 <= port <= 65535:
        raise RuntimeError(f"PORT skal være mellem 1 og 65535, ikke {port}.")
    return port


def load_settings() -> Settings:
    """Læser miljøet og returnerer en færdig Settings."""
    hosts = os.getenv("ALLOWED_HOSTS") or "localhost,127.0.0.1"
    return Settings(
        port=_port(_clean(os.getenv("PORT"))),
        database_url=_clean(os.getenv("DATABASE_URL")),
        anthropic_api_key=_clean(os.getenv("ANTHROPIC_API_KEY")),
        google_places_api_key=_clean(os.getenv("GOOGLE_PLACES_API_KEY")),
        claude_model=_clean(os.getenv("CLAUDE_MODEL")) or DEFAULT_CLAUDE_MODEL,
        allowed_hosts=tuple(h.strip() for h in hosts.split(",") if h.strip()),
        enable_docs=os.getenv("ENABLE_DOCS") == "1",
        public_dir=REPO_DIR / "public",
        cookie_secure=os.getenv("COOKIE_SECURE") == "1",
    )
