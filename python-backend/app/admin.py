"""Værktøj til OS (ikke til kunderne): opret og administrér kunder.

Kør fra python-backend/:

    uv run python -m app.admin search "Café Gotland København"
    uv run python -m app.admin create-customer --email ejer@gotland.dk --place-id ChIJ...
    uv run python -m app.admin list-customers
    uv run python -m app.admin reset-password --email ejer@gotland.dk
    uv run python -m app.admin deactivate --email ejer@gotland.dk
    uv run python -m app.admin stats --days 30

Der er bevidst ingen admin-side på nettet. Kun den der har adgang til serverens .env kan oprette
kunder, så der ikke er en indgang at angribe udefra. Adgangskoder skrives aldrig på kommandolinjen
(de ville ende i terminalens historik), men laves tilfældigt og vises én gang.
"""

import argparse
import asyncio
import sys
from uuid import UUID

import httpx
import psycopg
from email_validator import EmailNotValidError, validate_email

from app.core.config import Settings, load_settings
from app.db import pool
from app.models import replies, restaurants, reviews, sessions, users
from app.services import auth_service, edit_metrics, places_service


class CliError(Exception):
    """En fejl med en tekst der er skrevet til den der kører værktøjet."""


# ---- Selve arbejdet. Kaldes både af kommandoerne nedenfor og af testene. ----------


async def create_customer(
    *,
    email: str,
    restaurant_id: UUID | None = None,
    place_id: str | None = None,
    business_type: str | None = None,
    http: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> tuple[dict, dict, str]:
    """Opretter en kunde og kobler dem til en café. Returnerer (bruger, café, adgangskode).

    Caféen angives på ÉN af to måder: `restaurant_id` (den findes allerede i databasen) eller
    `place_id` (Googles id, så hentes stedet og dets anmeldelser og gemmes med det samme).
    """
    if (restaurant_id is None) == (place_id is None):
        raise CliError("Angiv præcis én af --restaurant-id og --place-id.")

    try:
        clean_email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise CliError(f"Ugyldig e-mail: {exc}") from None

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
        )
        await reviews.upsert_many(restaurant["id"], details.reviews)
        await restaurants.claim_refresh(restaurant["id"], 0)  # noterer at anmeldelserne er hentet nu
    else:
        restaurant = await restaurants.find_by_id(restaurant_id)
        if restaurant is None:
            raise CliError("Der findes ingen café med det id. Se dem med 'list-restaurants'.")
        if business_type:
            await restaurants.update_business_type(restaurant["id"], business_type)

    password = auth_service.generate_password()
    try:
        user = await users.create(
            email=clean_email,
            password_hash=await auth_service.hash_password(password),
            restaurant_id=restaurant["id"],
        )
    except psycopg.errors.UniqueViolation:
        raise CliError(f"Der findes allerede en bruger med e-mailen {clean_email}.") from None
    return user, restaurant, password


async def reset_password(email: str) -> str:
    """Laver en ny tilfældig adgangskode til en bruger og logger dem ud alle steder. Returnerer den."""
    user = await users.find_by_email(auth_service.normalize_email(email))
    if user is None:
        raise CliError(f"Der findes ingen bruger med e-mailen {email}.")
    password = auth_service.generate_password()
    await users.update_password(user["id"], await auth_service.hash_password(password))
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


async def _create_customer(settings: Settings, args: argparse.Namespace) -> None:
    """Kommandoen `create-customer`."""
    async with httpx.AsyncClient(timeout=15.0) as http:
        user, restaurant, password = await create_customer(
            email=args.email,
            restaurant_id=UUID(args.restaurant_id) if args.restaurant_id else None,
            place_id=args.place_id,
            business_type=args.business_type,
            http=http,
            api_key=settings.google_places_api_key,
        )
    print(f"Kunde oprettet for {restaurant['name']}.")
    print(f"   E-mail:      {user['email']}")
    print(f"   Adgangskode: {password}")
    print("\nAdgangskoden vises KUN denne ene gang. Giv den til kunden, og bed dem skifte den ved første login.")


async def _list_customers() -> None:
    """Kommandoen `list-customers`."""
    rows = await users.list_with_restaurants()
    if not rows:
        print("Ingen kunder endnu.")
    for r in rows:
        status = "aktiv" if r["is_active"] else "SLÅET FRA"
        last = r["last_login_at"].strftime("%Y-%m-%d %H:%M") if r["last_login_at"] else "aldrig"
        print(f"{r['email']:35} {status:10} {r['restaurant_name']}  (sidst logget ind: {last})")


async def _list_restaurants() -> None:
    """Kommandoen `list-restaurants`."""
    for r in await restaurants.list_all():
        print(f"{r['id']}  {r['name']}  [{r['user_count']} bruger(e)]  place-id: {r['google_place_id'] or '-'}")


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

    commands.add_parser("list-customers", help="vis alle kunder")
    commands.add_parser("list-restaurants", help="vis alle caféer med id")

    reset = commands.add_parser("reset-password", help="lav en ny adgangskode til en kunde")
    reset.add_argument("--email", required=True)

    for name, text in (("deactivate", "slå en kunde fra"), ("activate", "slå en kunde til igen")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--email", required=True)

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
