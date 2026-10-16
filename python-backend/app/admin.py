"""Værktøj til OS (ikke til kunderne): opret og administrér kunder.

Kør fra python-backend/:

    uv run python -m app.admin search "Café Gotland København"
    uv run python -m app.admin create-customer --email ejer@gotland.dk --place-id ChIJ...
    uv run python -m app.admin invite --email ejer@gotland.dk
    uv run python -m app.admin revoke-invite --email ejer@gotland.dk
    uv run python -m app.admin list-customers
    uv run python -m app.admin reset-password --email ejer@gotland.dk
    uv run python -m app.admin deactivate --email ejer@gotland.dk
    uv run python -m app.admin stats --days 30
    uv run python -m app.admin usage --days 30
    uv run python -m app.admin ai-off --restaurant-id <id>

Der er bevidst ingen admin-side på nettet. Kun den der har adgang til serverens .env kan oprette
kunder, så der ikke er en indgang at angribe udefra. Adgangskoder skrives aldrig på kommandolinjen
(de ville ende i terminalens historik). Nye kunder får en invitation på mail og vælger selv deres
adgangskode, så vi aldrig ser den.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from uuid import UUID

import httpx
import psycopg
from email_validator import EmailNotValidError, validate_email

from app.core.config import Settings, load_settings
from app.db import pool
from app.models import password_tokens, replies, restaurants, reviews, sessions, usage, users
from app.services import auth_service, edit_metrics, email_service, places_service


class CliError(Exception):
    """En fejl med en tekst der er skrevet til den der kører værktøjet."""


# ---- Selve arbejdet. Kaldes både af kommandoerne nedenfor og af testene. ----------


def clean_email(email: str) -> str:
    """Tjekker en e-mail og gør den ens (små bogstaver). Fejler med en tydelig tekst hvis den er ugyldig."""
    try:
        return validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise CliError(f"Ugyldig e-mail: {exc}") from None


def email_is_confirmed(expected: str, typed: str) -> bool:
    """Er den indtastede e-mail den samme som den vi skal sende til? (Store/små bogstaver er ligegyldige.)"""
    return typed.strip().lower() == expected.strip().lower()


async def create_customer(
    *,
    email: str,
    restaurant_id: UUID | None = None,
    place_id: str | None = None,
    business_type: str | None = None,
    http: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    invite_pending: bool = False,
) -> tuple[dict, dict, str]:
    """Opretter en kunde og kobler dem til en café. Returnerer (bruger, café, adgangskode).

    Med `invite_pending` er kunden inviteret og vælger selv sin adgangskode via et link. Den
    adgangskode der returneres, må så ikke bruges til noget: den er tilfældig og vises ikke.

    Caféen angives på ÉN af to måder: `restaurant_id` (den findes allerede i databasen) eller
    `place_id` (Googles id, så hentes stedet og dets anmeldelser og gemmes med det samme).
    """
    if (restaurant_id is None) == (place_id is None):
        raise CliError("Angiv præcis én af --restaurant-id og --place-id.")

    email = clean_email(email)

    if place_id is not None:
        if not places_service.is_valid_place_id(place_id):
            raise CliError("Ugyldigt place-id. Det ser ud som ChIJ... Find det med 'search'.")
        if http is None or not api_key:
            raise CliError("Mangler GOOGLE_PLACES_API_KEY i .env, så stedet kan hentes fra Google.")
        details = await places_service.get_place_details(http, place_id, api_key)
        restaurant = await restaurants.upsert_by_place_id(
            google_place_id=place_id,
            name=details.business_name or place_id,
            business_type=business_type,
            address=details.address,
            google_rating=details.rating,
            google_rating_count=details.user_rating_count,
            website_url=details.website,
        )
        await reviews.upsert_many(restaurant["id"], details.reviews)
        await restaurants.claim_refresh(restaurant["id"], 0)  # noterer at anmeldelserne er hentet nu
        # Hentningen kostede et Google-kald, så den skal med i regnskabet (men tjekkes ikke mod
        # lofterne: det er os selv der opretter kunden).
        await usage.record_done(kind=usage.FETCH, restaurant_id=restaurant["id"])
    else:
        restaurant = await restaurants.find_by_id(restaurant_id)
        if restaurant is None:
            raise CliError("Der findes ingen café med det id. Se dem med 'list-restaurants'.")
        if business_type:
            await restaurants.update_business_type(restaurant["id"], business_type)

    password = auth_service.generate_password()
    try:
        user = await users.create(
            email=email,
            password_hash=await auth_service.hash_password(password),
            restaurant_id=restaurant["id"],
            invite_pending=invite_pending,
        )
    except psycopg.errors.UniqueViolation:
        raise CliError(f"Der findes allerede en bruger med e-mailen {email}.") from None
    return user, restaurant, password


async def send_invitation(*, email: str, http: httpx.AsyncClient, settings: Settings) -> dict:
    """Sender (eller gensender) velkomstmailen til en kunde der ikke har valgt adgangskode endnu.

    En ny invitation gør den forrige ubrugelig. Returnerer brugeren.
    """
    user = await users.find_by_email(auth_service.normalize_email(email))
    if user is None:
        raise CliError(f"Der findes ingen bruger med e-mailen {email}.")
    if not user["is_active"]:
        raise CliError("Kunden er slået fra. Slå dem til igen med 'activate' først.")
    if not user["invite_pending"]:
        raise CliError(
            "Kunden har allerede valgt en adgangskode. De kan selv bruge 'Glemt adgangskode?' på "
            "loginsiden, eller du kan bruge 'reset-password'."
        )
    restaurant = await restaurants.find_by_id(user["restaurant_id"])

    token = auth_service.new_reset_token()
    await password_tokens.create(
        token_hash=auth_service.hash_token(token),
        user_id=user["id"],
        expires_at=datetime.now(timezone.utc) + auth_service.INVITE_LIFETIME,
    )
    sent = await email_service.send_invitation(
        http,
        settings,
        to=user["email"],
        token=token,
        business_name=restaurant["name"] if restaurant else "din café",
        days=auth_service.INVITE_LIFETIME.days,
    )
    if not sent:
        await password_tokens.delete_for_user(user["id"])  # linket nåede aldrig frem, så det bliver ikke liggende
        raise CliError(
            "Mailen kunne ikke sendes. Tjek RESEND_API_KEY og at afsenderdomænet er bekræftet hos Resend "
            "(se serverens log). Prøv igen med 'invite'."
        )
    return user


async def revoke_invite(email: str) -> None:
    """Trækker en invitation tilbage, så linket i mailen ikke virker mere.

    Kunden kan ikke logge ind og ikke bruge "Glemt adgangskode?", før vi sender en ny invitation.
    """
    user = await users.find_by_email(auth_service.normalize_email(email))
    if user is None:
        raise CliError(f"Der findes ingen bruger med e-mailen {email}.")
    if not user["invite_pending"]:
        raise CliError("Kunden har allerede valgt en adgangskode, så der er ingen invitation at trække tilbage.")
    await password_tokens.delete_for_user(user["id"])


async def reset_password(email: str) -> str:
    """Laver en ny tilfældig adgangskode til en bruger og logger dem ud alle steder. Returnerer den."""
    user = await users.find_by_email(auth_service.normalize_email(email))
    if user is None:
        raise CliError(f"Der findes ingen bruger med e-mailen {email}.")
    password = auth_service.generate_password()
    await users.update_password(user["id"], await auth_service.hash_password(password))
    await users.set_invite_pending(user["id"], False)  # kunden har nu en adgangskode
    await password_tokens.delete_for_user(user["id"])  # et gammelt link må ikke overskrive den
    await sessions.delete_for_user(user["id"])
    return password


async def set_active(email: str, active: bool) -> None:
    """Slår en bruger til eller fra. Ved fra logges de også ud med det samme."""
    user = await users.find_by_email(auth_service.normalize_email(email))
    if user is None:
        raise CliError(f"Der findes ingen bruger med e-mailen {email}.")
    await users.set_active(user["id"], active)
    if not active:
        await sessions.delete_for_user(user["id"])


async def edit_report(*, restaurant_id: UUID | None = None, days: int | None = None) -> dict:
    """Regner ud hvor meget der rettes i Claudes udkast. Kun til os, aldrig vist til kunderne.

    Returnerer det samlede billede, det samme opdelt pr. café og pr. model, og alle
    svarene med deres rettelse (mest rettede først), så man kan læse eksemplerne.
    """
    if days is not None and days < 1:
        raise CliError("--days skal være mindst 1.")
    rows = await replies.list_sent_from_claude(restaurant_id=restaurant_id, days=days)
    for row in rows:
        row["edit_ratio"] = edit_metrics.edit_ratio(row["draft_text"], row["final_text"])

    def grouped(key: str) -> dict[str, edit_metrics.EditSummary]:
        groups: dict[str, list[float]] = {}
        for row in rows:
            groups.setdefault(str(row[key]), []).append(row["edit_ratio"])
        return {name: edit_metrics.summarize(ratios) for name, ratios in groups.items()}

    return {
        "overall": edit_metrics.summarize([row["edit_ratio"] for row in rows]),
        "by_restaurant": grouped("restaurant_name"),
        "by_model": grouped("model"),
        "drafts_per_reply": sum(row["draft_count"] for row in rows) / len(rows) if rows else 0.0,
        "replies": sorted(rows, key=lambda row: row["edit_ratio"], reverse=True),
    }


# ---- Kommandolinjen ---------------------------------------------------------------


async def _search(settings: Settings, query: str) -> None:
    """Kommandoen `search`: finder sted-id'er hos Google, så man kan koble en kunde til det rigtige."""
    if not settings.google_places_api_key:
        raise CliError("Mangler GOOGLE_PLACES_API_KEY i .env.")
    async with httpx.AsyncClient(timeout=15.0) as http:
        places = await places_service.search_places(http, query, settings.google_places_api_key)
    if not places:
        print("Ingen resultater. Prøv et mere præcist navn eller tilføj byen.")
        return
    for place in places:
        print(f"{place.name}\n   {place.address}\n   place-id: {place.place_id}\n")


def _confirm_email_interactively(email: str) -> None:
    """Beder om at få e-mailen skrevet igen, så en tastefejl ikke sender invitationen til en fremmed."""
    print(f"Invitationen sendes til:  {email}")
    try:
        typed = input("Skriv e-mailadressen igen for at bekræfte: ")
    except EOFError:
        raise CliError("Kan ikke bekræfte e-mailen uden en terminal. Brug --yes, hvis du er sikker.") from None
    if not email_is_confirmed(email, typed):
        raise CliError("E-mailadresserne er ikke ens. Der er ikke oprettet noget.")


async def _create_customer(settings: Settings, args: argparse.Namespace) -> None:
    """Kommandoen `create-customer`: opretter kunden og sender en invitation (eller viser en adgangskode)."""
    email = clean_email(args.email)
    # Bekræftelsen sker FØR noget oprettes eller hentes fra Google. Den springes over, når der ikke
    # sendes en mail (--show-password), eller når man bevidst har sagt --yes.
    if not args.show_password and not args.yes:
        _confirm_email_interactively(email)

    async with httpx.AsyncClient(timeout=15.0) as http:
        user, restaurant, password = await create_customer(
            email=email,
            restaurant_id=UUID(args.restaurant_id) if args.restaurant_id else None,
            place_id=args.place_id,
            business_type=args.business_type,
            http=http,
            api_key=settings.google_places_api_key,
            invite_pending=not args.show_password,
        )
        print(f"Kunde oprettet for {restaurant['name']}.")
        print(f"   E-mail:      {user['email']}")
        if args.show_password:
            print(f"   Adgangskode: {password}")
            print("\nAdgangskoden vises KUN denne ene gang. Giv den til kunden, og bed dem skifte den ved første login.")
            return
        _warn_if_localhost(settings)
        await send_invitation(email=user["email"], http=http, settings=settings)
    days = auth_service.INVITE_LIFETIME.days
    print(f"\nInvitation sendt. Linket virker i {days} dage. Send en ny med 'invite', eller træk den tilbage med 'revoke-invite'.")


def _warn_if_localhost(settings: Settings) -> None:
    """Advarer hvis linket i mailen peger på din egen computer, for så virker det ikke for kunden."""
    if settings.app_base_url.startswith("http://localhost") or settings.app_base_url.startswith("http://127."):
        print(f"ADVARSEL: linket i mailen peger på {settings.app_base_url}, som kun virker på din egen computer.")
        print("          Sæt APP_BASE_URL i .env til den adresse kunden skal bruge.\n")


async def _invite(settings: Settings, args: argparse.Namespace) -> None:
    """Kommandoen `invite`: sender en (ny) velkomstmail."""
    _warn_if_localhost(settings)
    async with httpx.AsyncClient(timeout=15.0) as http:
        user = await send_invitation(email=args.email, http=http, settings=settings)
    print(f"Invitation sendt til {user['email']}. Tidligere links er ugyldige. Det nye virker i {auth_service.INVITE_LIFETIME.days} dage.")


def customer_status(row: dict) -> str:
    """Giver kundens status som tekst: aktiv, slået fra, eller hvor langt invitationen er."""
    if not row["is_active"]:
        return "SLÅET FRA"
    if row["invite_pending"]:
        return "afventer invitation" if row["has_valid_link"] else "INVITATION UDLØBET"
    return "aktiv"


async def _list_customers() -> None:
    """Kommandoen `list-customers`."""
    rows = await users.list_with_restaurants()
    if not rows:
        print("Ingen kunder endnu.")
    for r in rows:
        last = r["last_login_at"].strftime("%Y-%m-%d %H:%M") if r["last_login_at"] else "aldrig"
        print(f"{r['email']:35} {customer_status(r):20} {r['restaurant_name']}  (sidst logget ind: {last})")


async def _list_restaurants() -> None:
    """Kommandoen `list-restaurants`."""
    for r in await restaurants.list_all():
        flag = "" if r["ai_enabled"] else "  [udkast slået fra]"
        print(f"{r['id']}  {r['name']}  [{r['user_count']} bruger(e)]  place-id: {r['google_place_id'] or '-'}{flag}")


async def set_ai_enabled(restaurant_id: UUID, enabled: bool) -> None:
    """Slår udkast fra Claude til eller fra for én café (nødbremse)."""
    if not await restaurants.set_ai_enabled(restaurant_id, enabled):
        raise CliError("Der findes ingen café med det id. Se dem med 'list-restaurants'.")


def _caps_line(name: str, used: dict[str, int], caps) -> str:
    """Én linje med forbrug i forhold til lofterne for hele Stamgæst."""
    return (
        f"  {name:24} i dag {used['all_day']:>6} af {caps.all_day:<7}"
        f"denne måned {used['all_month']:>7} af {caps.all_month}"
    )


async def _usage(settings: Settings, args: argparse.Namespace) -> None:
    """Kommandoen `usage`: forbrug pr. café, i alt, og i forhold til lofterne."""
    if args.days < 1:
        raise CliError("--days skal være mindst 1.")
    rows = await usage.summary(args.days)
    price_in = settings.claude_price_input_per_mtok
    price_out = settings.claude_price_output_per_mtok

    def dollars(row: dict) -> float:
        tokens_in = row["input_tokens"] + row["profile_input_tokens"]
        tokens_out = row["output_tokens"] + row["profile_output_tokens"]
        return (tokens_in * price_in + tokens_out * price_out) / 1_000_000

    if settings.ai_paused:
        print("BEMÆRK: AI_PAUSED=1 er sat, så udkast er slået fra for ALLE.\n")

    print("Lige nu (hele Stamgæst, dansk tid)")
    print(_caps_line("Udkast fra Claude", await usage.current(usage.DRAFT), settings.draft_caps))
    print(_caps_line("Hentninger fra Google", await usage.current(usage.FETCH), settings.fetch_caps))
    print(_caps_line("Profiler fra hjemmesider", await usage.current(usage.PROFILE), settings.profile_caps))

    print(f"\nPr. café, de sidste {args.days} dage")
    if not rows:
        print("  Intet forbrug endnu.")
        return
    print(f"  {'Café':30} {'Udkast':>7} {'Tokens ind':>11} {'Tokens ud':>10} {'Anslået':>9} {'Hentninger':>11} {'Profiler':>9}")
    for row in rows:
        flag = "" if row["ai_enabled"] in (True, None) else "   [udkast slået fra]"
        print(
            f"  {row['restaurant_name'][:30]:30} {row['drafts']:>7} {row['input_tokens']:>11} "
            f"{row['output_tokens']:>10} {'$' + format(dollars(row), '.2f'):>9} {row['fetches']:>11} {row['profiles']:>9}{flag}"
        )
    keys = ("drafts", "input_tokens", "output_tokens", "fetches", "profiles", "profile_input_tokens", "profile_output_tokens")
    total = {key: sum(row[key] for row in rows) for key in keys}
    print(
        f"  {'I alt':30} {total['drafts']:>7} {total['input_tokens']:>11} {total['output_tokens']:>10} "
        f"{'$' + format(dollars(total), '.2f'):>9} {total['fetches']:>11} {total['profiles']:>9}"
    )
    print(
        f"\nTokens-kolonnerne er udkast. Beløbet er Claude i alt (udkast og profiler), anslået ud fra {price_in:g}/{price_out:g} USD pr. million tokens ind/ud "
        "(CLAUDE_PRICE_*_PER_MTOK i .env). Den rigtige regning står i Anthropic Console. "
        "Google-prisen står i Google Cloud Console."
    )


def _pct(value: float) -> str:
    """0.345 -> "35 %"."""
    return f"{round(value * 100)} %"


async def _stats(args: argparse.Namespace) -> None:
    """Kommandoen `stats`."""
    report = await edit_report(
        restaurant_id=UUID(args.restaurant_id) if args.restaurant_id else None, days=args.days
    )
    scope = "alle caféer" if not args.restaurant_id else "én café"
    period = f"de sidste {args.days} dage" if args.days else "hele perioden"
    print(f"Rettelser i Claudes udkast ({scope}, {period})\n")

    overall = report["overall"]
    if overall is None:
        print("Ingen sendte svar fra Claude endnu.")
        return

    def share(n: int) -> str:
        return f"{n:>4}   ({_pct(n / overall.count)})"

    print(f"  Sendte svar fra Claude:      {overall.count:>4}")
    print(f"  Gennemsnitlig rettelse:      {_pct(overall.mean):>6}   (median {_pct(overall.median)})")
    print(f"  Sendt uændret:               {share(overall.unchanged)}")
    print(f"  Småretter (under 20 %):      {share(overall.small)}")
    print(f"  Større rettelser (20-60 %):  {share(overall.large)}")
    print(f"  Skrevet om (60 % eller mere):{share(overall.rewritten)}")
    print(f"  Udkast pr. sendt svar:       {format(report['drafts_per_reply'], '.1f').replace('.', ','):>6}   (over 1 = der blev trykket \"Generér igen\")")

    for title, groups in (("Pr. café", report["by_restaurant"]), ("Pr. model", report["by_model"])):
        print(f"\n{title}")
        for name, summary in sorted(groups.items(), key=lambda item: -item[1].count):
            print(f"  {name[:34]:35} {summary.count:>4} svar   {_pct(summary.mean):>6} rettet")

    examples = report["replies"][: args.examples]
    if examples:
        print(f"\nMest rettede ({len(examples)})")
    for row in examples:
        stars = f"{row['rating']}/5" if row["rating"] else "?/5"
        print(f"\n  {_pct(row['edit_ratio'])} rettet · {row['restaurant_name']} · {stars} · tone: {row['tone'] or '-'}")
        print(f"  Anmeldelse: {row['review_text'] or '(ingen tekst)'}")
        print(f"  Udkast:     {row['draft_text']}")
        print(f"  Sendt:      {row['final_text']}")


def _parser() -> argparse.ArgumentParser:
    """Bygger kommandolinjens kommandoer og valgmuligheder."""
    parser = argparse.ArgumentParser(prog="python -m app.admin", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="find en café hos Google (giver place-id)")
    search.add_argument("query")

    create = commands.add_parser("create-customer", help="opret en kunde og kobl dem til en café")
    create.add_argument("--email", required=True)
    create.add_argument("--place-id", help="Googles id for caféen (fra 'search')")
    create.add_argument("--restaurant-id", help="id på en café der allerede findes (fra 'list-restaurants')")
    create.add_argument("--business-type", help='fx "café" (bruges i prompten til Claude)')
    create.add_argument("--yes", action="store_true", help="spring bekræftelsen af e-mailen over")
    create.add_argument(
        "--show-password",
        action="store_true",
        help="send ingen invitation, men vis en adgangskode én gang (til kunder der ikke kan få mail)",
    )

    invite = commands.add_parser("invite", help="send en (ny) velkomstmail til en kunde der ikke har valgt adgangskode")
    invite.add_argument("--email", required=True)

    revoke = commands.add_parser("revoke-invite", help="træk en invitation tilbage, så linket ikke virker mere")
    revoke.add_argument("--email", required=True)

    commands.add_parser("list-customers", help="vis alle kunder")
    commands.add_parser("list-restaurants", help="vis alle caféer med id")

    reset = commands.add_parser("reset-password", help="lav en ny adgangskode til en kunde")
    reset.add_argument("--email", required=True)

    for name, text in (("deactivate", "slå en kunde fra"), ("activate", "slå en kunde til igen")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--email", required=True)

    usage_cmd = commands.add_parser("usage", help="forbrug af Claude og Google pr. café og i forhold til lofterne")
    usage_cmd.add_argument("--days", type=int, default=30, help="hvor mange dage tilbage (standard 30)")

    for name, text in (
        ("ai-off", "slå udkast fra Claude FRA for én café (nødbremse)"),
        ("ai-on", "slå udkast fra Claude til igen for én café"),
    ):
        command = commands.add_parser(name, help=text)
        command.add_argument("--restaurant-id", required=True, help="caféens id (fra 'list-restaurants')")

    stats = commands.add_parser("stats", help="hvor meget retter kunderne i Claudes udkast?")
    stats.add_argument("--days", type=int, help="kun svar sendt de sidste N dage (fx for at sammenligne før/efter en ny prompt)")
    stats.add_argument("--restaurant-id", help="kun én café (fra 'list-restaurants')")
    stats.add_argument("--examples", type=int, default=3, help="hvor mange af de mest rettede svar der vises (standard 3)")
    return parser


async def _run(args: argparse.Namespace, settings: Settings) -> None:
    """Åbner databasen, kører den valgte kommando og lukker igen."""
    if args.command == "search":  # rører ikke databasen
        await _search(settings, args.query)
        return

    if not settings.database_url:
        raise CliError("DATABASE_URL mangler i .env.")
    await pool.init_pool(pool.build_conninfo(settings.database_url))
    try:
        if args.command == "create-customer":
            await _create_customer(settings, args)
        elif args.command == "invite":
            await _invite(settings, args)
        elif args.command == "revoke-invite":
            await revoke_invite(args.email)
            print("Invitationen er trukket tilbage. Send en ny med 'invite', hvis kunden alligevel skal have adgang.")
        elif args.command == "list-customers":
            await _list_customers()
        elif args.command == "list-restaurants":
            await _list_restaurants()
        elif args.command == "reset-password":
            print(f"Ny adgangskode: {await reset_password(args.email)}\n(Vises kun denne ene gang. Kunden er logget ud overalt.)")
        elif args.command in ("deactivate", "activate"):
            await set_active(args.email, args.command == "activate")
            print("Færdig.")
        elif args.command == "stats":
            await _stats(args)
        elif args.command == "usage":
            await _usage(settings, args)
        elif args.command in ("ai-off", "ai-on"):
            await set_ai_enabled(UUID(args.restaurant_id), args.command == "ai-on")
            state = "slået TIL" if args.command == "ai-on" else "slået FRA"
            print(f"Udkast fra Claude er {state} for caféen. Det gælder med det samme.")
    finally:
        await pool.close_pool()


def main() -> int:
    """Kommandolinjens indgang. Returnerer 0 ved succes, 1 ved en fejl."""
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args, load_settings()))
    except (CliError, ValueError) as exc:
        print(f"Fejl: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Kun typen, aldrig en fejltekst der kan indeholde en forbindelsesstreng.
        print(f"Uventet fejl ({type(exc).__name__}). Se serverens log.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
