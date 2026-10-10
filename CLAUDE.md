# CLAUDE.md

Stamgæst: et dashboard hvor en café-ejer logger ind, henter sine Google-anmeldelser, får
AI-udkast til svar fra Claude, retter dem og sender. Hver kunde ser **kun sin egen café**.
README.md er den fulde dokumentation. Denne fil er reglerne for at arbejde i koden.

## Repoet

- `python-backend/`: FastAPI-server (Python 3.14, uv), Postgres på Neon via `psycopg`, rå SQL
- `public/index.html`: hele dashboardet i én fil (inline CSS og JS), serveret af backenden
- `stamgaest-website/`: statisk markedsføringsside (stamgaest.com). Har intet med backenden at gøre

## Kommandoer (kør fra `python-backend/`)

```
uv sync                                   # installer
uv run python -m app                      # start serveren på http://localhost:3000
uv run pytest                             # alle tests (db-tests springes over uden DATABASE_URL)
uv run pytest tests/test_unit.py          # kun tests uden database
uv run python -m app.db.migrate           # kør nye migrationer
uv run python -m app.admin --help         # opret/administrér kunder (kun til os)
```

Kør `uv run pytest` efter hver ændring i backenden, og før du siger at noget er færdigt.

## Arkitektur

Lag, hvor hvert lag kun kalder laget under sig:

```
routers/   HTTP-endpoints under /api. Tynde: validér, hent CurrentUser, kald videre, svar
schemas/   Pydantic-klasser for JSON ind og ud
services/  Logik og eksterne API'er (claude_service, places_service, auth_service)
models/    Al SQL, én fil pr. tabel, funktioner der returnerer dict. Ingen ORM
db/        pool.py (forbindelser), migrate.py, migrations/NNN_navn.sql
core/      config, deps (CurrentUser), security-middleware, errors, rate_limit
```

`main.py` samler appen i `create_app()`. Testene bruger samme funktion med egne indstillinger.
`admin.py` er et CLI der genbruger `models` og `services`. Der er bevidst ingen admin-side på nettet.

## Regler der ikke må brydes

**Adskillelse af kunder**
- Caféen afgøres altid af `CurrentUser` (session-cookien), aldrig af noget browseren sender.
- Hver SQL-query på kundedata filtrerer på `restaurant_id` i selve SQL'en (se `reviews.find_owned`).
  Det er ikke nok at routeren tjekker det.
- Et id fra en anden café giver samme svar som et id der ikke findes ("findes ikke").
- Nye endpoints med kundedata skal have en test i `tests/test_isolation_db.py`.

**Sikkerhed**
- Kun parametriseret SQL. Byg aldrig værdier ind i SQL-teksten.
- Fejl fra Google, Claude og databasen sendes aldrig til browseren. Log dem, og svar med vores egen tekst.
- Anmeldelsestekst hentes fra databasen, ikke fra requesten, og står i tags i prompten som data.
- Frontenden: tekst fra Google, Claude eller brugeren indsættes via `textContent` eller `esc()`,
  aldrig rå `innerHTML`.
- CSP tillader kun det inline script i `index.html` (via hash, beregnet ved opstart). Ingen eksterne
  scripts eller CDN'er. Genstart serveren efter ændringer i scriptet.
- Hemmeligheder kun i `.env`, som aldrig committes. Adgangskoder skrives aldrig på kommandolinjen eller i logs.

**Database**
- Neon-databasen er **fælles** og deles med andre. Kør aldrig destruktiv SQL (DROP, DELETE, TRUNCATE)
  mod den uden at spørge først. Testene kører i et midlertidigt schema og rører ikke de rigtige tabeller.
- Ret aldrig i en migration der allerede er kørt. Lav en ny fil (`003_...sql`).

## Kodestil

- Kommentarer, docstrings, fejltekster og UI-tekst er på **dansk**, i et enkelt sprog uden jargon.
  Forklar *hvorfor*, ikke kun *hvad*. Følg tonen i den eksisterende kode.
- Hver funktion har en kort docstring, der starter med én linje om hvad den gør.
- Hold det enkelt: ingen nye afhængigheder, frameworks eller build-trin uden at spørge først.
- Al I/O er `async`. Eksterne kald har altid en timeout.

## UI

- Følg DESIGN_xxx.md for al UI: farver, typografi, spacing og komponenter.
- Find ikke på nye farver, skrifttyper eller komponenter, der ikke står i DESIGN_xxx.md-filen. Mangler noget, så spørg.

## Arbejdsgang

- Lav altid en ny branch før kodeændringer. Ret aldrig direkte på `main`, og merge ikke før jeg har godkendt.
- Commit eller push kun når jeg beder om det.
- Opdatér README.md når kommandoer, endpoints eller opsætning ændres.
