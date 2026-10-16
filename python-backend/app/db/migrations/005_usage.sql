-- 005_usage: lofter for betalte kald (Claude og Google), log over forbrug og nødbremse pr. café.
--
-- Kun TILFØJELSER: to nye tabeller og én ny kolonne. Alle caféer får ai_enabled = true, så
-- alt virker som før.

-- Én række pr. betalt kald. Rækken oprettes FØR kaldet ("reserved"), så to samtidige kald ikke
-- begge kan snige sig forbi et loft, og opdateres bagefter:
--   done    = kaldet lykkedes
--   failed  = kaldet fejlede. TÆLLER STADIG MED, for et fejlet kald kan koste penge, og ellers
--             kunne man prøve igen og igen uden at ramme noget loft
--   skipped = kaldet blev aldrig sendt (fx de sidste i en serie, efter at et tidligere fejlede).
--             Tæller ikke med.
-- Der står med vilje INGEN anmeldelsestekst eller navne her, kun id'er og tal.
CREATE TABLE usage_events (
  id            BIGSERIAL PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('claude_draft', 'places_fetch')),
  -- SET NULL: slettes en café eller bruger, bliver forbruget stående i regnskabet.
  restaurant_id UUID REFERENCES restaurants(id) ON DELETE SET NULL,
  user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
  status        TEXT NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved', 'done', 'failed', 'skipped')),
  model         TEXT,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX usage_events_kind_created_idx ON usage_events (kind, created_at);
CREATE INDEX usage_events_restaurant_idx ON usage_events (restaurant_id, kind, created_at);

-- Hvilke advarselsmails der er sendt, så der højst går én mail pr. loft, niveau og dag.
CREATE TABLE usage_alerts (
  kind  TEXT NOT NULL,
  scope TEXT NOT NULL,   -- fx "cafe_day:<café-id>" eller "all_month"
  level INTEGER NOT NULL, -- 80 eller 100 (procent af loftet)
  day   DATE NOT NULL,
  PRIMARY KEY (kind, scope, level, day)
);

-- Nødbremse pr. café: false = ingen udkast fra Claude for denne café. Kunden kan stadig se og
-- besvare anmeldelser selv.
ALTER TABLE restaurants ADD COLUMN ai_enabled BOOLEAN NOT NULL DEFAULT true;
