-- 008_reviews_seen: husker hvornår hver bruger sidst var inde, så "Nye siden sidst" kan tælle de
-- anmeldelser der er kommet til siden. Pr. bruger, for to ejere af samme café er inde på
-- forskellige tidspunkter.
--
-- Kun TILFØJELSER: tre nye kolonner, og den ene udfyldes for caféer der allerede har anmeldelser.
-- Intet eksisterende bliver ændret eller slettet.

-- Sidste gang brugeren åbnede dashboardet. Rykkes frem ved hvert kald, så et besøg varer så
-- længe brugeren er aktiv.
ALTER TABLE users ADD COLUMN reviews_seen_at TIMESTAMPTZ;

-- Hvornår det FORRIGE besøg sluttede. Anmeldelser der er kommet til efter dette tidspunkt, er
-- "nye siden sidst". Den står stille under et besøg, så tallet ikke nulstilles ved genindlæsning.
ALTER TABLE users ADD COLUMN previous_reviews_seen_at TIMESTAMPTZ;

-- Hvornår caféens første hentning fra Google blev gemt. Den første hentning er en IMPORT af det
-- der allerede fandtes, ikke noget nyt. Uden den ville en ny kunde med mange anmeldelser se dem
-- alle som "Nye siden sidst" første gang.
ALTER TABLE restaurants ADD COLUMN first_import_at TIMESTAMPTZ;

-- Caféer der allerede har anmeldelser: den første hentning var, da den ældste blev gemt.
UPDATE restaurants r
   SET first_import_at = (SELECT min(created_at) FROM reviews rv WHERE rv.restaurant_id = r.id)
 WHERE first_import_at IS NULL;
