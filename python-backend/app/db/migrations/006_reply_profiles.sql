-- 006_reply_profiles: skræddersyede svar pr. café (svarstil, fakta om stedet og eksempler).
--
-- Kun TILFØJELSER, og én udvidelse af en regel: forbrugsloggen får en ny slags kald
-- ("claude_profile"). Ingen eksisterende data ændres eller slettes.

-- Caféens hjemmeside, som Google oplyser den. Kun den bruges til at bygge profilen, så hverken
-- kunden eller en anmelder kan få Claude til at læse en anden side.
ALTER TABLE restaurants ADD COLUMN website_url TEXT
  CHECK (website_url IS NULL OR (length(website_url) <= 500 AND website_url ~ '^https?://'));

-- Én række pr. café. Alt her ender i prompten til Claude, så hvert felt har et loft på længden
-- (både for prisen og for at holde prompten skarp). Lofterne tjekkes også i koden.
CREATE TABLE reply_settings (
  restaurant_id     UUID PRIMARY KEY REFERENCES restaurants(id) ON DELETE CASCADE,

  -- A. Svarstil, som ejeren selv vælger.
  address_form      TEXT NOT NULL DEFAULT 'du' CHECK (address_form IN ('du', 'De')),
  reply_length      TEXT NOT NULL DEFAULT 'kort' CHECK (reply_length IN ('kort', 'mellem')),
  signature         TEXT NOT NULL DEFAULT '' CHECK (length(signature) <= 150),
  phrases           TEXT NOT NULL DEFAULT '' CHECK (length(phrases) <= 600),   -- faste vendinger
  avoid             TEXT NOT NULL DEFAULT '' CHECK (length(avoid) <= 600),     -- må aldrig skrives
  contact           TEXT NOT NULL DEFAULT '' CHECK (length(contact) <= 200),   -- hvor utilfredse henvises hen

  -- C. Eksempler på svar, ejeren selv har skrevet og er glad for.
  examples          TEXT NOT NULL DEFAULT '' CHECK (length(examples) <= 3000),

  -- B. Fakta om stedet. `facts` er den GODKENDTE udgave, som bruges i udkastene. `facts_draft`
  -- er Claudes forslag fra hjemmesiden, som ALDRIG bruges, før ejeren har godkendt det.
  facts             TEXT NOT NULL DEFAULT '' CHECK (length(facts) <= 2000),
  facts_draft       TEXT CHECK (facts_draft IS NULL OR length(facts_draft) <= 2000),
  facts_approved_at TIMESTAMPTZ,

  -- Status for "Byg profil fra hjemmesiden", som kører i baggrunden.
  profile_status     TEXT NOT NULL DEFAULT 'none'
                     CHECK (profile_status IN ('none', 'building', 'ready', 'failed')),
  profile_message    TEXT NOT NULL DEFAULT '' CHECK (length(profile_message) <= 300),
  profile_started_at TIMESTAMPTZ,

  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER reply_settings_set_updated_at BEFORE UPDATE ON reply_settings
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- At bygge en profil fra hjemmesiden er et betalt kald til Claude, så det skal under lofterne.
ALTER TABLE usage_events DROP CONSTRAINT usage_events_kind_check;
ALTER TABLE usage_events ADD CONSTRAINT usage_events_kind_check
  CHECK (kind IN ('claude_draft', 'places_fetch', 'claude_profile'));
