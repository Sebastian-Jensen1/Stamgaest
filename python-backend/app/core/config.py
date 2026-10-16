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
class Caps:
    """Lofter for én slags betalt kald: pr. café og for hele Stamgæst, pr. dag og pr. måned.

    Dag og måned regnes i dansk tid. Lofterne for "hele Stamgæst" er den vigtigste bremse: de
    holder, selv hvis mange konti misbruges på én gang.
    """

    cafe_day: int
    cafe_month: int
    all_day: int
    all_month: int


# Udkast fra Claude. Et udkast koster ca. 0,04 kr. med claude-sonnet-4-6 (oktober 2026).
DEFAULT_DRAFT_CAPS = Caps(cafe_day=100, cafe_month=1000, all_day=1000, all_month=10000)
# Hentninger fra Google Places (hver er ét betalt kald).
DEFAULT_FETCH_CAPS = Caps(cafe_day=20, cafe_month=300, all_day=200, all_month=3000)
# "Byg profil fra hjemmesiden": Claude læser hjemmesiden (nogle kroner pr. gang). Sker sjældent.
DEFAULT_PROFILE_CAPS = Caps(cafe_day=3, cafe_month=10, all_day=30, all_month=200)

# Afsender indtil vi har et bekræftet domæne hos Resend (kan kun sende til din egen mail).
DEFAULT_MAIL_FROM = "Stamgæst <onboarding@resend.dev>"


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
    # Mails (nulstilling af adgangskode). Mangler nøglen, sendes der ingen mails.
    resend_api_key: str | None = field(default=None, repr=False)
    # Afsenderen, fx "Stamgæst <noreply@mail.stamgaest.com>". Adressen skal høre til et domæne
    # der er bekræftet hos Resend. Resends testadresse virker kun til din egen mail.
    mail_from: str = DEFAULT_MAIL_FROM
    # Adressen siden ligger på. Bruges til linket i mails, uden skråstreg til sidst.
    app_base_url: str = "http://localhost:3000"
    # Lofter for betalte kald (se Caps).
    draft_caps: Caps = DEFAULT_DRAFT_CAPS
    fetch_caps: Caps = DEFAULT_FETCH_CAPS
    profile_caps: Caps = DEFAULT_PROFILE_CAPS
    # Nødbremse: True slår udkast fra Claude fra for ALLE caféer.
    ai_paused: bool = False
    # Hvem får en mail, når et loft er ved at blive nået. Mangler den, skrives det kun i loggen.
    admin_email: str | None = None
    # Claudes pris i USD pr. million tokens. Bruges kun til det anslåede beløb i `usage`.
    claude_price_input_per_mtok: float = 3.0
    claude_price_output_per_mtok: float = 15.0


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


def _positive_int(name: str, default: int) -> int:
    """Læser et loft fra miljøet. Bruger standardværdien hvis det ikke er sat, og fejler hvis det er ugyldigt."""
    raw = _clean(os.getenv(name))
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} skal være et heltal, ikke {raw!r}.") from None
    if value < 0:
        raise RuntimeError(f"{name} må ikke være negativ.")
    return value


def _price(name: str, default: float) -> float:
    """Læser en pris (USD pr. million tokens) fra miljøet."""
    raw = _clean(os.getenv(name))
    if raw is None:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise RuntimeError(f"{name} skal være et tal, ikke {raw!r}.") from None


def _caps(prefix: str, default: Caps) -> Caps:
    """Læser de fire lofter med et fælles navn, fx CAP_DRAFTS_CAFE_DAY, CAP_DRAFTS_CAFE_MONTH osv."""
    return Caps(
        cafe_day=_positive_int(f"{prefix}_CAFE_DAY", default.cafe_day),
        cafe_month=_positive_int(f"{prefix}_CAFE_MONTH", default.cafe_month),
        all_day=_positive_int(f"{prefix}_TOTAL_DAY", default.all_day),
        all_month=_positive_int(f"{prefix}_TOTAL_MONTH", default.all_month),
    )


def load_settings() -> Settings:
    """Læser miljøet og returnerer en færdig Settings."""
    hosts = os.getenv("ALLOWED_HOSTS") or "localhost,127.0.0.1"
    port = _port(_clean(os.getenv("PORT")))
    settings = Settings(
        port=port,
        database_url=_clean(os.getenv("DATABASE_URL")),
        anthropic_api_key=_clean(os.getenv("ANTHROPIC_API_KEY")),
        google_places_api_key=_clean(os.getenv("GOOGLE_PLACES_API_KEY")),
        claude_model=_clean(os.getenv("CLAUDE_MODEL")) or DEFAULT_CLAUDE_MODEL,
        allowed_hosts=tuple(h.strip() for h in hosts.split(",") if h.strip()),
        enable_docs=os.getenv("ENABLE_DOCS") == "1",
        public_dir=REPO_DIR / "public",
        cookie_secure=os.getenv("COOKIE_SECURE") == "1",
        resend_api_key=_clean(os.getenv("RESEND_API_KEY")),
        mail_from=_clean(os.getenv("MAIL_FROM")) or DEFAULT_MAIL_FROM,
        app_base_url=(_clean(os.getenv("APP_BASE_URL")) or f"http://localhost:{port}").rstrip("/"),
        draft_caps=_caps("CAP_DRAFTS", DEFAULT_DRAFT_CAPS),
        fetch_caps=_caps("CAP_FETCHES", DEFAULT_FETCH_CAPS),
        profile_caps=_caps("CAP_PROFILES", DEFAULT_PROFILE_CAPS),
        ai_paused=os.getenv("AI_PAUSED") == "1",
        admin_email=_clean(os.getenv("ADMIN_EMAIL")),
        claude_price_input_per_mtok=_price("CLAUDE_PRICE_INPUT_PER_MTOK", 3.0),
        claude_price_output_per_mtok=_price("CLAUDE_PRICE_OUTPUT_PER_MTOK", 15.0),
    )
    # COOKIE_SECURE=1 betyder at siden ligger på et rigtigt domæne med HTTPS. Så skal linkene i
    # mails også pege dertil. Ellers ville en kunde få et link til localhost, som ikke virker.
    if settings.cookie_secure and not settings.app_base_url.startswith("https://"):
        raise RuntimeError(
            "COOKIE_SECURE=1 er sat, men APP_BASE_URL er ikke en https-adresse "
            f"(den er {settings.app_base_url!r}). Sæt APP_BASE_URL=https://dit-domæne i .env, "
            "ellers får kunderne mails med et link der ikke virker."
        )
    return settings
