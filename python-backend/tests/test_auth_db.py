"""Tester login, sessioner og adgangskoder mod en rigtig database (et midlertidigt testschema)."""

import dataclasses
import hashlib

from tests.conftest import (
    PASSWORD,
    db_execute,
    db_rows,
    login,
    logged_in,
    make_customer,
    token_of,
    with_token,
)


def whoami(client, token):
    return with_token(client, "GET", "/api/auth/me", token)


# ---- almindeligt login -----------------------------------------------------------


def test_login_shows_who_you_are_and_never_the_password_or_hash(client):
    customer = make_customer(client)
    response = login(client, customer)
    assert response.status_code == 200
    assert response.json() == {
        "email": "ejer@test.dk",
        "restaurantId": str(customer["restaurant_id"]),
        "businessName": "Test Café",
    }
    assert PASSWORD not in response.text and "argon2" not in response.text
    assert client.get("/api/auth/me").json()["email"] == "ejer@test.dk"


def test_login_cookie_cannot_be_read_by_javascript_and_is_not_sent_cross_site(client):
    response = login(client, make_customer(client))
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Max-Age=604800" in cookie  # 7 dage
    assert "Secure" not in cookie  # lokalt over http; se næste test


def test_cookie_is_https_only_when_configured_for_a_real_server(client):
    original = client.app.state.settings
    client.app.state.settings = dataclasses.replace(original, cookie_secure=True)
    try:
        response = login(client, make_customer(client))
        assert "Secure" in response.headers["set-cookie"]
    finally:
        client.app.state.settings = original


def test_email_is_matched_ignoring_case_and_spaces(client):
    make_customer(client, email="Ejer@Test.dk")
    response = client.post("/api/auth/login", json={"email": "  EJER@test.DK ", "password": PASSWORD})
    assert response.status_code == 200


def test_wrong_password_and_unknown_email_look_exactly_the_same(client):
    customer = make_customer(client)
    wrong = login(client, customer, "forkert-adgangskode")
    unknown = client.post("/api/auth/login", json={"email": "findes-ikke@test.dk", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"error": "Forkert e-mail eller adgangskode."}
    assert "set-cookie" not in wrong.headers and "set-cookie" not in unknown.headers


def test_login_input_is_validated(client):
    for payload in [{}, {"email": "a@b.dk"}, {"email": "", "password": "x"}, {"email": "a@b.dk", "password": "x" * 129}]:
        assert client.post("/api/auth/login", json=payload).status_code == 400


# ---- sessioner -------------------------------------------------------------------


def test_only_a_fingerprint_of_the_session_key_is_stored(client):
    response = login(client, make_customer(client))
    token = token_of(response)
    ((stored,),) = db_rows(client, "SELECT token_hash FROM sessions")
    assert stored != token
    assert token not in stored
    assert stored == hashlib.sha256(token.encode()).hexdigest()


def test_password_is_stored_as_an_argon2_hash_never_as_text(client):
    make_customer(client)
    ((stored,),) = db_rows(client, "SELECT password_hash FROM users")
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


def test_logout_kills_the_session_even_if_someone_kept_the_key(client):
    token = token_of(login(client, make_customer(client)))
    assert whoami(client, token).status_code == 200

    assert client.post("/api/auth/logout").json() == {"ok": True}

    assert whoami(client, token).status_code == 401  # nøglen virker ikke mere
    assert db_rows(client, "SELECT count(*) FROM sessions") == [(0,)]
    assert client.get("/api/reviews").status_code == 401


def test_an_expired_session_is_rejected(client):
    token = token_of(login(client, make_customer(client)))
    db_execute(client, "UPDATE sessions SET expires_at = now() - interval '1 minute'")
    response = whoami(client, token)
    assert response.status_code == 401
    assert "udløbet" in response.json()["error"]


def test_a_made_up_session_key_is_rejected(client):
    make_customer(client)
    assert whoami(client, "en-noegle-jeg-selv-har-fundet-paa").status_code == 401


def test_expired_sessions_are_cleaned_up_at_the_next_login(client):
    customer = make_customer(client)
    login(client, customer)
    db_execute(client, "UPDATE sessions SET expires_at = now() - interval '1 day'")
    client.cookies.clear()
    login(client, customer)
    assert db_rows(client, "SELECT count(*) FROM sessions WHERE expires_at <= now()") == [(0,)]


def test_a_chosen_session_key_is_never_accepted_session_fixation(client):
    customer = make_customer(client)
    response = client.post(
        "/api/auth/login",
        json={"email": customer["email"], "password": PASSWORD},
        headers={"Cookie": "session=angriberens-noegle"},
    )
    assert token_of(response) != "angriberens-noegle"
    assert whoami(client, "angriberens-noegle").status_code == 401


def test_logging_in_again_replaces_the_old_session(client):
    customer = make_customer(client)
    first = token_of(login(client, customer))
    second = token_of(login(client, customer))  # cookien fra første login sendes med
    assert first != second
    assert whoami(client, first).status_code == 401
    assert whoami(client, second).status_code == 200


def test_a_deactivated_user_is_logged_out_at_once_and_cannot_log_back_in(client):
    customer = make_customer(client)
    token = token_of(login(client, customer))
    db_execute(client, "UPDATE users SET is_active = false")
    assert whoami(client, token).status_code == 401
    assert login(client, customer).status_code == 401


# ---- gætte-angreb ----------------------------------------------------------------


def test_five_wrong_passwords_lock_the_account_even_for_the_right_password(client):
    customer = make_customer(client)
    for _ in range(5):
        assert login(client, customer, "forkert-adgangskode").status_code == 401

    blocked = login(client, customer)  # nu med den RIGTIGE adgangskode
    assert blocked.status_code == 429
    assert "For mange forsøg" in blocked.json()["error"]

    other = make_customer(client, email="anden@test.dk", name="Anden café", place_id="ChIJanden")
    assert login(client, other).status_code == 200  # andre påvirkes ikke


def test_the_lock_is_lifted_when_the_time_has_passed(client):
    customer = make_customer(client)
    for _ in range(5):
        login(client, customer, "forkert-adgangskode")
    assert login(client, customer).status_code == 429
    client.app.state.limits.reset_all()  # står i stedet for at vente 15 minutter
    assert login(client, customer).status_code == 200


def test_a_successful_login_resets_the_counter(client):
    customer = make_customer(client)
    for _ in range(4):
        login(client, customer, "forkert-adgangskode")
    assert login(client, customer).status_code == 200
    for _ in range(4):
        login(client, customer, "forkert-adgangskode")
    assert login(client, customer).status_code == 200  # 4 + 4 fejl, men aldrig 5 i træk


def test_guessing_across_many_emails_from_one_address_is_stopped_too(client):
    for i in range(20):
        assert client.post("/api/auth/login", json={"email": f"nr{i}@test.dk", "password": "gæt-gæt-gæt"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"email": "nr99@test.dk", "password": "gæt-gæt-gæt"})
    assert blocked.status_code == 429


def test_unknown_emails_are_locked_the_same_way_so_lockout_reveals_nothing(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "findes-ikke@test.dk", "password": "gæt-gæt-gæt"})
    assert client.post("/api/auth/login", json={"email": "findes-ikke@test.dk", "password": "gæt-gæt-gæt"}).status_code == 429


# ---- fremmede sider --------------------------------------------------------------


def test_login_from_another_website_is_refused(client):
    customer = make_customer(client)
    response = client.post(
        "/api/auth/login",
        json={"email": customer["email"], "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_logged_in_user_cannot_be_made_to_act_from_another_website(client):
    logged_in(client)
    for path in ["/api/reviews/refresh", "/api/generate", "/api/auth/logout", "/api/auth/password"]:
        response = client.post(path, json={}, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403, path


# ---- skift af adgangskode --------------------------------------------------------

NEW_PASSWORD = "en-helt-ny-adgangskode-7"


def change(client, current=PASSWORD, new=NEW_PASSWORD):
    return client.post("/api/auth/password", json={"currentPassword": current, "newPassword": new})


def test_changing_password_works_and_the_old_one_stops_working(client):
    customer = logged_in(client)
    assert change(client).json() == {"ok": True}

    client.cookies.clear()
    assert login(client, customer).status_code == 401
    assert login(client, customer, NEW_PASSWORD).status_code == 200


def test_changing_password_logs_out_all_other_devices_but_not_this_one(client):
    customer = make_customer(client)
    phone = token_of(login(client, customer))
    client.cookies.clear()  # "en anden enhed"
    laptop = token_of(login(client, customer))
    assert whoami(client, phone).status_code == 200

    assert with_token(
        client, "POST", "/api/auth/password", laptop,
        json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
    ).status_code == 200

    assert whoami(client, phone).status_code == 401  # den anden enhed er logget ud
    assert whoami(client, laptop).status_code == 200  # den vi skiftede fra, er ikke


def test_changing_password_needs_the_current_one_and_is_not_a_login_error(client):
    logged_in(client)
    response = change(client, current="forkert-adgangskode")
    assert response.status_code == 400  # ikke 401, ellers ville siden tro man var logget ud
    assert "nuværende adgangskode" in response.json()["error"]
    assert client.get("/api/auth/me").status_code == 200  # stadig logget ind


def test_new_password_must_be_good_enough_and_different(client):
    logged_in(client)
    assert change(client, new="kort").status_code == 400
    assert change(client, new="aaaaaaaaaaaa").status_code == 400
    same = change(client, new=PASSWORD)
    assert same.status_code == 400 and "en anden" in same.json()["error"]


def test_guessing_the_current_password_via_change_password_is_limited_too(client):
    logged_in(client)
    for _ in range(5):
        assert change(client, current="forkert-adgangskode").status_code == 400
    assert change(client).status_code == 429  # selv med den rigtige
