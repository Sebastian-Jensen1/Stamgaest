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
| `RESEND_API_KEY` | Nøgle til Resend (mails). Uden den sendes der ingen "glemt adgangskode"-mails |
| `MAIL_FROM` | Afsender, fx `Stamgæst <noreply@mail.stamgaest.com>` |
| `APP_BASE_URL` | Adressen siden ligger på (lokalt `http://localhost:3000`) |

Opret tabellerne i databasen (sikkert at køre igen):

```
uv run python -m app.db.migrate
```

## Opret en bruger (kunde)

En kunde hører til præcis én café. Man kan ikke tilmelde sig på siden, så brugere oprettes her.
Kunden får en **invitation på mail** og vælger selv sin adgangskode, så du aldrig ser den.

```
# 1. Find caféens Google place-id (starter med ChIJ...)
uv run python -m app.admin search "navn og by" fx: "Café Gotland København"

# 2. Opret kunden. Anmeldelserne hentes med det samme, og invitationen sendes
uv run python -m app.admin create-customer --email ejer@gotland.dk --place-id ChIJ... --business-type café
```

Værktøjet beder dig **skrive e-mailadressen igen**, før noget sker. Det er for at fange tastefejl:
en invitation til en forkert adresse kunne give en fremmed adgang til caféen. Linket i mailen virker
i **7 dage** og kun én gang.

Skal en ekstra medarbejder have adgang til en café, der allerede findes:

```
uv run python -m app.admin list-restaurants                      # find café-id
uv run python -m app.admin create-customer --email medarbejder@gotland.dk --restaurant-id <id>
```

### Send igen, træk tilbage, se status

```
uv run python -m app.admin invite --email ejer@gotland.dk          # send en ny velkomstmail (den gamle virker ikke mere)
uv run python -m app.admin revoke-invite --email ejer@gotland.dk   # træk invitationen tilbage
uv run python -m app.admin list-customers                          # status: afventer invitation / INVITATION UDLØBET / aktiv
```

Indtil kunden har valgt en adgangskode, kan "Glemt adgangskode?" **ikke** bruges. Kun du kan sende en ny
invitation. Det er med vilje, så en trukket invitation ikke kan omgås.

`invite` virker kun for kunder, der ikke har valgt en adgangskode endnu. Har de glemt den, så brug
"Glemt adgangskode?" på loginsiden.

Kan en kunde ikke få mail, kan du i stedet vise en adgangskode **én gang** (vises i terminalen, så giv den videre
på en sikker måde):

```
uv run python -m app.admin create-customer --email ejer@gotland.dk --place-id ChIJ... --show-password
```

Vil du springe bekræftelsen af e-mailen over (fx i et script), så tilføj `--yes`.

## Administrér brugere

```
uv run python -m app.admin list-customers                        # alle kunder
uv run python -m app.admin list-restaurants                      # alle caféer med id
uv run python -m app.admin reset-password --email ejer@gotland.dk   # ny adgangskode (glemt kode)
uv run python -m app.admin deactivate --email ejer@gotland.dk       # slå fra, logger ud med det samme
uv run python -m app.admin activate --email ejer@gotland.dk         # slå til igen
```

## Gør udkastene skræddersyede (før en demo eller pilot)

Log ind som caféen, og tryk **"Indstillinger for svar"** øverst i dashboardet:

1. **Stil:** vælg længde (Automatisk er et godt sted at starte), beskriv tonen med egne ord, skriv
   underskrift, og hvad der aldrig må skrives.
2. **Om stedet:** tryk **"Lav forslag fra jeres hjemmeside"**. Efter ca. et halvt minut kommer
   Claudes forslag med et flueben ved hvert punkt. Fjern fluebenet ved det irrelevante, ret
   teksten, hvor det trænger, og tryk **"Tilføj valgte"** (det gemmer også).
3. **Eksempler:** indsæt 3-5 svar, caféen selv har skrevet (fx fra deres Google-profil).
4. **Prøv et udkast:** vælg en anmeldelse, og se svaret med det, der står på siden.
5. Tryk **"Gem indstillinger"**.

Tjek kvaliteten med testsættet, før du viser det til nogen:

```
uv run python -m app.admin list-restaurants                      # find café-id
uv run python -m evals.compare --restaurant-id <id>             # ca. 0,30 USD, du skal sige ja først
```

Åbn rapporten (stien står i terminalen) i browseren, og læs svarene igennem.

## Beskyt mod en stor regning

Hvert udkast fra Claude og hver hentning fra Google koster penge. Der er lofter pr. café og for hele
Stamgæst, pr. dag og pr. måned. De står i `python-backend/.env` som `CAP_...`-linjer. Ret tallet og
genstart serveren. Alle navne og standardværdier står i `.env.example`.

```
uv run python -m app.admin usage                                    # forbrug pr. café og hvor tæt vi er på lofterne
uv run python -m app.admin ai-off --restaurant-id <id>              # nødbremse: ingen udkast for én café
uv run python -m app.admin ai-on --restaurant-id <id>               # slå til igen
```

Nødbremse for **alle**: sæt `AI_PAUSED=1` i `.env` og genstart. Du får en mail til `ADMIN_EMAIL`, når et
loft når 80 % og 100 %.

**Gør også dette hos udbyderne.** Lækkes en API-nøgle, kan den bruges helt uden om vores kode, og så er det
kun disse grænser, der beskytter dig:

1. **Anthropic Console** (console.anthropic.com): sæt en månedlig forbrugsgrænse under Limits/Billing, og
   brug en API-nøgle der kun bruges til Stamgæst.
2. **Google Cloud Console**: under APIs & Services → Credentials, begræns Places-nøglen til **kun
   Places API**. Under Places API → Quotas, sæt et dagligt loft på antal kald. Under Billing → Budgets &
   alerts, opret en budget-alarm.
3. Lækker en nøgle alligevel: lav en ny nøgle hos udbyderen med det samme, og slet den gamle.

## Start appen

```
uv run python -m app
```

Åbn http://localhost:3000 og log ind. Stop serveren med `Ctrl+C`.

Ser du den gamle side, så er det browserens cache. Hård genindlæsning (`Cmd+Shift+R`) eller et privat vindue løser det.

## Gør appen tilgængelig på nettet (Cloudflare Tunnel)

Linket i "glemt adgangskode"-mails skal pege på en adresse, kunden kan åbne. En tunnel giver
din lokale server adressen `https://app.stamgaest.com`, uden at du skal købe hosting. Den virker
kun, mens din computer er tændt og serveren kører, så den er til test og demo og ikke til drift.

**Engang (ca. 10 minutter):**

```
brew install cloudflared                            # kun hvis du ikke har den
cloudflared tunnel login                            # åbner browseren: vælg stamgaest.com
cloudflared tunnel create stamgaest                 # skriver et tunnel-id (UUID) og en .json-fil
cloudflared tunnel route dns stamgaest app.stamgaest.com   # opretter DNS-recorden i Cloudflare
```

Opret filen `~/.cloudflared/config.yml` (ret `<ID>` til tunnel-id'et fra `create`):

```
tunnel: stamgaest
credentials-file: /Users/DITBRUGERNAVN/.cloudflared/<ID>.json
ingress:
  - hostname: app.stamgaest.com
    service: http://localhost:3000
  - service: http_status:404
```

Tilføj i `python-backend/.env`:

```
ALLOWED_HOSTS=localhost,127.0.0.1,app.stamgaest.com
COOKIE_SECURE=1
APP_BASE_URL=https://app.stamgaest.com
```

**Hver gang du vil have den online** (to terminaler):

```
uv run python -m app                # i python-backend/
cloudflared tunnel run stamgaest    # i en ny terminal
```

Åbn https://app.stamgaest.com. Med `COOKIE_SECURE=1` er det kun https-adressen, der er garanteret at
virke. På `http://localhost:3000` afhænger det af browseren: Chrome og Firefox gemmer gerne cookien dér,
Safari gør ikke. Vil du arbejde lokalt uden at tænke over det, så kommentér `COOKIE_SECURE` og
`APP_BASE_URL` ud og genstart serveren.

Serveren nægter at starte, hvis `COOKIE_SECURE=1` er sat uden en https-`APP_BASE_URL`.

Husk: gennem tunnelen ser serveren alle besøgende som samme IP-adresse (`127.0.0.1`), så
grænserne pr. IP (forkerte logins, glemt adgangskode) gælder alle samlet. Det er fint til test,
men skal løses ved rigtig hosting (se README, afsnittet "Sikkerhed").

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
| "Der findes allerede en bruger" | Brug `invite` (hvis de ikke har valgt adgangskode) eller `reset-password` til den eksisterende bruger |
| "Google sendte ingen anmeldelser for jeres sted" | Google svarer uden anmeldelser i stedet for at give en fejl, når API-nøglen ikke er sat helt op. Tjek at oprettelsen af nøglen er gjort færdig i Google Cloud Console, at den er begrænset til **Places API (New)**, og at dagens loft (Quotas) ikke er brugt op. Serverens log skriver "Google sendte 0 anmeldelser ..." |
| Kunden har ikke fået invitationen | Tjek spam. Send en ny med `invite --email ...`. Se `list-customers` for status |
| Mistet adgangskoden | Kunden kan selv trykke "Glemt adgangskode?" på loginsiden. Virker mail ikke: `reset-password --email ...` |
| Mailen kommer ikke frem | Tjek `RESEND_API_KEY`, at afsenderdomænet er bekræftet hos Resend, og spam-mappen |
| Port 3000 er optaget | Luk den gamle server (`Ctrl+C`), eller ret `PORT` i `.env` |
| Login virker ikke lokalt | Tjek at `COOKIE_SECURE` ikke er sat til 1 i `.env` (eller brug https-adressen fra tunnelen) |
| `Invalid host header` / 400 via tunnelen | Tilføj `app.stamgaest.com` i `ALLOWED_HOSTS` og genstart serveren |
| `/docs` er hvid eller findes ikke | Sæt `ENABLE_DOCS=1` i `.env` og genstart |
