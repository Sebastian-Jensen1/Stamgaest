-- 004_invitations: nye kunder får en invitation på mail i stedet for en adgangskode.
--
-- Kun TILFØJELSER: én ny kolonne. Alle eksisterende brugere får false, så deres login virker
-- præcis som før.

-- true = kunden er inviteret, men har endnu ikke valgt en adgangskode. Kontoen har en tilfældig
-- adgangskode som ingen kender. Så længe den er true, kan "glemt adgangskode" IKKE bruges: kun
-- vi kan sende (eller trække tilbage) invitationen, så en fejlindtastet e-mail ikke kan overtage
-- en café ad omvejen. Sættes til false når kunden har valgt en adgangskode.
ALTER TABLE users ADD COLUMN invite_pending BOOLEAN NOT NULL DEFAULT false;
