-- 003_password_tokens: "glemt adgangskode" via mail.
--
-- Kun TILFØJELSER: én ny tabel. Ingen eksisterende data eller kolonner ændres.

-- Et nulstillingslink indeholder en tilfældig nøgle. Her gemmes kun et FINGERAFTRYK (SHA-256)
-- af den, på samme måde som med sessioner (se 002_auth.sql). Læser nogen databasen, kan de
-- derfor ikke bruge rækkerne til at nulstille andres adgangskode.
CREATE TABLE password_tokens (
  token_hash TEXT PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Linket virker kun et stykke tid (sættes af koden, ca. en time).
  expires_at TIMESTAMPTZ NOT NULL,
  -- Sættes når linket er brugt. Så kan det aldrig bruges to gange.
  used_at    TIMESTAMPTZ
);

CREATE INDEX password_tokens_user_idx ON password_tokens (user_id);
CREATE INDEX password_tokens_expires_idx ON password_tokens (expires_at);
