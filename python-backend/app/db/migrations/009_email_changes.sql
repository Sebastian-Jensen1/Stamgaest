-- 009_email_changes: kunden kan selv skifte e-mail, når den nye adresse er bekræftet via et link.
--
-- Kun TILFØJELSER: én ny tabel. Ingen eksisterende data eller kolonner ændres.

-- Et ønske om at skifte e-mail. Den nye adresse gælder FØRST, når linket i mailen til den nye
-- adresse er åbnet. Ellers kunne en tastefejl låse kunden ude, eller en med adgang til en åben
-- browser kunne flytte kontoen til en adresse de ikke selv kan modtage mail på.
-- Som ved password_tokens gemmes kun et FINGERAFTRYK (SHA-256) af nøglen i linket.
CREATE TABLE email_changes (
  token_hash TEXT PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- Den adresse kunden gerne vil skifte til. Den er endnu ikke bekræftet.
  new_email  TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Linket virker kun et stykke tid (sættes af koden, ca. et døgn).
  expires_at TIMESTAMPTZ NOT NULL,
  -- Sættes når linket er brugt. Så kan det aldrig bruges to gange.
  used_at    TIMESTAMPTZ
);

CREATE INDEX email_changes_user_idx ON email_changes (user_id);
CREATE INDEX email_changes_expires_idx ON email_changes (expires_at);
