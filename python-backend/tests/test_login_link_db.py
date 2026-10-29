"""Tester login med et link på mail: mailen, linket og at det logger ind som den rigtige.

Resend er falsk, så der sendes aldrig rigtige mails, og testene er gratis.
"""

import hashlib
import json
import re

from tests.conftest import as_user, db_execute, db_rows, login, logged_in, make_customer
from tests.test_password_reset_db import forgot, token_in as reset_token_in, use_resend


def ask(client, email="ejer@test.dk"):
    """Beder om et login-link."""
    return client.post("/api/auth/login-link", json={"email": email})


def use(client, token):
    """Logger ind med nøglen fra mailen."""
    return client.post("/api/auth/login-link/use", json={"token": token})


def token_in(mail) -> str:
    """Læser nøglen ud af login-linket i en mail."""
    body = json.loads(mail.content)
    match = re.search(r"https://stamgaest\.com/#login=(\S+)", body["text"])
    assert match, body["text"]
    return match.group(1)


# ---- mailen ----------------------------------------------------------------------


def test_asking_for_a_link_sends_one_mail_with_a_link_that_logs_in(client):
    customer = make_customer(client)
    mails = use_resend(client)

    response = ask(client)

    assert response.status_code == 200 and response.json() == {"ok": True}
    assert len(mails) == 1
    body = json.loads(mails[0].content)
    assert body["to"] == ["ejer@test.dk"]
    assert body["subject"] == "Log ind på Stamgæst"
    assert "15 minutter" in body["text"]

    assert client.get("/api/auth/me").status_code == 401  # endnu ikke logget ind
    logged = use(client, token_in(mails[0]))
    assert logged.status_code == 200
    assert logged.json()["email"] == "ejer@test.dk"
    assert client.get("/api/auth/me").json()["restaurantId"] == str(customer["restaurant_id"])
    assert "httponly" in logged.headers["set-cookie"].lower()


def test_only_a_fingerprint_of_the_key_is_stored(client):
    make_customer(client)
    mails = use_resend(client)
    ask(client)
    token = token_in(mails[0])

    stored = db_rows(client, "SELECT token_hash FROM login_tokens")
    assert stored == [(hashlib.sha256(token.encode()).hexdigest(),)]
    assert token not in str(stored)


def test_unknown_email_gets_the_same_answer_and_no_mail(client):
    make_customer(client)
    mails = use_resend(client)
    known, unknown = ask(client), ask(client, "findes.ikke@test.dk")
    assert known.json() == unknown.json() == {"ok": True}
    assert len(mails) == 1


def test_deactivated_and_invited_users_get_no_mail(client):
    deactivated = make_customer(client, email="fra@test.dk", place_id="ChIJa")
    invited = make_customer(client, email="ny@test.dk", place_id="ChIJb")
    db_execute(client, "UPDATE users SET is_active = false WHERE id = %s", (deactivated["user_id"],))
    db_execute(client, "UPDATE users SET invite_pending = true WHERE id = %s", (invited["user_id"],))
    mails = use_resend(client)

    assert ask(client, "fra@test.dk").status_code == ask(client, "ny@test.dk").status_code == 200
    assert mails == []


def test_too_many_requests_are_stopped_also_for_unknown_emails(client):
    make_customer(client)
    mails = use_resend(client)
    for _ in range(3):
        assert ask(client).status_code == 200
    assert ask(client).status_code == 429
    assert len(mails) == 3

    for _ in range(3):
        ask(client, "findes.ikke@test.dk")
    assert ask(client, "findes.ikke@test.dk").status_code == 429


def test_login_links_and_forgot_password_have_separate_limits(client):
    make_customer(client)
    mails = use_resend(client)
    for _ in range(3):
        ask(client)
    assert forgot(client).status_code == 200  # "glemt adgangskode" virker stadig
    assert len(mails) == 4


def test_input_is_validated(client):
    assert client.post("/api/auth/login-link", json={}).status_code == 400
    assert client.post("/api/auth/login-link", json={"email": "x" * 300}).status_code == 400
    assert client.post("/api/auth/login-link/use", json={}).status_code == 400
    assert use(client, "x" * 500).status_code == 400


# ---- linket ----------------------------------------------------------------------


def test_a_link_works_only_once(client):
    make_customer(client)
    mails = use_resend(client)
    ask(client)
    token = token_in(mails[0])

    assert use(client, token).status_code == 200
    client.cookies.clear()
    second = use(client, token)
    assert second.status_code == 400
    assert second.json() == {"error": "Linket virker ikke længere. Bed om et nyt."}
    assert "set-cookie" not in second.headers


def test_an_expired_link_is_refused(client):
    make_customer(client)
    mails = use_resend(client)
    ask(client)
    db_execute(client, "UPDATE login_tokens SET expires_at = now() - interval '1 second'")
    assert use(client, token_in(mails[0])).status_code == 400


def test_unknown_expired_and_used_links_give_the_same_answer(client):
    make_customer(client)
    mails = use_resend(client)
    ask(client)
    token = token_in(mails[0])
    use(client, token)
    client.cookies.clear()

    answers = {use(client, t).json()["error"] for t in (token, "helt-ukendt-noegle")}
    assert answers == {"Linket virker ikke længere. Bed om et nyt."}


def test_a_new_request_makes_the_old_link_useless(client):
    make_customer(client)
    mails = use_resend(client)
    ask(client)
    ask(client)
    assert use(client, token_in(mails[0])).status_code == 400
    assert use(client, token_in(mails[1])).status_code == 200


def test_a_login_link_and_a_reset_link_do_not_cancel_each_other(client):
    make_customer(client)
    mails = use_resend(client)
    forgot(client)
    ask(client)
    assert db_rows(client, "SELECT count(*) FROM password_tokens") == [(1,)]
    assert use(client, token_in(mails[1])).status_code == 200
    assert client.post(
        "/api/auth/reset-password", json={"token": reset_token_in(mails[0]), "newPassword": "en-helt-ny-adgangskode-77"}
    ).status_code == 200


def test_a_deactivated_user_cannot_use_an_old_link(client):
    customer = make_customer(client)
    mails = use_resend(client)
    ask(client)
    db_execute(client, "UPDATE users SET is_active = false WHERE id = %s", (customer["user_id"],))
    assert use(client, token_in(mails[0])).status_code == 400
    assert client.get("/api/auth/me").status_code == 401


def test_a_link_only_logs_in_the_user_it_was_sent_to(client):
    a = make_customer(client, email="a@test.dk", name="Café A", place_id="ChIJa")
    make_customer(client, email="b@test.dk", name="Café B", place_id="ChIJb")
    mails = use_resend(client)
    as_user(client, a)  # logget ind som A i forvejen
    ask(client, "b@test.dk")

    response = use(client, token_in(mails[0]))

    assert response.json()["businessName"] == "Café B"
    me = client.get("/api/auth/me").json()
    assert me["email"] == "b@test.dk"
    # A's gamle session i denne browser er slettet, ligesom ved et almindeligt login.
    assert db_rows(client, "SELECT count(*) FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.email = 'a@test.dk'") == [(0,)]


def test_a_locked_out_customer_can_log_in_with_a_link(client):
    customer = make_customer(client)
    mails = use_resend(client)
    for _ in range(5):
        login(client, customer, password="forkert-adgangskode")
    assert login(client, customer).status_code == 429

    ask(client)
    assert use(client, token_in(mails[0])).status_code == 200
    client.cookies.clear()
    assert login(client, customer).status_code == 200  # spærringen er ophævet


def test_wrong_links_are_rate_limited_per_ip(client):
    for _ in range(20):
        assert use(client, "forkert-noegle").status_code == 400
    assert use(client, "forkert-noegle").status_code == 429


def test_resetting_the_password_or_changing_email_cancels_waiting_login_links(client):
    make_customer(client)
    mails = use_resend(client)
    ask(client)
    forgot(client)
    client.post(
        "/api/auth/reset-password", json={"token": reset_token_in(mails[1]), "newPassword": "en-helt-ny-adgangskode-77"}
    )
    assert use(client, token_in(mails[0])).status_code == 400
    assert db_rows(client, "SELECT count(*) FROM login_tokens") == [(0,)]


def test_a_confirmed_email_change_cancels_links_sent_to_the_old_address(client):
    customer = logged_in(client)
    mails = use_resend(client)
    ask(client)
    client.post("/api/auth/email", json={"newEmail": "ny@test.dk", "currentPassword": customer["password"]})
    confirm = re.search(r"#skiftmail=(\S+)", json.loads(mails[1].content)["text"]).group(1)
    assert client.post("/api/auth/confirm-email", json={"token": confirm}).status_code == 200

    client.cookies.clear()
    assert use(client, token_in(mails[0])).status_code == 400
