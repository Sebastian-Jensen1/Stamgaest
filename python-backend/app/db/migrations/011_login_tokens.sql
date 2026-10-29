-- 011_login_tokens: log ind med et link på mail i stedet for adgangskoden.
--
-- Kun TILFØJELSER: én ny tabel. Ingen eksisterende data eller kolonner ændres.

-- Som password_tokens (003): kun et FINGERAFTRYK (SHA-256) af nøglen gemmes, så rækkerne ikke
-- kan bruges til at logge ind, hvis nogen læser databasen. En tabel for sig, så et login-link
-- og et "glemt adgangskode"-link ikke gør hinanden ubrugelige.
CREATE TABLE login_tokens (
  token_hash TEXT PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Linket virker kun kort (sættes af koden, ca. et kvarter), for det logger direkte ind.
  expires_at TIMESTAMPTZ NOT NULL,
  -- Sættes når linket er brugt. Så kan det aldrig bruges to gange.
  used_at    TIMESTAMPTZ
);

CREATE INDEX login_tokens_user_idx ON login_tokens (user_id);
CREATE INDEX login_tokens_expires_idx ON login_tokens (expires_at);
