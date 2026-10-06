# Stamgæst — quick guide

De kommandoer du oftest har brug for. Mere forklaring står i [README.md](README.md).

Alle kommandoer køres fra `python-backend/`, medmindre andet er skrevet:

```
cd python-backend
```

## Første gang du sætter op

```
brew install uv                      # kun hvis du ikke har uv (Mac)
uv sync                              # installerer dependencies
cp .env.example .env                 #kopier, så lav din egen .env ud fra .env.example
```

Åbn `.env` og udfyld:

| Nøgle | Hvad |
| --- | --- |
| `DATABASE_URL` | Forbindelsesstrengen til Neon (står ikke i Git, spørg efter den) |
| `ANTHROPIC_API_KEY` | Nøgle til Claude (console.anthropic.com) |
| `GOOGLE_PLACES_API_KEY` | Nøgle til Google Places |

Opret tabellerne i databasen (sikkert at køre igen):

```
uv run python -m app.db.migrate
```

## Opret en bruger (kunde)

En kunde hører til præcis én café. Man kan ikke tilmelde sig på siden, så brugere oprettes her.

```
# 1. Find caféens Google place-id (starter med ChIJ...)
uv run python -m app.admin search "navn og by" fx: "Café Gotland København"

# 2. Opret kunden. Anmeldelserne hentes med det samme
uv run python -m app.admin create-customer --email ejer@gotland.dk --place-id ChIJ... --business-type café
```

Adgangskoden vises **kun én gang**. Kopiér den med det samme, og giv den til kunden.

Skal en ekstra medarbejder have adgang til en café, der allerede findes:

```
uv run python -m app.admin list-restaurants                      # find café-id
uv run python -m app.admin create-customer --email medarbejder@gotland.dk --restaurant-id <id>
```

## Administrér brugere

```
uv run python -m app.admin list-customers                        # alle kunder
uv run python -m app.admin list-restaurants                      # alle caféer med id
uv run python -m app.admin reset-password --email ejer@gotland.dk   # ny adgangskode (glemt kode)
uv run python -m app.admin deactivate --email ejer@gotland.dk       # slå fra, logger ud med det samme
uv run python -m app.admin activate --email ejer@gotland.dk         # slå til igen
```

## Start appen

```
uv run python -m app
```

Åbn http://localhost:3000 og log ind. Stop serveren med `Ctrl+C`.

Ser du den gamle side, så er det browserens cache. Hård genindlæsning (`Cmd+Shift+R`) eller et privat vindue løser det.

## Se hjemmesiden (stamgaest-website)

Fra projektets rod:

```
python3 -m http.server 8000 --directory stamgaest-website
```

Åbn http://localhost:8000. Dansk er forsiden, engelsk ligger på `/en/`.

## Tests

```
uv run pytest tests/test_unit.py     # hurtig, kræver ingen database
uv run pytest                        # alt, ca. 3 min (kræver DATABASE_URL)
```

## Git: det du bruger hele tiden

Lav altid en ny branch, før du ændrer noget. Rør ikke `main` direkte.

```
git status                           # hvad har jeg ændret?
git checkout -b navn-paa-branch      # ny branch
git add <filer>                      # vælg hvad der skal med
git commit -m "Kort beskrivelse"
git push -u origin navn-paa-branch   # første gang
gh pr create --base main             # opret pull request (tjek at base er main!)
```

## Når noget går galt

| Problem | Løsning |
| --- | --- |
| `DATABASE_URL mangler` | Tjek at `.env` ligger i `python-backend/` og har nøglen |
| "Der findes allerede en bruger" | Brug `reset-password` til den eksisterende bruger |
| Mistet adgangskoden | `reset-password --email ...` |
| Port 3000 er optaget | Luk den gamle server (`Ctrl+C`), eller ret `PORT` i `.env` |
| Login virker ikke lokalt | Tjek at `COOKIE_SECURE` ikke er sat til 1 i `.env` |
| `/docs` er hvid eller findes ikke | Sæt `ENABLE_DOCS=1` i `.env` og genstart |
