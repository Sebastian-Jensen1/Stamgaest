-- 002_auth: kunder kan logge ind og ser kun deres egen café.
--
-- Kun TILFØJELSER: to nye tabeller og én ny kolonne. Ingen eksisterende data eller
-- kolonner ændres eller slettes, så alt der virkede før virker stadig.

-- Hvornår vi sidst hentede anmeldelser fra Google for stedet. Bruges til en
-- pause mellem hentninger ("Hent anmeldelser" koster penge hos Google).
ALTER TABLE restaurants ADD COLUMN reviews_fetched_at TIMESTAMPTZ;

-- En bruger er en person der kan logge ind. Hver bruger hører til PRÆCIS ÉN café, og
-- det er den kobling der afgør hvad brugeren må se. Flere personer kan høre til
-- samme café (fx ejer og souschef); det er kun en bruger med flere caféer der
-- ikke understøttes endnu.
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         TEXT NOT NULL,
  -- Aldrig selve adgangskoden. Kun en argon2-hash, som ikke kan regnes tilbage.
  password_hash TEXT NOT NULL,
  restaurant_id UUID NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  -- Slås den fra, kan brugeren ikke længere logge ind (og alle sessioner slettes).
  is_active     BOOLEAN NOT NULL DEFAULT true,
  last_login_at TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unik uden hensyn til store/små bogstaver, så "Ida@x.dk" og "ida@x.dk" er samme bruger.
CREATE UNIQUE INDEX users_email_key ON users (lower(email));
CREATE INDEX users_restaurant_idx ON users (restaurant_id);

CREATE TRIGGER users_set_updated_at BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- En session er "du er logget ind". Browseren har en tilfældig nøgle i en cookie, og
-- her gemmes kun et FINGERAFTRYK (SHA-256) af den. Læser nogen databasen, kan de
-- derfor ikke bruge rækkerne til at logge ind som andre.
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX sessions_user_idx ON sessions (user_id);
CREATE INDEX sessions_expires_idx ON sessions (expires_at);
