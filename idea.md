# Idéer til Stamgæst

Samlet liste over ting vi kan bygge. Nummereringen bruges i samtaler ("lad os lave #1").
Sæt `[x]` når en idé er lavet.

## Anbefalet rækkefølge

1. #1 + #2 som én opgave (mailservice + token-baseret nulstilling/invitation)
2. Selvregistrering med manuel godkendelse (#12), bygger på #1
3. Mail-features (#3, #4), der giver kunderne synlig værdi
4. Resten efter behov

## Mail og adgangskode

- [ ] **#1 Mailservice + "glemt adgangskode"**
  - Nu kører vi `reset-password` i terminalen og sender koden manuelt. Det skalerer ikke.
  - Flow: "Glemt kode" → token (hashet i DB, udløber efter ca. 1 time, kan kun bruges én gang)
    → mail med link → ny kode → alle sessioner logges ud.
  - Udbyder: Resend eller Postmark. Husk SPF/DKIM på domænet, ellers havner mails i spam.
  - Svar altid ens, uanset om mailen findes (ingen user enumeration). Rate-limit endpointet
    (`core/rate_limit.py` findes allerede).
  - Mailservicen genbruges af alle punkter herunder.
- [ ] **#2 Invitation via mail**
  - I stedet for at admin-CLI'en viser en kode én gang, sender systemet kunden et
    "sæt din adgangskode"-link. Samme mekanisme som #1, så næsten gratis bagefter.
  - Bemærk: #1 + #2 løser kun adgangskoden. Kunden oprettes stadig med `create-customer`.

## Mail-baserede features (bygger på #1)

- [ ] **#3 Ugentlig/daglig digest**: "Du har 3 nye anmeldelser, 1 er 1-stjernet", med link til at svare.
- [ ] **#4 Øjeblikkelig alarm ved dårlig anmeldelse** (1-2 stjerner). Hurtigt svar betyder mest her.
- [ ] **#5 Skift adgangskode/mail når man er logget ind**, med bekræftelsesmail ved ændring.

## Produktforbedringer

- [ ] **#6 Selvbetjening af tone/stil**: kunden vælger tone (formel, afslappet, humoristisk), faste
  vendinger, ting der aldrig skal siges, og underskrift (cafénavn/ejernavn).
- [ ] **#7 Svar direkte på Google** via Business Profile API, så man slipper for copy-paste.
  Kræver Googles godkendelse, så ansøgningen bør startes tidligt.
- [ ] **#8 Auto-hent nye anmeldelser** med et baggrundsjob i stedet for kun ved oprettelse.
- [ ] **#9 Statistik**: gennemsnit over tid, hyppige emner ("ventetid", "kaffe"), stemning.
  Claude kan trække temaer ud.
- [ ] **#10 Flersproget**: svar på anmeldelsens eget sprog (turister på engelsk/tysk).

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
- [ ] **#14 Forbrugsloft og audit-log** på Claude-kald, så ingen kan løbe API-regningen op.
- [ ] **#15 2FA** eller "log ind via mail-link" (magic link) som alternativ til adgangskoder.
