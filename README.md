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

### Glemt adgangskode (mail)

Kunden trykker "Glemt adgangskode?" på loginsiden og skriver sin e-mail. Findes den, sender
serveren et link, der virker i én time og kun én gang. Linket åbner siden, hvor kunden vælger en
ny adgangskode, og bagefter er kunden logget ud alle steder.

- **Samme svar uanset om e-mailen findes**, så man ikke kan gætte hvem der er kunde. Mailen sendes
  efter svaret, så svartiden heller ikke afslører det.
- **Nøglen står efter `#` i linket** (`/#nulstil=...`). Den sendes aldrig til serveren af browseren
  og står derfor ikke i nogen log. Databasen gemmer kun et fingeraftryk af den.
- **Højst 3 mails pr. e-mail og 10 pr. IP-adresse i timen**, også for e-mails der ikke findes.
- Et nyt link gør de gamle ubrugelige. Slåede-fra brugere får ingen mail.
- `reset-password` i kommandolinjeværktøjet virker stadig, hvis kunden ikke kan få mail.
- Er kunden netop inviteret og har ikke valgt en adgangskode endnu, sendes der ingen mail herfra (se nedenfor).

Mails sendes via [Resend](https://resend.com) og kræver tre ting i `.env`:

| Nøgle | Hvad |
| --- | --- |
| `RESEND_API_KEY` | API-nøgle fra Resend (uden den sendes der ingen mails) |
| `MAIL_FROM` | Afsender, fx `Stamgæst <noreply@mail.stamgaest.com>` |
| `APP_BASE_URL` | Adressen siden ligger på. Lokalt `http://localhost:3000`, i drift `https://stamgaest.com` |

Før kunder kan få mails, skal domænet bekræftes hos Resend: tilføj `mail.stamgaest.com` under
**Domains**, opret de records Resend viser i Cloudflare (sæt skyen til **DNS only**), og tryk Verify.
Indtil da virker kun Resends testafsender (standard), og den kan kun sende til din egen mail.

### Sådan opretter du en ny kunde (fx efter et besøg hos en café)

Kør fra `python-backend/`:

```
# 1. Find caféens Google-id
uv run python -m app.admin search "Café Gotland København"

# 2. Opret kunden. Første hentning af anmeldelser sker med det samme
uv run python -m app.admin create-customer --email ejer@gotland.dk --place-id ChIJ... --business-type café
```

Værktøjet beder dig skrive kundens e-mail igen (så en tastefejl ikke sender en invitation til en
fremmed) og sender så en **velkomstmail** med et link, der virker i 7 dage og kun én gang. Kunden
vælger selv sin adgangskode, så vi aldrig ser den. Adgangskoder skrives aldrig på kommandolinjen,
for de ville ende i terminalens historik.

Indtil kunden har valgt en adgangskode, er kontoen "inviteret": ingen kender adgangskoden, og
"Glemt adgangskode?" sender intet. Kun vi kan sende en ny invitation eller trække den tilbage.
Ellers kunne en fejlindtastet e-mail overtage en café ad omvejen, efter at invitationen er trukket
tilbage. Kan en kunde ikke få mail, kan `--show-password` vise en adgangskode én gang i stedet.

| Kommando | Hvad den gør |
| --- | --- |
| `search "navn by"` | Finder en café hos Google og viser dens `place-id` |
| `create-customer --email … --place-id …` | Opretter café og kunde ud fra et Google-sted og sender en invitation. `--yes` springer bekræftelsen af e-mailen over, `--show-password` viser en adgangskode i stedet for at sende mail |
| `create-customer --email … --restaurant-id …` | Kobler en ny bruger til en café, der allerede findes (fx en medarbejder) |
| `invite --email …` | Sender en ny velkomstmail (den gamle virker ikke mere). Kun til kunder der ikke har valgt adgangskode |
| `revoke-invite --email …` | Trækker en invitation tilbage |
| `list-customers` / `list-restaurants` | Viser kunder (med status: afventer invitation / INVITATION UDLØBET / aktiv) / caféer (med id) |
| `reset-password --email …` | Ny adgangskode, og kunden logges ud alle steder (glemt kode) |
| `deactivate --email …` / `activate --email …` | Slår en kunde fra/til, og fra logger dem ud med det samme |
| `usage [--days N]` | Forbrug af Claude og Google pr. café, anslået beløb, og hvor tæt vi er på lofterne |
| `ai-off --restaurant-id …` / `ai-on --restaurant-id …` | Nødbremse: slår udkast fra Claude fra/til for én café med det samme |
| `stats [--days N] [--restaurant-id …]` | Hvor meget kunderne retter i Claudes udkast før de sender, og de mest rettede eksempler. Kun til os |

## "Nye siden sidst"

Tallet på dashboardet viser de anmeldelser, der er kommet ind hos os siden brugerens forrige besøg.

- **Den første hentning er en import**, ikke noget nyt. Det caféen havde i forvejen, tæller aldrig
  med, så en ny kunde ikke ser alle sine anmeldelser som "nye" (`restaurants.first_import_at`).
- **Pr. bruger:** to ejere af samme café har hver deres tal (kolonnerne `reviews_seen_at` og
  `previous_reviews_seen_at` på `users`, migration 008).
- **Et besøg** slutter efter 1 times pause (`VISIT_PAUSE_MINUTES` i `routers/reviews.py`). Så
  nulstilles tallet ikke, bare fordi siden genindlæses, mens ejeren er i gang med at svare.
- **"Kommet ind hos os"** betyder hvornår vi hentede anmeldelsen, ikke hvornår den blev skrevet.
  Google sender de 5 mest "relevante", så en gammel anmeldelse kan dukke op for første gang.
- En anmeldelse tæller med resten af besøget, også når den er besvaret. Hvad der mangler svar,
  viser fanen "Nye / ubesvarede".
- Første gang en bruger er inde, er alt der er kommet til efter importen nyt.

## Svar skræddersyet til hver café

Hver café har sine egne **"Indstillinger for svar"** (knappen øverst i dashboardet åbner en
side for sig, der følger `DESIGN_savvycal.md`). Claude får dem med i hvert udkast, så svarene
lyder som caféen og ikke som en generisk virksomhed. Svarene skrives altid med "du".

| Del | Hvad | Hvem |
| --- | --- | --- |
| **Stil** | Længde (Automatisk, Meget kort, Kort, Mellem, Lang), tonen med egne ord, underskrift, ting der aldrig må skrives, hvor utilfredse gæster henvises hen | Ejeren |
| **Om stedet** | Koncept, retter og drikke (mærkater), kendetegn (liste), stemning | Ejeren, evt. ud fra Claudes forslag fra hjemmesiden |
| **Eksempler** | Svar ejeren selv har skrevet og er glad for | Ejeren indsætter dem |
| **Tidligere svar** | De 5 seneste svar caféen har sendt gennem Stamgæst (efter rettelser) | Automatisk |

**Regler, der altid gælder** (står i system-prompten og kan ikke ændres af caféen): fakta bruges
kun til at kommentere det, anmelderen selv nævner. Ingen priser, åbningstider, løfter, rabatter,
kompensation eller reklame. Ejerens ønsker følges, så længe de ikke strider mod reglerne.

**"Lav forslag fra jeres hjemmeside":**
- Hjemmesiden er den, Google oplyser for stedet. Kunden kan ikke pege Claude på en anden side.
- Claude henter siderne med sit eget web-fetch-værktøj (Anthropic henter, ikke vores server), kun
  på caféens eget domæne og højst 5 sider. Priser og åbningstider kommer aldrig med i forslaget.
- Det kører i baggrunden (typisk under et minut), og siden viser status undervejs.
- **Forslaget bruges aldrig, før ejeren har valgt fra det.** Det vises i de samme felter, med et
  flueben ved hvert punkt, og kun det, der er nyt i forhold til det, der allerede står. Ejeren fjerner
  fluebenet ved det irrelevante, kan rette teksten i hvert punkt og trykker "Tilføj valgte", som
  også gemmer. Hjemmesiden kan være forældet, og teksten kommer fra en tredjepart.
- Det tæller under sine egne lofter (`CAP_PROFILES_*`) og nødbremserne.

**"Prøv et udkast"** på samme side laver ét udkast til en af caféens anmeldelser med det, der står
på siden lige nu, også før det er gemt. Det gemmes ikke, og det tæller som et udkast under lofterne.

**Pris:** profilen gør hvert udkast længere, men den caches hos Claude ("prompt caching"), så den
næsten kun betales én gang ad gangen for flere udkast i træk.

### Testsæt: bliver udkastene faktisk bedre?

`python-backend/evals/` indeholder 15 opdigtede, svære anmeldelser (vrede kunder, krav om penge
tilbage, kun stjerner, tysk, prompt injection, en ret der ikke er på menuen, et spørgsmål osv.)
til et opdigtet sted med en fuld profil. Hver har et "tjek", der siger, hvad et godt svar skal
eller ikke må.

```
uv run python -m evals.compare                          # gammel prompt mod ny prompt
uv run python -m evals.compare --restaurant-id <id>     # med en rigtig cafés profil og anmeldelser
uv run python -m evals.compare --compare-model <model>  # sammenlign to modeller med den nye prompt
```

Det kalder Claude rigtigt (ca. 0,30 USD for hele sættet), så prisen vises, og du skal sige ja
først. Resultatet er en HTML-side i `evals/out/` med svarene side om side. Mappen er i
`.gitignore`, for den kan indeholde rigtige anmeldelser. Kør det, hver gang prompten ændres,
og før en ny café går i gang.

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
4. Klik "Generér svar for valgte" — udkast dukker op under hver anmeldelse, redigérbare. Tonen og
   resten af stilen kommer fra "Indstillinger for svar"
5. Klik "Send til Google" — kopierer svaret til udklipsholderen og flytter anmeldelsen til "Besvarede"-fanen
6. Har en anmeldelse allerede fået svar direkte på Google (fx før I brugte dette værktøj), sæt flueben ved den og klik "Markér som besvaret" for at flytte den til "Besvarede" uden at generere et nyt svar
7. Udfyld "Indstillinger for svar", så udkastene lyder som jer (se "Svar skræddersyet til hver café")

## Tests

Kør dem fra `python-backend/`:

```
uv run pytest
```

Der er to slags tests. Ingen af dem koster penge eller rører jeres rigtige data:

- **Uden database** (`tests/test_unit.py`): validering, sikkerhedsheaders, prompten og caféens profil i den,
  TLS-opsætningen, adgangskoder, begrænsning af gætte-forsøg og at alle endpoints kræver login.
  `tests/test_hosting.py` tester `HOST`, `TRUSTED_PROXIES`, health check og at en falsk IP ignoreres.
- **Med database** (`tests/test_*_db.py`): kundens hele flow (`test_api_db`), login og
  sessioner (`test_auth_db`), **at en kunde aldrig kan se eller ændre en anden kundes data**
  (`test_isolation_db`), admin-værktøjet (`test_admin_db`), glemt adgangskode og invitation,
  lofterne for betalte kald (`test_usage_db`) og indstillinger for svar (`test_reply_settings_db`). De kører i et midlertidigt
  schema med tilfældigt navn i Neon, som oprettes før og slettes efter. Google og Claude er
  falske. Mangler `DATABASE_URL`, springes de over.

En hel kørsel tager ca. 7-8 minutter, fordi databasen ligger i skyen. Testsættet i `evals/` er
noget andet: det kalder den rigtige Claude og køres kun, når du selv starter det.

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
- **Lofter for betalte kald:** udkast fra Claude og hentninger fra Google har lofter pr. café og
  for hele Stamgæst, pr. dag og pr. måned (sættes i `.env`, se `.env.example`). Pladsen reserveres
  i databasen *før* kaldet, i én transaktion med en lås, så samtidige klik ikke kan komme over
  loftet, og lofterne holder efter en genstart. Fejlede kald tæller også med, for de kan koste
  penge, og ellers kunne man prøve igen i det uendelige. Hvert kald logges i `usage_events`
  (café, bruger, tokens), men aldrig anmeldelsestekst eller navne. Vi får en mail ved 80 % og
  100 % (`ADMIN_EMAIL`). Nødbremser: `ai-off` pr. café og `AI_PAUSED=1` for alle.
- **Lofter hos udbyderne:** lækkes en API-nøgle, kan den bruges uden om vores kode. Derfor skal
  der også være en forbrugsgrænse i Anthropic Console og et dagligt loft og en budget-alarm i
  Google Cloud (se QUICKSTART, "Beskyt mod en stor regning").
- **Gætte-angreb:** højst 5 forkerte forsøg pr. e-mail og 20 pr. IP-adresse på 15 minutter
  (også selvom næste gæt er rigtigt). Højst 100 udkast pr. bruger i timen, og en pause på
  et minut mellem hentninger fra Google.
- **Cookie:** `HttpOnly` (kan ikke læses af JavaScript), `SameSite=Lax`, ny nøgle ved hvert
  login, og logud og skift af adgangskode dræber sessionerne. Ændrende kald fra en anden
  hjemmeside afvises (Origin-tjek).

**Før en rigtig kunde bruger det, skal disse ting på plads:**

- **Hosting med HTTPS**, og `COOKIE_SECURE=1`, så cookien kun sendes over HTTPS. Koden er klar
  (se "Hosting" herunder), men selve hosten er ikke sat op endnu.
- **Tællerne til gætte-forsøg ligger i hukommelsen.** De nulstilles ved genstart og virker kun i
  én proces. Kører I flere processer, skal de flyttes til databasen.
- **Mailafsenderen skal bekræftes hos Resend** (SPF/DKIM i Cloudflare), før "glemt adgangskode" kan
  sende til kunder. Se "Glemt adgangskode (mail)" ovenfor.

### Hosting

Koden er klar til at køre hos en host (fx Render). Det styres af disse indstillinger:

| Indstilling | Standard | Hos hosten |
| --- | --- | --- |
| `HOST` | `127.0.0.1` (kun denne maskine) | `0.0.0.0`, så hostens proxy kan nå serveren |
| `PORT` | `3000` | Sættes af hosten selv (Render bruger `10000`) |
| `TRUSTED_PROXIES` | `127.0.0.1` | Hostens interne net og `cloudflare`, fx `10.0.0.0/8,cloudflare` |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | `app.stamgaest.com` (og evt. hostens egen adresse) |
| `COOKIE_SECURE` / `APP_BASE_URL` | slukket / localhost | `1` / `https://app.stamgaest.com` |

- **Serveren nægter at starte** med en anden `HOST` end 127.0.0.1, hvis `COOKIE_SECURE=1` ikke er
  sat. Ellers kunne adgangskoder sendes ukrypteret over netværket.
- **Kundens rigtige IP:** bag en proxy ser serveren proxyens IP. Den læser derfor kundens IP fra
  `X-Forwarded-For`, men kun når kaldet kommer fra en adresse i `TRUSTED_PROXIES`. Listen læses
  bagfra, og den første adresse der ikke er en kendt proxy, er kunden. En falsk IP, som en
  angriber selv skriver i headeren, bliver derfor ignoreret. `*` ("stol på alle") er ikke tilladt.
  `cloudflare` tilføjer Cloudflares net (listen står i `core/config.py`, tjek den ved flytningen).
- **Vi bruger ikke `CF-Connecting-IP`.** Den kan skrives af hvem som helst, der kalder hostens
  adresse direkte uden om Cloudflare. `X-Forwarded-For` læst bagfra giver det samme, sikkert.
- **Health check:** `GET /api/health` svarer `{"status": "ok"}` uden login. Den spørger ikke
  databasen, så hostens hyppige tjek ikke holder den fælles Neon-database vågen.
- Serveren sender ikke en `server`-header, så den ikke fortæller hvad vi kører.

Ved flytningen: find ud af hvilke adresser hostens proxy kalder fra (står i hostens
dokumentation), og skriv dem i `TRUSTED_PROXIES`. Tjek derefter i loggen at et forkert login
viser din egen IP, ikke proxyens.

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
- **Flere caféer pr. bruger** (fx en kæde): i dag hører en bruger til præcis én café. Flere brugere kan dele en café.
- **Betaling**: Stripe-integration til abonnement, hvis I vil automatisere fakturering.

## Hjemmesiden (stamgaest.com)

Mappen `stamgaest-website/` er den offentlige forside med privatlivspolitik. Den er ren HTML og CSS uden
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
    │   ├── routers/             # HTTP-endpoints: auth (login), reviews, generate, reply_settings, health
    │   ├── schemas/             # Tjekker og former data ind og ud
    │   ├── services/            # Claude (udkast og profil fra hjemmeside), Google Places, mail, lofter og login
    │   ├── models/              # Al SQL, én fil pr. tabel (restaurants, reviews, replies, users, sessions, password_tokens, usage, reply_settings)
    │   ├── db/
    │   │   ├── pool.py          # Forbindelsen til databasen
    │   │   ├── migrate.py       # Migrations-runner
    │   │   └── migrations/      # 001_init.sql (data), 002_auth.sql (brugere og sessioner), 003_password_tokens.sql (glemt adgangskode), 004_invitations.sql (invitation af nye kunder), 005_usage.sql (lofter og forbrug), 006_reply_profiles.sql (indstillinger for svar)
    │   └── core/                # Indstillinger, fejl, sikkerhed, grænser og "hvem er logget ind"
    ├── evals/                   # Testsæt: sammenlign udkast side om side (uv run python -m evals.compare)
    └── tests/                   # uv run pytest
```

**Vigtigt:** `.env` ligger allerede i `.gitignore` og skal blive der — den
indeholder både API-nøgler og adgangskoden til databasen.

## API-endpoints

Alle endpoints undtagen health, login, logout og glemt adgangskode kræver, at man er logget ind. Hvilken café det gælder,
ved serveren ud fra login, og den kan aldrig angives i kaldet.

| Metode | Sti | Hvad den gør |
| --- | --- | --- |
| `GET` | `/api/health` | Svarer `{"status": "ok"}`, så hosten kan se at serveren kører. Kræver ikke login. |
| `POST` | `/api/auth/login` | Logger ind (e-mail og adgangskode) og sætter cookien. |
| `POST` | `/api/auth/logout` | Sletter sessionen. |
| `GET` | `/api/auth/me` | Hvem er logget ind, og hvilken café. Siden bruger den ved opstart. |
| `POST` | `/api/auth/password` | Skifter adgangskode og logger andre enheder ud. |
| `POST` | `/api/auth/forgot-password` | Sender et link til at vælge ny adgangskode, hvis e-mailen findes. Svarer altid ens. |
| `POST` | `/api/auth/reset-password` | Vælger ny adgangskode med nøglen fra mailen og logger ud alle steder. |
| `GET` | `/api/reviews` | Din cafés oplysninger og alle dens gemte anmeldelser (henter intet fra Google). Hver anmeldelse har `newSinceLastVisit`. |
| `POST` | `/api/reviews/refresh` | Henter fra Google for **din** café, gemmer, og svarer med det der nu står i databasen. Højst en gang i minuttet. |
| `POST` | `/api/generate` | Genererer udkast med Claude for de valgte anmeldelser og gemmer dem i `replies`. |
| `POST` | `/api/reviews/{id}/send` | Gemmer den tekst der faktisk blev sendt, og markerer anmeldelsen besvaret. |
| `POST` | `/api/reviews/{id}/answered` | Markerer besvaret uden at gemme en tekst (svaret blev givet på Google). |
| `GET` | `/api/reply-settings` | Caféens indstillinger for svar, Claudes eventuelle forslag og status for profilen. |
| `PUT` | `/api/reply-settings` | Gemmer indstillingerne. At gemme fakta godkender dem. |
| `POST` | `/api/reply-settings/profile` | Starter "Lav forslag fra jeres hjemmeside" i baggrunden (svarer 202 med det samme). |
| `DELETE` | `/api/reply-settings/suggestion` | Kasserer Claudes forslag til fakta om stedet. |
| `POST` | `/api/generate/preview` | "Prøv et udkast": ét udkast med indstillingerne fra siden (også ugemte). Gemmes ikke. |
