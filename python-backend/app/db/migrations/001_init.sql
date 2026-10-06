-- 001_init: restauranter, anmeldelser og svar.
--
-- Bevidst holdt i ren SQL (ikke en ORM), så skemaet tilhører databasen og ikke
-- Node-koden. Skifter vi senere til Python-backenden, kan SQLAlchemy pege på
-- præcis de samme tabeller uden at skemaet skal skrives om.

-- Hver restaurant får sit eget interne UUID. Vi bruger IKKE Googles place_id som
-- primærnøgle: en restaurant kan oprettes før den er koblet til Google, kan skifte
-- place_id, og place_id'et er en ekstern værdi vi ikke selv kontrollerer.
CREATE TABLE restaurants (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  google_place_id     TEXT UNIQUE,
  name                TEXT NOT NULL,
  business_type       TEXT,
  address             TEXT,
  -- Den tone ejeren normalt vil have i svarene, så den ikke skal vælges hver gang.
  default_tone        TEXT,
  -- Googles egen samlede score. Et øjebliksbillede fra sidste hentning, ikke en
  -- beregning på vores egne rækker (Google udstiller kun 5 anmeldelser pr. sted).
  google_rating       NUMERIC(2,1),
  google_rating_count INTEGER,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reviews (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  restaurant_id UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  -- Googles eget review-id (feltet "name" i Places-svaret). Nøglen til at genkende
  -- en anmeldelse vi allerede har hentet.
  external_id   TEXT NOT NULL,
  reviewer_name TEXT,
  rating        SMALLINT CHECK (rating BETWEEN 1 AND 5),
  review_text   TEXT,
  published_at  TIMESTAMPTZ,
  status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'answered')),
  answered_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Det vigtigste constraint i hele skemaet: Google returnerer de samme 5
  -- anmeldelser hver gang man henter. Uden denne ville hver "Hent anmeldelser"
  -- lave dubletter. Med den kan vi lave ON CONFLICT-upsert i stedet.
  UNIQUE (restaurant_id, external_id)
);

-- Dækker dashboardets to faner ("Nye / ubesvarede" og "Besvarede"), som altid
-- er: én restaurant + én status, nyeste først.
CREATE INDEX reviews_restaurant_status_published_idx
  ON reviews (restaurant_id, status, published_at DESC);

-- Svar ligger i egen tabel frem for en kolonne på reviews, fordi et udkast kan
-- genereres om flere gange. Det giver samtidig noget værdifuldt: draft_text vs.
-- final_text viser hvad modellen skrev kontra hvad ejeren faktisk sendte — det
-- er den bedste kilde til at forbedre prompten senere.
CREATE TABLE replies (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  review_id  UUID NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
  draft_text TEXT NOT NULL,
  final_text TEXT,
  tone       TEXT,
  model      TEXT,
  status     TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent', 'discarded')),
  sent_at    TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Til "hent det nyeste udkast for denne anmeldelse".
CREATE INDEX replies_review_created_idx ON replies (review_id, created_at DESC);

-- Holder updated_at opdateret i databasen, så ingen kaldende kode kan glemme det.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER restaurants_set_updated_at BEFORE UPDATE ON restaurants
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER reviews_set_updated_at BEFORE UPDATE ON reviews
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER replies_set_updated_at BEFORE UPDATE ON replies
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
