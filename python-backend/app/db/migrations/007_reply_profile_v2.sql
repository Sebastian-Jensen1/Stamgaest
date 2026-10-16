-- 007_reply_profile_v2: ny indstillingsside. Flere længder, tone med egne ord og fakta om stedet
-- delt op i felter (koncept, retter og drikke, kendetegn, stemning) i stedet for én tekstklump.
--
-- Kun TILFØJELSER og en udvidelse af tilladte værdier. Ingen data slettes. Kolonnerne
-- address_form, phrases, facts og facts_draft fra 006 bruges ikke længere, men bliver stående.
-- Den gamle fakta-tekst flyttes automatisk over i den nye struktur, første gang den læses.

-- Flere valg for længden. 'auto' = Claude tilpasser længden til anmeldelsen (ny standard).
-- Rækker med 'kort' eller 'mellem' er stadig gyldige.
ALTER TABLE reply_settings DROP CONSTRAINT reply_settings_reply_length_check;
ALTER TABLE reply_settings ADD CONSTRAINT reply_settings_reply_length_check
  CHECK (reply_length IN ('auto', 'meget_kort', 'kort', 'mellem', 'lang'));
ALTER TABLE reply_settings ALTER COLUMN reply_length SET DEFAULT 'auto';

-- Tonen med ejerens egne ord, fx "uformel og lidt fræk". Erstatter tone-menuen i dashboardet.
ALTER TABLE reply_settings ADD COLUMN tone TEXT NOT NULL DEFAULT '' CHECK (length(tone) <= 200);

-- Fakta om stedet som felter: {"concept": "", "items": [], "traits": [], "atmosphere": ""}.
-- `about` er den GODKENDTE udgave, som bruges i udkastene. `about_suggestion` er Claudes forslag
-- fra hjemmesiden, som aldrig bruges, før ejeren har valgt, hvad der skal med.
-- Loftet på størrelsen er en sikring. De præcise lofter pr. felt håndhæves i koden.
ALTER TABLE reply_settings ADD COLUMN about JSONB
  CHECK (about IS NULL OR (jsonb_typeof(about) = 'object' AND octet_length(about::text) <= 8000));
ALTER TABLE reply_settings ADD COLUMN about_suggestion JSONB
  CHECK (about_suggestion IS NULL OR (jsonb_typeof(about_suggestion) = 'object' AND octet_length(about_suggestion::text) <= 8000));

COMMENT ON COLUMN reply_settings.address_form IS 'Bruges ikke længere (007): svar skrives altid med "du".';
COMMENT ON COLUMN reply_settings.phrases IS 'Bruges ikke længere (007): stemmen læres af eksempler og tidligere svar.';
COMMENT ON COLUMN reply_settings.facts IS 'Bruges ikke længere (007): erstattet af about. Læses kun for at flytte gammel tekst over.';
COMMENT ON COLUMN reply_settings.facts_draft IS 'Bruges ikke længere (007): erstattet af about_suggestion.';
