"""Tester "skift e-mail": bekræftelseslinket, beskeden til den gamle adresse og sikkerheden omkring det.

Resend er falsk, så der sendes aldrig rigtige mails, og testene er gratis.
"""

import hashlib
import json
import re

import httpx

from tests.conftest import PASSWORD, db_execute, db_rows, login, logged_in, make_customer
from tests.test_password_reset_db import NEW_PASSWORD, forgot, reset, token_in, use_resend

NEW_EMAIL = "ny@test.dk"


def change(client, new_email=NEW_EMAIL, password=PASSWORD):
    """Beder om at skifte e-mail (som den der er logget ind)."""
    return client.post("/api/auth/email", json={"newEmail": new_email, "currentPassword": password})


def confirm(client, token):
    """Bekræfter den nye e-mail med nøglen fra mailen."""
    return client.post("/api/auth/confirm-email", json={"token": token})


def mail_body(mail: httpx.Request) -> dict:
    """Læser en sendt mail (modtager, emne og tekst)."""
    return json.loads(mail.content)


def email_token_in(mail: httpx.Request) -> str:
    """Læser nøglen ud af bekræftelseslinket i en mail."""
    text = mail_body(mail)["text"]
    match = re.search(r"https://stamgaest\.com/#skiftmail=(\S+)", text)
    assert match, text
    return match.group(1)


def current_email(client, customer) -> str:
    """Giver den e-mail der står på brugeren i databasen lige nu."""
    ((email,),) = db_rows(client, "SELECT email FROM users WHERE id = %s", (customer["user_id"],))
    return email


# ---- at bede om et skift --------------------------------------------------------


def test_the_new_address_gets_a_link_and_nothing_changes_before_it_is_opened(client):
    customer = logged_in(client)
    mails = use_resend(client)

    response = change(client)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(mails) == 1
    assert mail_body(mails[0])["to"] == [NEW_EMAIL]
    assert "#skiftmail=" in mail_body(mails[0])["text"]
    # Indtil linket er åbnet, gælder den gamle e-mail stadig.
    assert current_email(client, customer) == "ejer@test.dk"
    assert client.get("/api/auth/me").json()["email"] == "ejer@test.dk"


def test_only_a_fingerprint_of_the_key_is_stored(client):
    logged_in(client)
    mails = use_resend(client)
    change(client)
    token = email_token_in(mails[0])

    ((stored,),) = db_rows(client, "SELECT token_hash FROM email_changes")
    assert stored == hashlib.sha256(token.encode()).hexdigest()


def test_changing_email_needs_login(client):
    make_customer(client)
    assert change(client).status_code == 401
    assert db_rows(client, "SELECT count(*) FROM email_changes") == [(0,)]


def test_changing_email_needs_the_current_password(client):
    logged_in(client)
    mails = use_resend(client)

    response = change(client, password="forkert-adgangskode-1")

    assert response.status_code == 400  # ikke 401, så siden ikke tror man er logget ud
    assert response.json() == {"error": "Adgangskoden er forkert."}
    assert mails == []


def test_guessing_the_password_via_change_email_is_limited(client):
    logged_in(client)
    use_resend(client)
    for _ in range(5):
        assert change(client, password="forkert-adgangskode-1").status_code == 400
    blocked = change(client)  # selv den rigtige adgangskode afvises nu
    assert blocked.status_code == 429


def test_the_new_email_is_checked(client):
    logged_in(client)
    mails = use_resend(client)
    for bad in ["ikke-en-mail", "a@b", "to ord@test.dk", "a@@test.dk"]:
        response = change(client, new_email=bad)
        assert response.status_code == 400, bad
        assert response.json() == {"error": "Skriv en gyldig e-mailadresse."}
    assert change(client, new_email="  EJER@test.DK ").json() == {"error": "Det er allerede din e-mail."}
    for payload in [{}, {"newEmail": "x@y.dk"}, {"newEmail": "x" * 255 + "@y.dk", "currentPassword": PASSWORD}]:
        assert client.post("/api/auth/email", json=payload).status_code == 400
    assert mails == []


def test_the_new_email_is_stored_trimmed_and_in_lowercase(client):
    customer = logged_in(client)
    mails = use_resend(client)
    change(client, new_email="  Ny@Test.DK ")
    assert mail_body(mails[0])["to"] == [NEW_EMAIL]
    assert confirm(client, email_token_in(mails[0])).status_code == 200
    assert current_email(client, customer) == NEW_EMAIL


def test_an_address_that_is_taken_gets_the_same_answer_and_only_a_notice(client):
    """Ellers kunne man bruge "skift e-mail" til at gætte hvem der er kunde."""
    make_customer(client, email="optaget@test.dk", name="Anden Café", place_id="ChIJother")
    customer = logged_in(client)
    mails = use_resend(client)

    taken = change(client, new_email="optaget@test.dk")
    free = change(client, new_email=NEW_EMAIL)

    assert taken.status_code == free.status_code == 200
    assert taken.json() == free.json()
    assert mail_body(mails[0])["to"] == ["optaget@test.dk"]
    assert "skiftmail" not in mail_body(mails[0])["text"]  # intet link der kan bruges
    assert "allerede" in mail_body(mails[0])["text"]
    assert db_rows(client, "SELECT new_email FROM email_changes") == [(NEW_EMAIL,)]
    assert current_email(client, customer) == "ejer@test.dk"


def test_too_many_requests_are_stopped_so_nobody_can_flood_an_inbox(client):
    logged_in(client)
    mails = use_resend(client)
    assert [change(client).status_code for _ in range(3)] == [200, 200, 200]
    assert change(client).status_code == 429
    assert len(mails) == 3


def test_a_failing_mail_service_is_not_shown_to_the_browser(client):
    logged_in(client)
    use_resend(client, lambda request: httpx.Response(500, json={"message": "hemmelig intern fejl"}))
    response = change(client)
    assert response.status_code == 200
    assert "hemmelig" not in response.text


# ---- at bekræfte ----------------------------------------------------------------


def test_the_link_changes_the_email_and_the_old_address_is_told(client):
    customer = logged_in(client)
    mails = use_resend(client)
    change(client)

    response = confirm(client, email_token_in(mails[0]))

    assert response.status_code == 200
    assert current_email(client, customer) == NEW_EMAIL
    assert client.get("/api/auth/me").json()["email"] == NEW_EMAIL  # stadig logget ind
    notice = mail_body(mails[1])
    assert notice["to"] == ["ejer@test.dk"]
    assert NEW_EMAIL in notice["text"]
    assert "skiftmail" not in notice["text"] and "nulstil" not in notice["text"]  # intet link der giver adgang


def test_after_the_change_you_log_in_with_the_new_email_only(client):
    customer = logged_in(client)
    mails = use_resend(client)
    change(client)
    confirm(client, email_token_in(mails[0]))
    client.cookies.clear()

    assert login(client, customer).status_code == 401  # den gamle e-mail
    assert login(client, {**customer, "email": NEW_EMAIL}).status_code == 200


def test_the_link_works_without_being_logged_in(client):
    """Kunden åbner måske linket på telefonen."""
    customer = logged_in(client)
    mails = use_resend(client)
    change(client)
    client.cookies.clear()

    assert confirm(client, email_token_in(mails[0])).status_code == 200
    assert current_email(client, customer) == NEW_EMAIL


def test_a_link_works_only_once(client):
    logged_in(client)
    mails = use_resend(client)
    change(client)
    token = email_token_in(mails[0])

    assert confirm(client, token).status_code == 200
    again = confirm(client, token)
    assert again.status_code == 400
    assert again.json() == {"error": "Linket virker ikke længere. Bed om et nyt."}


def test_an_expired_link_is_refused(client):
    customer = logged_in(client)
    mails = use_resend(client)
    change(client)
    db_execute(client, "UPDATE email_changes SET expires_at = now() - interval '1 minute'")

    assert confirm(client, email_token_in(mails[0])).status_code == 400
    assert current_email(client, customer) == "ejer@test.dk"


def test_a_new_request_makes_the_old_link_useless(client):
    customer = logged_in(client)
    mails = use_resend(client)
    change(client, new_email="foerste@test.dk")
    change(client, new_email="anden@test.dk")

    assert confirm(client, email_token_in(mails[0])).status_code == 400
    assert confirm(client, email_token_in(mails[1])).status_code == 200
    assert current_email(client, customer) == "anden@test.dk"


def test_unknown_expired_and_used_links_give_the_same_answer(client):
    logged_in(client)
    mails = use_resend(client)
    change(client)
    token = email_token_in(mails[0])
    confirm(client, token)

    answers = {confirm(client, t).text for t in [token, "findes-ikke", "x" * 43]}
    assert len(answers) == 1


def test_a_deactivated_user_cannot_use_the_link(client):
    customer = logged_in(client)
    mails = use_resend(client)
    change(client)
    db_execute(client, "UPDATE users SET is_active = false WHERE id = %s", (customer["user_id"],))

    assert confirm(client, email_token_in(mails[0])).status_code == 400
    assert current_email(client, customer) == "ejer@test.dk"


def test_if_the_address_was_taken_meanwhile_nothing_changes_and_the_link_is_kept(client):
    customer = logged_in(client)
    mails = use_resend(client)
    change(client)
    token = email_token_in(mails[0])
    make_customer(client, email=NEW_EMAIL, name="Anden Café", place_id="ChIJother")

    response = confirm(client, token)

    assert response.status_code == 400
    assert response.json() == {"error": "Den e-mail bruges allerede af en anden konto."}
    assert current_email(client, customer) == "ejer@test.dk"
    assert db_rows(client, "SELECT used_at IS NULL FROM email_changes") == [(True,)]


def test_guessing_links_is_limited(client):
    use_resend(client)
    for _ in range(20):
        assert confirm(client, "gaet-" + "x" * 30).status_code == 400
    assert confirm(client, "gaet-" + "x" * 30).status_code == 429


def test_confirm_input_is_validated(client):
    for payload in [{}, {"token": ""}, {"token": "x" * 201}]:
        assert client.post("/api/auth/confirm-email", json=payload).status_code == 400


# ---- samspil med adgangskoden ---------------------------------------------------


def test_old_reset_links_stop_working_when_the_email_changes(client):
    """De blev sendt til den gamle adresse, som kunden måske ikke har mere."""
    customer = logged_in(client)
    mails = use_resend(client)
    forgot(client)
    reset_token = token_in(mails[0])
    change(client)
    confirm(client, email_token_in(mails[1]))

    assert reset(client, reset_token).status_code == 400
    client.cookies.clear()
    assert login(client, {**customer, "email": NEW_EMAIL}).status_code == 200  # adgangskoden er uændret


def test_resetting_the_password_cancels_a_pending_email_change(client):
    """Tager den rigtige ejer kontoen tilbage, må en andens skift af e-mail ikke kunne fuldføres."""
    customer = logged_in(client)
    mails = use_resend(client)
    change(client)
    email_token = email_token_in(mails[0])
    forgot(client)
    assert reset(client, token_in(mails[1])).status_code == 200

    assert confirm(client, email_token).status_code == 400
    assert current_email(client, customer) == "ejer@test.dk"


def test_changing_the_password_cancels_a_pending_email_change(client):
    customer = logged_in(client)
    mails = use_resend(client)
    change(client)
    response = client.post(
        "/api/auth/password", json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD}
    )
    assert response.status_code == 200

    assert confirm(client, email_token_in(mails[0])).status_code == 400
    assert current_email(client, customer) == "ejer@test.dk"
