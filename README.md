# Stamgæst — dashboard-prototype

Et dashboard hvor en butiksejer logger ind, henter sin virksomheds anmeldelser fra
Google, vælger hvilke der skal have AI-genereret svar (fx alle 5-stjernede),
gennemgår/redigerer udkastet, og trykker "Send". Hver kunde har sit eget login og ser
**kun sin egen café** (se "Kunder og login"). "Send til Google" kopierer til
udklipsholder i stedet for at poste direkte (se afsnittet om Google Business Profile
API nedenfor).

Backenden er skrevet i Python med [FastAPI](https://fastapi.tiangolo.com/), og data
gemmes i PostgreSQL på Neon. Frontenden er én HTML-fil (`public/index.html`), som
backenden selv serverer.

## Sådan kører du den i VS Code

Du skal bruge [uv](https://docs.astral.sh/uv/) (Python-pakkehåndtering; på Mac:
`brew install uv`). Python 3.14 hentes af uv, hvis du ikke har den.

1. Åbn mappen `review-assistant` i VS Code (`File > Open Folder`)
2. Åbn integreret terminal (`Ctrl+\`` / `Cmd+\``) og gå ind i backend-mappen:
   ```
   cd python-backend
   ```
3. Installer afhængigheder:
   ```
   uv sync
   ```
4. Kopiér `.env.example` til `.env`:
   ```
   cp .env.example .env
   ```
5. Åbn `.env` og indsæt din egen Anthropic API-nøgle (hent en gratis på https://console.anthropic.com/settings/keys — kræver et lille indestående på kontoen, typisk et par kroner er nok til at teste)
6. Sæt databasen op (se afsnittet nedenfor)
7. Opret en kunde til dig selv (se "Kunder og login"). Du kan ikke bruge siden uden et login
8. Start serveren:
   ```
   uv run python -m app
   ```
9. Åbn http://localhost:3000 i browseren og log ind

Serveren lytter kun på din egen maskine (`127.0.0.1`), så den kan ikke nås fra andre
computere. Det er med vilje, se "Sikkerhed" nedenfor.

## Kunder og login

Hver kunde (fx en café) har et login, som er koblet til **præcis én café**. Kunden ser og
ændrer kun sin egen cafés anmeldelser. Sådan hænger det sammen:

- **Login med e-mail og adgangskode.** Adgangskoder gemmes aldrig som tekst, kun som en
  argon2-hash. Efter login får browseren en tilfældig nøgle i en cookie (7 dage), og
  databasen gemmer kun et fingeraftryk af den.
- **Serveren afgør, hvilken café det gælder, ud fra login.** Browseren nævner aldrig en
  café. Sender den alligevel et café-id eller sted-id med, ignoreres det. Et
  anmeldelses-id fra en anden café giver "findes ikke".
- **Kunden kan ikke søge efter steder.** Google-stedet kobler *vi* til caféen, når kunden
  oprettes. "Hent seneste anmeldelser" henter altid for det sted (højst en gang i minuttet).
- **Vi opretter kunderne selv.** Der er ingen tilmelding på siden og ingen admin-side på
  nettet, kun et kommandolinjeværktøj.

### Sådan opretter du en ny kunde (fx efter et besøg hos en café)

Kør fra `python-backend/`:

```
# 1. Find caféens Google-id
uv run python -m app.admin search "Café Gotland København"

# 2. Opret kunden. Første hentning af anmeldelser sker med det samme
uv run python -m app.admin create-customer --email ejer@gotland.dk --place-id ChIJ... --business-type café
```

Værktøjet laver en tilfældig adgangskode og viser den **én gang**. Giv den til kunden, som
kan skifte den under "Skift adgangskode" på siden. Adgangskoder skrives aldrig på
kommandolinjen, for de ville ende i terminalens historik.

| Kommando | Hvad den gør |
| --- | --- |
| `search "navn by"` | Finder en café hos Google og viser dens `place-id` |
| `create-customer --email … --place-id …` | Opretter café og kunde ud fra et Google-sted |
| `create-customer --email … --restaurant-id …` | Kobler en ny bruger til en café, der allerede findes (fx en medarbejder) |
| `list-customers` / `list-restaurants` | Viser kunder / caféer (med id) |
| `reset-password --email …` | Ny adgangskode, og kunden logges ud alle steder (glemt kode) |
| `deactivate --email …` / `activate --email …` | Slår en kunde fra/til, og fra logger dem ud med det samme |

## Database (PostgreSQL på Neon)

Databasen ligger hos [Neon](https://neon.tech/) og kører døgnet rundt, så vi
begge arbejder mod de samme data uden at nogens computer skal være tændt.

### Hvis databasen allerede er sat op (det normale)

1. Få forbindelsesstrengen af den anden — den står **ikke** i Git, fordi den
   indeholder adgangskoden til databasen
2. Sæt den ind som `DATABASE_URL` i `python-backend/.env` (uden mellemrum omkring `=`)
3. `uv run python -m app.db.migrate` — opretter de tabeller du eventuelt mangler
4. `uv run python -m app`

### Første gang databasen oprettes

1. Opret en gratis konto på https://neon.tech (ingen betalingskort)
2. Opret et projekt — vælg region **EU (Frankfurt)**, så data bliver i EU og
   svartiden fra Danmark er lav
3. Kopiér "Connection string" fra dashboardet. Den ser sådan ud:
   ```
   postgresql://bruger:adgangskode@ep-xxxx.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```
4. Sæt den ind i `python-backend/.env` som `DATABASE_URL`, men ret `sslmode=require`
   til `sslmode=verify-full` — se `.env.example` for hvorfor
5. Kør `uv run python -m app.db.migrate`

Databasen hedder `neondb`. Det er med vilje: det er den Neon selv opretter, og
vores tabeller ligger inde i den. Der skal ikke oprettes en `feedbackfox`.

Neons onboarding foreslår også `neon skills`, `neon mcp`, `neon config init` og
`neon deploy`. Det er deres infrastructure-as-code-værktøj og agent-integrationer —
vi bruger ingen af delene. Projektet snakker helt almindelig Postgres via
`psycopg`, så der skal kun bruges en forbindelsesstreng.

### Fælles database — hvad det betyder i praksis

- I ser **de samme anmeldelser og svar med det samme**. Sletter den ene noget,
  er det også væk for den anden
- Skal I teste noget rodet, så skift midlertidigt til en lokal database (se
  `.env.example`) — eller lav en gratis **branch** i Neon, som er en kopi af
  data I kan arbejde i uden at ødelægge noget
- Neon lukker databasen ned når den ikke bruges, og starter den igen ved næste
  forespørgsel. Første kald efter en pause tager derfor et øjeblik. Det er ikke
  en fejl, og ventetiden i `app/db/pool.py` er sat høj nok til det

### Del aldrig forbindelsesstrengen i Git

`DATABASE_URL` indeholder adgangskoden til hele databasen. `.env` er i
`.gitignore` og skal blive der. Send strengen til hinanden på en måde der ikke
ligger offentligt — og hvis den først er havnet i en commit, så skift
adgangskoden i Neon med det samme frem for bare at slette linjen.

`app/db/pool.py` kræver desuden et gyldigt certifikat for alt der ikke er
localhost. Det er bevidst ikke overladt til `sslmode` i forbindelsesstrengen: en
streng med `sslmode=disable` ville ellers slå tjekket fra, uden at man kunne se det
nogen steder i koden. Får du en certifikatfejl, så sig til frem for at slå
verifikationen fra — så er der noget galt der er værd at kigge på.

### Migrationer

```
uv run python -m app.db.migrate
```

Kommandoen er idempotent: den holder styr på hvad der er kørt i tabellen
`schema_migrations` og springer det over næste gang.

Skemaændringer laves som en **ny** fil i `python-backend/app/db/migrations/` (fx
`002_tilføj_kolonne.sql`). Ret aldrig i en migration der allerede er kørt — den
er kørt på den fælles database, og en rettelse i filen bliver aldrig udført der.

### Kigge direkte i dataene

Nemmest er "SQL Editor" i Neons dashboard. Vil du hellere bruge terminalen, kan
du bruge `psql` fra Postgres.app med den samme forbindelsesstreng:

```
/Applications/Postgres.app/Contents/Versions/latest/bin/psql "DIN_DATABASE_URL"
```

Er du træt af den lange sti, så læg den i `PATH` én gang:

```
sudo mkdir -p /etc/paths.d && echo /Applications/Postgres.app/Contents/Versions/latest/bin | sudo tee /etc/paths.d/postgresapp
```

Nyttige kommandoer: `\dt` (vis tabeller), `\d reviews` (vis kolonner),
`SELECT * FROM reviews;`, `\q` (afslut).

## Hvordan du bruger den

1. Log ind med den e-mail og adgangskode, du har fået
2. Klik "Hent seneste anmeldelser" — de lander under "Nye / ubesvarede"
3. Brug filter-chips (fx "★★★★★") til at vælge en gruppe, sæt flueben ved dem du vil have svar til
4. Vælg tone, og klik "Generér svar for valgte" — udkast dukker op under hver anmeldelse, redigérbare
5. Klik "Send til Google" — kopierer svaret til udklipsholderen og flytter anmeldelsen til "Besvarede"-fanen
6. Har en anmeldelse allerede fået svar direkte på Google (fx før I brugte dette værktøj), sæt flueben ved den og klik "Markér som besvaret" for at flytte den til "Besvarede" uden at generere et nyt svar
7. Første gang: skift adgangskoden under "Skift adgangskode" øverst

## Tests

Kør dem fra `python-backend/`:

```
uv run pytest
```

Der er to slags tests. Ingen af dem koster penge eller rører jeres rigtige data:

- **Uden database** (`tests/test_unit.py`): validering, sikkerhedsheaders, prompten,
  TLS-opsætningen, adgangskoder, begrænsning af gætte-forsøg og at alle endpoints kræver login.
- **Med database** (`tests/test_*_db.py`): kundens hele flow (`test_api_db`), login og
  sessioner (`test_auth_db`), **at en kunde aldrig kan se eller ændre en anden kundes data**
  (`test_isolation_db`) og admin-værktøjet (`test_admin_db`). De kører i et midlertidigt
  schema med tilfældigt navn i Neon, som oprettes før og slettes efter. Google og Claude er
  falske. Mangler `DATABASE_URL`, springes de over.

En hel kørsel tager ca. 3 minutter, fordi databasen ligger i skyen.

## Sikkerhed

- **Al input valideres** med typer og grænser (fx højst 25 anmeldelser pr. kald,
  svar højst 4096 tegn, id'er skal være UUID'er). Fejl svarer med de feltnavne der er
  galt, aldrig med det klienten sendte.
- **Kun parametriseret SQL.** Ingen værdier bygges ind i SQL-tekst.
- **Anmeldelsernes tekst læses fra databasen**, ikke fra det browseren sender med
  til `/api/generate`, og den står i tags i prompten, som Claude får besked på at
  behandle som data (beskytter mod "prompt injection" fra anmeldere).
- **Fejl fra Google, Claude og databasen sendes ikke videre til browseren.** De
  logges på serveren; brugeren får en tekst vi selv har skrevet.
- **Anmeldelser vises som ren tekst.** Frontenden renser alt fra Google og Claude,
  før det sættes ind i siden, så en anmeldelse ikke kan køre kode i browseren
  ("XSS"). Som ekstra lag tillader en CSP kun det indlejrede script i `index.html`
  (udpeget ved dets hash), siden kan ikke lægges i en iframe, og API-svar caches ikke.
- **`Host`-tjek** (`ALLOWED_HOSTS`) mod DNS rebinding, og serveren binder kun til
  `127.0.0.1`.
- **Grænse for request-størrelse** (256 KB), timeouts på alle eksterne kald, og
  `/docs` er slukket som standard (tænd med `ENABLE_DOCS=1`). Er den tændt, får kun
  `/docs` og `/redoc` en lempeligere CSP, fordi de henter deres design fra et eksternt
  CDN. Selve siden og API'et beholder den strenge.
- **Ingen redirects** følges ved kald til Google, så API-nøglen aldrig sendes videre.

- **Login og adskillelse af kunder:** alle data-endpoints kræver login. Alle databaseopslag
  på anmeldelser tjekker også `restaurant_id` i selve SQL'en, så adskillelsen ikke kun afhænger
  af routerne. Forkerte og manglende id'er giver samme svar ("findes ikke").
- **Adgangskoder:** argon2id, mindst 10 tegn, og server-genererede adgangskoder til nye kunder.
  Samme svar på "forkert adgangskode" og "ukendt e-mail", og der regnes lige lang tid på begge.
- **Gætte-angreb:** højst 5 forkerte forsøg pr. e-mail og 20 pr. IP-adresse på 15 minutter
  (også selvom næste gæt er rigtigt). Højst 100 udkast pr. bruger i timen, og en pause på
  et minut mellem hentninger fra Google.
- **Cookie:** `HttpOnly` (kan ikke læses af JavaScript), `SameSite=Lax`, ny nøgle ved hvert
  login, og logud og skift af adgangskode dræber sessionerne. Ændrende kald fra en anden
  hjemmeside afvises (Origin-tjek).

**Før en rigtig kunde bruger det, skal disse ting på plads:**

- **Hosting med HTTPS**, og `COOKIE_SECURE=1` i `.env`, så cookien kun sendes over HTTPS.
  Serveren binder i dag kun til `127.0.0.1`, og `ALLOWED_HOSTS` skal sættes til jeres domæne.
- **Kører serveren bag en proxy** (som de fleste hosts), ser den proxyens IP i stedet for
  kundens, og grænsen for gætte-forsøg pr. IP virker så ikke rigtigt. Det skal løses ved opsætning.
- **Tællerne til gætte-forsøg ligger i hukommelsen.** De nulstilles ved genstart og virker kun i
  én proces. Kører I flere processer, skal de flyttes til databasen.
- **"Glemt adgangskode" sker pr. henvendelse til os** (`reset-password`). Der er ingen mail-flow.

## Vejen til "1-knap send direkte til Google"

Lige nu er "Send"-knappen en placeholder (kopiér til udklipsholder). For at den
reelt poster svaret på Google, skal I igennem **Google Business Profile API**:

1. Ansøg om adgang via Google Cloud Console — kræver en verificeret Google
   Business-profil der har været aktiv i 60+ dage, samt en beskrivelse af jeres
   brug. Godkendelsen tager tid, så søg tidligt.
2. Hver restaurant-kunde skal selv logge ind med sin egen Google-konto via
   OAuth 2.0 og godkende adgang — det er ikke noget du kan gøre på deres vegne
   uden deres aktive login.
3. Når adgangen er givet, bruges `accounts.locations.reviews.updateReply`
   endpointet til selve afsendelsen.

Indtil da er kopiér-til-udklipsholder en helt fin (og etisk sikker) måde at
demonstrere værdien for kunderne på.

## Automatisk hentning af anmeldelser (Google Places API)

Når en kunde klikker "Hent seneste anmeldelser", henter serveren anmeldelserne for det
Google-sted, caféen blev koblet til ved oprettelsen (se "Kunder og login"). Kunden kan ikke
vælge et andet sted.

For at det virker:

1. Indsæt en `GOOGLE_PLACES_API_KEY` i `python-backend/.env` (se `.env.example`). Den bruges både til at oprette kunder (`search`) og til kundernes hentninger
2. I [Google Cloud Console](https://console.cloud.google.com/apis/library): aktivér
   **"Places API (New)"** for det projekt nøglen tilhører
3. Sørg for at projektet har en **billing-konto** koblet på — Places API kræver
   det, selvom I ikke overskrider den gratis månedlige kvote
4. Hvis nøglen har API-restriktioner sat (Credentials → din nøgle → "API restrictions"),
   skal "Places API (New)" stå på listen over tilladte API'er

**Vigtig begrænsning:** Google udstiller kun de **5 mest relevante anmeldelser**
pr. sted via denne API — ikke jeres fulde historik. Det er en begrænsning i
Google's API, ikke noget der kan konfigureres væk. Til at hente og besvare
*alle* anmeldelser (og poste svar direkte) kræves Google Business Profile API,
se afsnittet ovenfor.

## Hvad der ellers mangler før det er et rigtigt produkt

- **Hosting og HTTPS** samt de øvrige punkter under "Før en rigtig kunde bruger det" i afsnittet "Sikkerhed".
- **Log ind med Google** (og adgang til alle anmeldelser og direkte svar) kræver godkendelse til Business Profile API. Login med e-mail er lavet, så det kan kobles på senere uden at kunderne mærker det.
- **"Nye siden sidst"-tælleren** tæller nu de anmeldelser der reelt står som ubesvarede i databasen. Den mangler stadig at tage højde for *hvornår ejeren sidst var inde* — så "ny" betyder "ikke besvaret endnu", ikke "kommet til siden dit sidste besøg".
- **Flere caféer pr. bruger** (fx en kæde): i dag hører en bruger til præcis én café. Flere brugere kan dele en café.
- **Betaling**: Stripe-integration til abonnement, hvis I vil automatisere fakturering.

## Hjemmesiden (stamgaest.com)

Mappen `website/` er den offentlige forside med privatlivspolitik. Den er ren HTML og CSS uden
build-trin og er adskilt fra appen (`public/`), som er selve login og dashboard.

Se den lokalt (fra projektets rod):

```
python3 -m http.server 8000 --directory website
```

Åbn derefter http://localhost:8000. Hostes på Cloudflare Pages med `website` som rodmappe og
ingen build-kommando.

## Filstruktur

```
review-assistant/
├── public/index.html            # Frontend (form + resultater), serveres af backenden
└── python-backend/
    ├── pyproject.toml           # Afhængigheder (uv.lock låser præcise versioner)
    ├── .env.example             # Skabelon — kopiér til .env
    ├── .env                     # DINE nøgler (opret selv, committes aldrig)
    ├── app/
    │   ├── main.py              # Selve appen: samler routes, sikkerhed og opstart
    │   ├── __main__.py          # Startknap: uv run python -m app
    │   ├── admin.py             # Vores værktøj til at oprette kunder (uv run python -m app.admin)
    │   ├── routers/             # HTTP-endpoints: auth (login), reviews, generate
    │   ├── schemas/             # Tjekker og former data ind og ud
    │   ├── services/            # Claude, Google Places og login (adgangskoder, sessioner)
    │   ├── models/              # Al SQL, én fil pr. tabel (restaurants, reviews, replies, users, sessions)
    │   ├── db/
    │   │   ├── pool.py          # Forbindelsen til databasen
    │   │   ├── migrate.py       # Migrations-runner
    │   │   └── migrations/      # 001_init.sql (data), 002_auth.sql (brugere og sessioner)
    │   └── core/                # Indstillinger, fejl, sikkerhed, grænser og "hvem er logget ind"
    └── tests/                   # uv run pytest
```

**Vigtigt:** `.env` ligger allerede i `.gitignore` og skal blive der — den
indeholder både API-nøgler og adgangskoden til databasen.

## API-endpoints

Alle endpoints undtagen login og logout kræver, at man er logget ind. Hvilken café det gælder,
ved serveren ud fra login, og den kan aldrig angives i kaldet.

| Metode | Sti | Hvad den gør |
| --- | --- | --- |
| `POST` | `/api/auth/login` | Logger ind (e-mail og adgangskode) og sætter cookien. |
| `POST` | `/api/auth/logout` | Sletter sessionen. |
| `GET` | `/api/auth/me` | Hvem er logget ind, og hvilken café. Siden bruger den ved opstart. |
| `POST` | `/api/auth/password` | Skifter adgangskode og logger andre enheder ud. |
| `GET` | `/api/reviews` | Din cafés oplysninger og alle dens gemte anmeldelser (henter intet fra Google). |
| `POST` | `/api/reviews/refresh` | Henter fra Google for **din** café, gemmer, og svarer med det der nu står i databasen. Højst en gang i minuttet. |
| `POST` | `/api/generate` | Genererer udkast med Claude for de valgte anmeldelser og gemmer dem i `replies`. |
| `POST` | `/api/reviews/{id}/send` | Gemmer den tekst der faktisk blev sendt, og markerer anmeldelsen besvaret. |
| `POST` | `/api/reviews/{id}/answered` | Markerer besvaret uden at gemme en tekst (svaret blev givet på Google). |
