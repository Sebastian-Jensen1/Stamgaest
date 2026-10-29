
## Anbefalet rækkefølge

1. ~~#1 + #2 (mailservice, glemt adgangskode, invitation)~~ Færdige.
2. Indtil launch: ting der hverken kræver hosting eller Google, fx #6 (tone/stil) og #14 (forbrugsloft).
3. Et par uger før launch: #16 hosting, så #8 automatisk hentning, så #4 alarm og #3 digest.
4. Selvregistrering med manuel godkendelse (#12), bygger på #1.
5. Resten efter behov.

## Mail og adgangskode

- [x] **#1 Mailservice + "glemt adgangskode"** (virker og er testet via Cloudflare Tunnel på `app.stamgaest.com`; til drift kræver det #16)
  - Nu kører vi `reset-password` i terminalen og sender koden manuelt. Det skalerer ikke.
  - Flow: "Glemt kode" → token (hashet i DB, udløber efter ca. 1 time, kan kun bruges én gang)
    → mail med link → ny kode → alle sessioner logges ud.
  - Udbyder: Resend eller Postmark. Husk SPF/DKIM på domænet, ellers havner mails i spam.
  - Svar altid ens, uanset om mailen findes (ingen user enumeration). Rate-limit endpointet
    (`core/rate_limit.py` findes allerede).
  - Mailservicen genbruges af alle punkter herunder.
- [x] **#2 Invitation via mail**
  - I stedet for at admin-CLI'en viser en kode én gang, sender systemet kunden et
    "sæt din adgangskode"-link. Samme mekanisme som #1, så næsten gratis bagefter.
  - Bemærk: #1 + #2 løser kun adgangskoden. Kunden oprettes stadig med `create-customer`.

## Mail-baserede features (bygger på #1)

**Kan ikke bygges endnu.** #3 og #4 kræver, at nye anmeldelser opdages automatisk (#8), og det kræver en server der kører hele tiden (#16). Rækkefølge: #16 → #8 → #4 → #3.

- [ ] **#3 Ugentlig/daglig digest**: "Du har 3 nye anmeldelser, 1 er 1-stjernet", med link til at svare.
  - Kun tal og et link i mailen, ingen anmeldernavne eller tekst (de er persondata, vis dem kun efter login).
  - Kunden skal kunne vælge hyppighed eller slå den fra.
- [ ] **#4 Øjeblikkelig alarm ved dårlig anmeldelse** (1-2 stjerner). Hurtigt svar betyder mest her.
  - Kunden skal kunne slå den fra og vælge grænsen (fx kun 1 stjerne).
  - Begrænsning: Places API giver kun 5 anmeldelser pr. sted, valgt efter "relevans", ikke de nyeste.
    En ny dårlig anmeldelse kan derfor mangle. Løses først rigtigt med #7.
- [x] **#5 Skift mail når man er logget ind, og bekræftelsesmail ved ændringer.**
  - Skift adgangskode og mail ved ændret adgangskode er lavet.
  - Skift af mail er lavet (`POST /api/auth/email` og `/api/auth/confirm-email`, se README "Skift e-mail").
    Den nye adresse bekræftes via et link, før den gælder, og den gamle adresse får besked.

## Produktforbedringer

- [x] **#6 Selvbetjening af tone/stil**: kunden vælger tone (formel, afslappet, humoristisk), faste
  vendinger, ting der aldrig skal siges, og underskrift (cafénavn/ejernavn).
- [ ] **#7 Svar direkte på Google** via Business Profile API, så man slipper for copy-paste.
  Kræver Googles godkendelse, så ansøgningen bør startes tidligt.
  - **Ikke sendt endnu.** Kræver en verificeret profil der har været aktiv i 60+ dage, og Google kan
    sige nej. Byg ikke salget på funktionen, før ansøgningen er godkendt.
  - Hver café skal selv koble sin Google-konto til (OAuth). Ejes profilen af et bureau eller en
    anden konto end ejerens, bliver det bøvlet. Spørg om det ved salget.
  - **Alle anmeldelser, ikke kun 5.** En stor kunde kan have tusindvis. Det betyder:
    - Første import kører i baggrunden med en statusbesked, ikke mens ejeren venter på et klik.
    - Senere hentninger henter kun det der er ændret siden sidst (sorteret efter `updateTime`).
    - `GET /api/reviews` sender i dag alt i ét svar. Dashboardet skal deles op i sider.
    - "Nye siden sidst" tæller allerede ikke importen med (`first_import_at`), så det er klar.
  - **Filter: kun de seneste 90 dage som standard**, med "Vis ældre" og mulighed for at vælge
    perioden. Et filter i visningen, ikke en grænse ved import, så intet forsvinder. Bygges først
    med #7: i dag sender Places 5 "relevante", som kan være ældre end 90 dage, så et filter nu kunne
    give et tomt dashboard.
  - **Allerede besvarede:** hver anmeldelse kommer med ejerens svar (`reviewReply`). Hent dem, men
    gem dem som besvarede med svarteksten. De vises under "Besvarede", tæller ikke som nye og får
    aldrig et udkast. Vi skal kende dem for at genkende dem næste gang. Svarer ejeren direkte på
    Google mellem to hentninger, ser vi det først ved næste hentning.
  - **Bedre tone:** caféens egne tidligere svar er de bedste eksempler for Claude. Vælg de gode ud
    (nyere, længere, forskellige stjerner), for dårlige svar lærer Claude også. Mål forskellen med
    `evals.compare` og rettelses-måleren i stedet for at gætte.
  - **Persondata:** gem alle ubesvarede, men af de gamle besvarede kun de ca. 100 seneste, som
    bruges til tonen.
- [ ] **#8 Auto-hent nye anmeldelser** med et baggrundsjob i stedet for kun ved oprettelse.
  - Kræver #16 (serveren skal køre hele tiden). Hver hentning koster et Places-kald pr. café.
- [x] **#9 Statistik**: gennemsnit over tid, hyppige emner ("ventetid", "kaffe"), stemning.
  Claude kan trække temaer ud.
  - Lavet: egen side med score, stjerner, måneder og emner (se README "Statistik"). Claude mærker hver
    anmeldelse én gang med en fast emneliste, så det holder, når vi får alle anmeldelser med #7.
  - Senere: emner over tid, klik på et emne for at se anmeldelserne, og Batch API til den store import.

## Drift og forretning

- [ ] **#11 Betaling/abonnement** (Stripe), så kunder kan komme på uden manuel opsætning.
- [ ] **#12 Selvregistrering**: tilmeldingsside med Google Places-søgning (`places_service.py`
  kan allerede søge) og mailbekræftelse.
  - **Beslutninger der skal tages først:**
    - Hvem må hævde en café? Ellers kan hvem som helst tilmelde sig som "Café Gotland" og få
      adgang til deres anmeldelser. Mulige løsninger: manuel godkendelse, betaling først, eller
      bekræftelse via en mail på caféens eget domæne.
    - Gratis prøve eller betaling først? Hver tilmelding koster Claude- og Places-kald.
    - Spam/bots: kræver captcha (fx Cloudflare Turnstile) og rate limiting.
  - Anbefaling: start med **manuel godkendelse** (konto er "afventer", til vi godkender den),
    og skift til betaling senere.
- [ ] **#13 Fejlovervågning** (Sentry) og uptime-check, nu hvor kunder er afhængige af det.
- [x] **#14 Forbrugsloft og audit-log** på Claude-kald, så ingen kan løbe API-regningen op.
- [x] **#15 2FA** eller "log ind via mail-link" (magic link) som alternativ til adgangskoder.
  - Lavet: login via mail-link (se README "Login med et link"). 2FA er fravalgt indtil videre: det giver
    kun reel beskyttelse, hvis også "glemt adgangskode" og login-linket kræver koden, og det er mere bøvl
    for kunderne.

## Hosting

- [ ] **#16 Appen på nettet**: linket i mails (`APP_BASE_URL`) skal pege på en adresse kunder kan åbne.
  - Trin 1 (test/demo): Cloudflare Tunnel til `app.stamgaest.com` fra egen computer, gratis. Se QUICKSTART.
  - Trin 2 (drift): betalt host, `COOKIE_SECURE=1`, `ALLOWED_HOSTS`, og IP-grænserne skal løses bag
    en proxy (se README, "Sikkerhed").
  - Beslutning taget: dashboardet på `app.stamgaest.com`, hjemmesiden forbliver på `stamgaest.com`.
  - **Beslutning taget: vi venter med at betale for hosting til et par uger før launch** (launch forventes
    om 1-2 måneder fra oktober 2026). Ikke samme dag, så der er tid til at fange problemer.
  - **Foretrukket host: Render, Starter-plan i Frankfurt** (ca. 7 USD/md., tjek prisen når vi vælger).
    Samme region som Neon, Python 3.14 og uv er indbygget (ingen Dockerfile), offentlig DPA og
    EU-US Data Privacy Framework. Alternativer: Fly.io (Frankfurt, mere teknisk), Railway (Amsterdam,
    DPA ikke fundet).
  - Kodeændringer der kan laves gratis før (gør selve flytningen hurtig). **Færdige** (`HOST`,
    `TRUSTED_PROXIES` og `/api/health`, se README "Hosting"):
    - Serveren lytter kun på `127.0.0.1` (`app/__main__.py`). Skal kunne lytte på `0.0.0.0` og hostens port
      via en indstilling. Kommentaren dér ("der er intet login") er forældet.
    - Rigtig IP bag proxy (`X-Forwarded-For`/`CF-Connecting-IP`, kun fra kendte proxyer).
    - `/api/health`-endpoint til hosten.
  - Ved flytningen: hemmeligheder i hostens dashboard (ikke `.env`), CNAME for `app.stamgaest.com` i
    Cloudflare peger på hosten i stedet for tunnelen, SSL "Full (strict)". Mail-records og hjemmesiden
    er uændrede.
  - Husk: hosten deployer typisk ved push til `main`, så "merge og push" betyder "går live".
  - GDPR: hosten bliver databehandler, så DPA og EU-region er krav.
