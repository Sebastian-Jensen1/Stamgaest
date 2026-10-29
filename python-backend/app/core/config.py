"""Indstillinger: API-nøgler, database og port.

Alt der står i .env læses her, ét sted. Resten af koden får en færdig `Settings`
i stedet for selv at kigge i .env.
"""

import ipaddress
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
# Modellen der mærker anmeldelser med emner til statistikken. Det er en enkel sortering, så en
# lille, billig model er nok. Kan ændres med CLAUDE_TOPICS_MODEL i .env.
DEFAULT_TOPICS_MODEL = "claude-haiku-5-5"

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
# Mærkning af anmeldelser med emner. Ét kald mærker op til 10 anmeldelser og koster under en øre
# med claude-haiku-5-5 (oktober 2026). Lofterne er pr. kald, ikke pr. anmeldelse.
DEFAULT_TOPICS_CAPS = Caps(cafe_day=30, cafe_month=200, all_day=300, all_month=3000)

# Cloudflares egne IP-adresser, som de står på https://www.cloudflare.com/ips/ (oktober 2026).
# Bruges når TRUSTED_PROXIES indeholder "cloudflare". Listen ændres sjældent, men tjek den ved
# flytningen til en host.
CLOUDFLARE_IP_RANGES = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
)

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
    topics_caps: Caps = DEFAULT_TOPICS_CAPS
    # Modellen der mærker anmeldelser med emner (se DEFAULT_TOPICS_MODEL).
    claude_topics_model: str = DEFAULT_TOPICS_MODEL
    # Nødbremse: True slår udkast fra Claude fra for ALLE caféer.
    ai_paused: bool = False
    # Hvem får en mail, når et loft er ved at blive nået. Mangler den, skrives det kun i loggen.
    admin_email: str | None = None
    # Claudes pris i USD pr. million tokens. Bruges kun til det anslåede beløb i `usage`.
    claude_price_input_per_mtok: float = 3.0
    claude_price_output_per_mtok: float = 15.0
    # Hvilken netværksadresse serveren lytter på. 127.0.0.1 = kun denne maskine. Hos en host
    # skal den være 0.0.0.0, så hostens proxy kan nå den.
    host: str = "127.0.0.1"
    # Proxyer vi stoler på til at fortælle kundens rigtige IP (X-Forwarded-For). Kun kald der
    # kommer direkte fra en af dem, får lov at ændre IP'en. Alle andre kunne skrive hvad som helst.
    trusted_proxies: tuple[str, ...] = ("127.0.0.1",)


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


def _host(raw: str | None) -> str:
    """Læser HOST. Skal være en IP-adresse, fx 127.0.0.1 (kun denne maskine) eller 0.0.0.0 (alle)."""
    host = raw or "127.0.0.1"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise RuntimeError(f"HOST skal være en IP-adresse, fx 127.0.0.1 eller 0.0.0.0, ikke {host!r}.") from None
    return host


def _is_loopback(host: str) -> bool:
    """Svarer på om adressen kun kan nås fra denne maskine (127.x.x.x eller ::1)."""
    return ipaddress.ip_address(host).is_loopback


def _trusted_proxies(raw: str | None) -> tuple[str, ...]:
    """Læser TRUSTED_PROXIES: IP-adresser og net (fx 10.0.0.0/8), adskilt af komma.

    Ordet "cloudflare" tilføjer Cloudflares net. En ugyldig værdi får serveren til at stoppe,
    for en stavefejl her ville ellers stille og roligt slå grænsen for gætte-forsøg fra.
    """
    entries = [e.strip() for e in (raw or "127.0.0.1").split(",") if e.strip()]
    result: list[str] = []
    for entry in entries:
        if entry.lower() == "cloudflare":
            result.extend(CLOUDFLARE_IP_RANGES)
            continue
        # "*" ville betyde "stol på alle", og så kunne enhver selv vælge sin IP.
        if entry == "*":
            raise RuntimeError("TRUSTED_PROXIES må ikke være *. Skriv proxyernes adresser eller net.")
        try:
            if "/" in entry:
                ipaddress.ip_network(entry)
            else:
                ipaddress.ip_address(entry)
        except ValueError:
            raise RuntimeError(
                f"TRUSTED_PROXIES indeholder {entry!r}, som ikke er en IP-adresse eller et net "
                "(fx 10.0.0.0/8)."
            ) from None
        result.append(entry)
    return tuple(result)


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
        topics_caps=_caps("CAP_TOPICS", DEFAULT_TOPICS_CAPS),
        claude_topics_model=_clean(os.getenv("CLAUDE_TOPICS_MODEL")) or DEFAULT_TOPICS_MODEL,
        ai_paused=os.getenv("AI_PAUSED") == "1",
        admin_email=_clean(os.getenv("ADMIN_EMAIL")),
        claude_price_input_per_mtok=_price("CLAUDE_PRICE_INPUT_PER_MTOK", 3.0),
        claude_price_output_per_mtok=_price("CLAUDE_PRICE_OUTPUT_PER_MTOK", 15.0),
        host=_host(_clean(os.getenv("HOST"))),
        trusted_proxies=_trusted_proxies(_clean(os.getenv("TRUSTED_PROXIES"))),
    )
    # Lytter serveren på netværket, skal login ske over HTTPS. Ellers kan adgangskoder og
    # login-cookies læses af alle på vejen. COOKIE_SECURE=1 er tegnet på at der er HTTPS foran.
    if not _is_loopback(settings.host) and not settings.cookie_secure:
        raise RuntimeError(
            f"HOST={settings.host} gør serveren synlig på netværket, men COOKIE_SECURE=1 er ikke sat. "
            "Brug kun en anden HOST end 127.0.0.1 bag HTTPS (hos hosten), og sæt COOKIE_SECURE=1 og "
            "APP_BASE_URL=https://dit-domæne."
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
