"""Testsættet: sammenligner udkast fra to prompter side om side, så vi kan se, om en ændring
faktisk gør svarene bedre, FØR en kunde ser dem.

Kør fra python-backend/:

    uv run python -m evals.compare                          # gammel prompt mod ny prompt
    uv run python -m evals.compare --restaurant-id <id>     # med en rigtig cafés profil og anmeldelser
    uv run python -m evals.compare --compare-model <model>  # samme nye prompt, to forskellige modeller

Det kalder Claude rigtigt, så det koster lidt. Prisen vises, og du skal sige ja, før noget sendes.
Resultatet er en HTML-side i evals/out/ (den er i .gitignore, for den kan indeholde rigtige
anmeldelser). Åbn den i browseren, og læs svarene igennem med "tjek"-kolonnen ved siden af.
"""

import argparse
import asyncio
import html
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import anthropic

from app.core.config import Settings, load_settings
from app.core.errors import UpstreamError
from app.services import claude_service
from evals import legacy_prompt

EVALS_DIR = Path(__file__).parent
OUT_DIR = EVALS_DIR / "out"
CASES_FILE = EVALS_DIR / "cases.json"

# Så mange kald til Claude ad gangen. Lavt, så vi ikke rammer Claudes grænser.
CONCURRENCY = 4
# Groft overslag pr. kald, kun til prisen før start (den rigtige pris tælles bagefter).
ESTIMATED_INPUT_TOKENS = 2000
ESTIMATED_OUTPUT_TOKENS = 250


@dataclass(frozen=True)
class Case:
    """Én anmeldelse i testsættet og det, et godt svar skal (eller ikke må) gøre."""

    id: str
    rating: int | None
    reviewer: str
    text: str
    check: str


@dataclass(frozen=True)
class Result:
    """Ét svar fra Claude, eller en fejltekst, og hvad det kostede."""

    text: str
    error: bool
    input_tokens: int
    output_tokens: int


def load_cases() -> tuple[dict, list[Case]]:
    """Læser det opdigtede sted og de svære anmeldelser fra cases.json."""
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    cases = [
        Case(id=c["id"], rating=c["rating"], reviewer=c["reviewer"], text=c["text"], check=c["tjek"])
        for c in data["cases"]
    ]
    return data["restaurant"], cases


async def load_restaurant(settings: Settings, restaurant_id: UUID) -> tuple[dict, list[Case]]:
    """Henter en rigtig cafés profil og anmeldelser fra databasen (kun læsning)."""
    from app.db import pool
    from app.models import reply_settings, restaurants, reviews
    from app.services import settings_service

    if not settings.database_url:
        raise SystemExit("DATABASE_URL mangler i .env.")
    await pool.init_pool(pool.build_conninfo(settings.database_url))
    try:
        restaurant = await restaurants.find_by_id(restaurant_id)
        if restaurant is None:
            raise SystemExit("Der findes ingen café med det id. Se dem med 'uv run python -m app.admin list-restaurants'.")
        profile = await settings_service.load(restaurant_id)
        recent = await reply_settings.recent_sent_replies(restaurant_id, claude_service.MAX_RECENT_REPLIES)
        rows = await reviews.list_by_restaurant(restaurant_id)
    finally:
        await pool.close_pool()

    style = {name: profile[name] for name in ("reply_length", "tone", "signature", "avoid", "contact", "examples", "about")}
    style["recent_replies"] = recent
    real_cases = [
        Case(
            id=f"rigtig-{i}",
            rating=row["rating"],
            reviewer=row["reviewer_name"] or "",
            text=row["review_text"] or "",
            check="Rigtig anmeldelse fra databasen. Lyder svaret som caféen, og er det konkret?",
        )
        for i, row in enumerate(rows, 1)
    ]
    return {"name": restaurant["name"], "business_type": restaurant["business_type"] or "", "style": style}, real_cases


def to_style(raw: dict) -> claude_service.ReplyStyle:
    """Laver profilen fra cases.json (eller databasen) om til en ReplyStyle, renset som i appen."""
    from app.services import settings_service

    settings = {**settings_service.DEFAULTS, **raw}
    return settings_service.to_style(settings, raw.get("recent_replies") or ())


async def draft_new(client, *, model: str, restaurant: dict, style, case: Case) -> Result:
    """Udkast med den NUVÆRENDE prompt og caféens profil (præcis som appen laver dem)."""
    try:
        draft = await claude_service.draft_review_reply(
            client,
            model=model,
            business_name=restaurant["name"],
            business_type=restaurant["business_type"],
            reviewer_name=case.reviewer,
            rating=case.rating,
            review_text=case.text,
            style=style,
        )
    except UpstreamError as exc:
        return Result(text=exc.message, error=True, input_tokens=0, output_tokens=0)
    return Result(text=draft.text, error=False, input_tokens=draft.input_tokens or 0, output_tokens=draft.output_tokens or 0)


async def draft_legacy(client, *, model: str, restaurant: dict, case: Case, tone: str) -> Result:
    """Udkast med den GAMLE prompt (uden profil), til sammenligning."""
    try:
        message = await claude_service.create_message(
            client,
            model=model,
            max_tokens=legacy_prompt.MAX_TOKENS,
            system=legacy_prompt.SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": legacy_prompt.build_prompt(
                    business_name=restaurant["name"], business_type=restaurant["business_type"],
                    reviewer_name=case.reviewer, rating=case.rating, review_text=case.text, tone=tone,
                ),
            }],
        )
    except UpstreamError as exc:
        return Result(text=exc.message, error=True, input_tokens=0, output_tokens=0)
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()
    used_in, used_out = claude_service.usage_tokens(message)
    return Result(text=text, error=False, input_tokens=used_in or 0, output_tokens=used_out or 0)


def cost_usd(settings: Settings, input_tokens: int, output_tokens: int) -> float:
    """Pris i USD ud fra priserne i .env."""
    return (input_tokens * settings.claude_price_input_per_mtok + output_tokens * settings.claude_price_output_per_mtok) / 1_000_000


def render_html(*, title: str, label_a: str, label_b: str, cases: list[Case], results: list[tuple[Result, Result]], total: str) -> str:
    """Bygger rapporten. Alt fra Claude og anmeldelserne escapes, så intet kan køre som kode i siden."""
    e = html.escape
    rows = []
    for case, (a, b) in zip(cases, results):
        def cell(result: Result) -> str:
            css = ' class="fejl"' if result.error else ""
            return f"<td{css}>{e(result.text)}</td>"

        stars = f"{case.rating}/5" if case.rating else "?/5"
        rows.append(
            "<tr>"
            f"<td><b>{e(case.id)}</b><br><span class=\"lille\">{e(stars)} · {e(case.reviewer)}</span>"
            f"<p>{e(case.text) or '<i>(ingen tekst)</i>'}</p><p class=\"tjek\">Tjek: {e(case.check)}</p></td>"
            f"{cell(a)}{cell(b)}</tr>"
        )
    return f"""<!doctype html>
<html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1c1917; background: #fff; }}
  table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
  th, td {{ border: 1px solid #e5e7eb; padding: 10px; vertical-align: top; white-space: pre-wrap; font-size: 14px; }}
  th {{ background: #f5f5f4; text-align: left; }}
  .lille {{ color: #78716c; font-size: 12px; }}
  .tjek {{ color: #9a3412; font-size: 13px; }}
  .fejl {{ color: #b91c1c; }}
</style></head><body>
<h1>{e(title)}</h1>
<p class="lille">{e(total)}</p>
<table><thead><tr><th>Anmeldelse og tjek</th><th>{e(label_a)}</th><th>{e(label_b)}</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>
"""


async def run(args: argparse.Namespace) -> int:
    """Kører testsættet og skriver rapporten. Returnerer 0 ved succes."""
    settings = load_settings()
    if not settings.anthropic_api_key:
        print("Fejl: ANTHROPIC_API_KEY mangler i .env.", file=sys.stderr)
        return 1

    restaurant, cases = load_cases()
    if args.restaurant_id:
        restaurant, real_cases = await load_restaurant(settings, UUID(args.restaurant_id))
        cases = real_cases + cases  # de rigtige først, så de svære tilfælde med cafeens profil
    style = to_style(restaurant["style"])

    model_b = settings.claude_model
    if args.compare_model:
        label_a, label_b = f"{args.compare_model} (ny prompt)", f"{model_b} (ny prompt)"
    else:
        label_a, label_b = f"Før: gammel prompt ({model_b})", f"Nu: ny prompt med profil ({model_b})"

    calls = len(cases) * 2
    estimate = cost_usd(settings, calls * ESTIMATED_INPUT_TOKENS, calls * ESTIMATED_OUTPUT_TOKENS)
    print(f"{len(cases)} anmeldelser x 2 = {calls} kald til Claude. Anslået pris: ca. ${estimate:.2f}.")
    if not args.yes:
        try:
            answer = input("Fortsæt? [j/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("j", "ja", "y", "yes"):
            print("Afbrudt. Der er ikke sendt noget.")
            return 1

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=60.0, max_retries=2)
    gate = asyncio.Semaphore(CONCURRENCY)

    async def column_a(case: Case) -> Result:
        async with gate:
            if args.compare_model:
                return await draft_new(client, model=args.compare_model, restaurant=restaurant, style=style, case=case)
            return await draft_legacy(client, model=model_b, restaurant=restaurant, case=case, tone=args.tone)

    async def column_b(case: Case) -> Result:
        async with gate:
            return await draft_new(client, model=model_b, restaurant=restaurant, style=style, case=case)

    try:
        a_results = await asyncio.gather(*(column_a(c) for c in cases))
        b_results = await asyncio.gather(*(column_b(c) for c in cases))
    finally:
        await client.close()

    used_in = sum(r.input_tokens for r in a_results + b_results)
    used_out = sum(r.output_tokens for r in a_results + b_results)
    errors = sum(r.error for r in a_results + b_results)
    total = f"{len(cases)} anmeldelser · {used_in} tokens ind · {used_out} tokens ud · ca. ${cost_usd(settings, used_in, used_out):.2f} · {errors} fejl · {datetime.now():%d.%m.%Y %H:%M}"

    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"rapport-{datetime.now():%Y%m%d-%H%M%S}.html"
    path.write_text(
        render_html(
            title=f"Testsæt: {restaurant['name']}", label_a=label_a, label_b=label_b,
            cases=cases, results=list(zip(a_results, b_results)), total=total,
        ),
        encoding="utf-8",
    )
    print(total)
    print(f"Rapport: {path}")
    return 0


def main() -> int:
    """Kommandolinjens indgang."""
    parser = argparse.ArgumentParser(prog="python -m evals.compare", description=__doc__.split("\n\n")[0])
    parser.add_argument("--restaurant-id", help="brug en rigtig cafés profil og anmeldelser (fra 'list-restaurants')")
    parser.add_argument("--compare-model", help="sammenlign to modeller med den nye prompt i stedet for gammel/ny prompt")
    parser.add_argument("--tone", default="venligt og professionelt", help="tonen til den GAMLE prompt (den nye bruger profilens tone)")
    parser.add_argument("--yes", action="store_true", help="spring spørgsmålet om prisen over")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
