-- 010_review_topics: emner og stemning pr. anmeldelse, til statistikken.
--
-- Kun TILFØJELSER: én ny tabel, én ny kolonne og en udvidelse af tilladte værdier i
-- forbrugsloggen. Ingen eksisterende data ændres eller slettes.

-- Claude mærker hver anmeldelse ÉN gang med emner fra en fast liste (fx "ventetid") og
-- stemningen for hvert emne. Statistikken er så almindelig SQL over denne tabel. Det holder
-- også, når vi en dag får alle anmeldelser (tusindvis) via Business Profile API: kun nye
-- anmeldelser koster noget, og vi kan se emner over tid.
CREATE TABLE review_topics (
  review_id        UUID PRIMARY KEY REFERENCES reviews(id) ON DELETE CASCADE,
  -- Gentaget fra reviews, så hvert opslag kan filtrere på caféen direkte i SQL'en.
  restaurant_id    UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  -- Fx {"ventetid": "negativ", "drikke": "positiv"}. Tomt objekt = ingen af emnerne nævnes.
  -- Nøgler og værdier tjekkes i koden (topics_service.TOPICS og SENTIMENTS).
  topics           JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(topics) = 'object'),
  -- Hvilken udgave af emnelisten mærkningen er lavet med. Ændres listen, kan de gamle
  -- mærkes igen målrettet i stedet for at starte forfra.
  taxonomy_version SMALLINT NOT NULL,
  -- md5 af den tekst, der blev mærket. Retter anmelderen sin tekst på Google, passer den ikke
  -- længere, og anmeldelsen mærkes igen.
  text_hash        TEXT NOT NULL,
  model            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX review_topics_restaurant_idx ON review_topics (restaurant_id);

CREATE TRIGGER review_topics_set_updated_at BEFORE UPDATE ON review_topics
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Hvornår mærkningen for caféen sidst blev startet. Sat = den kører (eller gik i stå, hvis det
-- er længe siden). Bruges så to faner eller to hentninger ikke starter den samtidig.
ALTER TABLE restaurants ADD COLUMN topics_started_at TIMESTAMPTZ;

-- Mærkningen er et betalt kald til Claude, så den skal under lofterne.
ALTER TABLE usage_events DROP CONSTRAINT usage_events_kind_check;
ALTER TABLE usage_events ADD CONSTRAINT usage_events_kind_check
  CHECK (kind IN ('claude_draft', 'places_fetch', 'claude_profile', 'claude_topics'));
