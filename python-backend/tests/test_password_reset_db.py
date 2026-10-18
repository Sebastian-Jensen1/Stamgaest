"""Tester "glemt adgangskode": mailen, linket og valget af ny adgangskode.

Resend er falsk, så der sendes aldrig rigtige mails, og testene er gratis.
"""

import dataclasses
import hashlib
import json
import re

import httpx

from tests.conftest import PASSWORD, db_execute, db_rows, login, logged_in, make_customer

NEW_PASSWORD = "en-helt-ny-adgangskode-77"


def use_resend(client, handler=None, *, api_key="test-resend-key"):
    """Lader appen sende mails til en falsk Resend. Returnerer listen af mails der blev sendt."""
    mails: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        mails.append(request)
        return handler(request) if handler else httpx.Response(200, json={"id": "mail-1"})

    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    client.app.state.settings = dataclasses.replace(
        client.app.state.settings,
        resend_api_key=api_key,
        mail_from="Stamgæst <noreply@mail.stamgaest.com>",
        app_base_url="https://stamgaest.com",
    )
    return mails


def forgot(client, email="ejer@test.dk"):
    """Beder om et link til at vælge ny adgangskode."""
    return client.post("/api/auth/forgot-password", json={"email": email})


def reset(client, token, password=NEW_PASSWORD):
    """Vælger ny adgangskode med nøglen fra mailen."""
    return client.post("/api/auth/reset-password", json={"token": token, "newPassword": password})


def token_in(mail: httpx.Request) -> str:
    """Læser nøglen ud af linket i en mail."""
    body = json.loads(mail.content)
    match = re.search(r"https://stamgaest\.com/#nulstil=(\S+)", body["text"])
    assert match, body["text"]
    return match.group(1)


# ---- mailen ----------------------------------------------------------------------


def test_forgot_password_sends_one_mail_with_a_working_link(client):
    make_customer(client)
    mails = use_resend(client)

    response = forgot(client)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(mails) == 1
    assert mails[0].url == "https://api.resend.com/emails"
    assert mails[0].headers["Authorization"] == "Bearer test-resend-key"
    body = json.loads(mails[0].content)
    assert body["to"] == ["ejer@test.dk"]
    assert body["from"] == "Stamgæst <noreply@mail.stamgaest.com>"
    assert "nulstil=" in body["text"]


def test_only_a_fingerprint_of_the_key_is_stored(client):
    make_customer(client)
    mails = use_resend(client)
    forgot(client)
    token = token_in(mails[0])

    ((stored,),) = db_rows(client, "SELECT token_hash FROM password_tokens")
    assert stored != token and token not in stored
    assert stored == hashlib.sha256(token.encode()).hexdigest()


def test_unknown_email_gets_the_same_answer_and_no_mail(client):
    make_customer(client)
    mails = use_resend(client)

    known = forgot(client)
    unknown = forgot(client, "findes-ikke@test.dk")

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert len(mails) == 1  # kun til den der findes
    assert db_rows(client, "SELECT count(*) FROM password_tokens") == [(1,)]


def test_deactivated_user_gets_no_mail(client):
    customer = make_customer(client)
    db_execute(client, "UPDATE users SET is_active = false WHERE id = %s", (customer["user_id"],))
    mails = use_resend(client)

    assert forgot(client).status_code == 200
    assert mails == []


def test_email_is_matched_ignoring_case_and_spaces(client):
    make_customer(client, email="Ejer@Test.dk")
    mails = use_resend(client)
    assert forgot(client, "  EJER@test.DK ").status_code == 200
    assert len(mails) == 1


def test_too_many_requests_are_stopped_and_no_extra_mails_go_out(client):
    make_customer(client)
    mails = use_resend(client)

    assert [forgot(client).status_code for _ in range(3)] == [200, 200, 200]
    blocked = forgot(client)

    assert blocked.status_code == 429
    assert "For mange forsøg" in blocked.json()["error"]
    assert len(mails) == 3


def test_the_limit_also_counts_emails_that_do_not_exist(client):
    """Ellers kunne grænsen afsløre hvem der er kunde."""
    use_resend(client)
    for _ in range(3):
        assert forgot(client, "findes-ikke@test.dk").status_code == 200
    assert forgot(client, "findes-ikke@test.dk").status_code == 429


def test_a_failing_mail_service_is_not_shown_to_the_browser(client):
    make_customer(client)
    use_resend(client, lambda request: httpx.Response(500, json={"message": "hemmelig intern fejl"}))

    response = forgot(client)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "hemmelig" not in response.text


def test_without_an_api_key_nothing_is_sent_but_the_answer_is_the_same(client):
    make_customer(client)
    mails = use_resend(client, api_key=None)
    response = forgot(client)
    assert response.status_code == 200
    assert mails == []


def test_forgot_input_is_validated(client):
    for payload in [{}, {"email": ""}, {"email": "x" * 255}]:
        assert client.post("/api/auth/forgot-password", json=payload).status_code == 400


# ---- at vælge ny adgangskode -----------------------------------------------------


def test_the_link_sets_a_new_password(client):
    customer = make_customer(client)
    mails = use_resend(client)
    forgot(client)

    response = reset(client, token_in(mails[0]))

    assert response.status_code == 200
    assert login(client, customer, NEW_PASSWORD).status_code == 200
    client.cookies.clear()
    assert login(client, customer, PASSWORD).status_code == 401  # den gamle virker ikke mere


def test_a_link_works_only_once(client):
    make_customer(client)
    mails = use_resend(client)
    forgot(client)
    token = token_in(mails[0])

    assert reset(client, token).status_code == 200
    again = reset(client, token, "endnu-en-adgangskode-55")

    assert again.status_code == 400
    assert again.json() == {"error": "Linket virker ikke længere. Bed om et nyt."}


def test_an_expired_link_is_refused(client):
    customer = make_customer(client)
    mails = use_resend(client)
    forgot(client)
    db_execute(client, "UPDATE password_tokens SET expires_at = now() - interval '1 minute'")

    assert reset(client, token_in(mails[0])).status_code == 400
    assert login(client, customer).status_code == 200  # adgangskoden er uændret


def test_unknown_expired_and_used_links_give_the_same_answer(client):
    make_customer(client)
    mails = use_resend(client)
    forgot(client)
    token = token_in(mails[0])
    reset(client, token)

    used = reset(client, token)
    unknown = reset(client, "en-nøgle-der-aldrig-har-eksisteret")

    assert used.status_code == unknown.status_code == 400
    assert used.json() == unknown.json()


def test_a_new_request_makes_the_old_link_useless(client):
    make_customer(client)
    mails = use_resend(client)
    forgot(client)
    forgot(client)

    assert reset(client, token_in(mails[0])).status_code == 400
    assert reset(client, token_in(mails[1])).status_code == 200


def test_a_too_weak_password_does_not_use_up_the_link(client):
    customer = make_customer(client)
    mails = use_resend(client)
    forgot(client)
    token = token_in(mails[0])

    weak = reset(client, token, "kort")
    assert weak.status_code == 400
    assert "mindst" in weak.json()["error"]

    assert reset(client, token).status_code == 200  # samme link virker stadig
    assert login(client, customer, NEW_PASSWORD).status_code == 200


def test_choosing_a_new_password_logs_out_everywhere(client):
    customer = logged_in(client)
    assert client.get("/api/auth/me").status_code == 200
    mails = use_resend(client)
    forgot(client)

    reset(client, token_in(mails[0]))

    assert client.get("/api/auth/me").status_code == 401
    assert db_rows(client, "SELECT count(*) FROM sessions") == [(0,)]
    assert db_rows(client, "SELECT count(*) FROM password_tokens") == [(0,)]
    assert customer["email"] == "ejer@test.dk"


def test_a_locked_out_customer_can_log_in_again_after_resetting(client):
    customer = make_customer(client)
    for _ in range(5):
        login(client, customer, "forkert-adgangskode")
    assert login(client, customer).status_code == 429
    mails = use_resend(client)
    forgot(client)

    reset(client, token_in(mails[0]))

    assert login(client, customer, NEW_PASSWORD).status_code == 200


def test_a_link_only_changes_the_password_of_the_user_it_was_sent_to(client):
    ida = make_customer(client, email="ida@test.dk", place_id="ChIJida", name="Idas Café")
    jonas = make_customer(client, email="jonas@test.dk", place_id="ChIJjonas", name="Jonas' Bar")
    mails = use_resend(client)
    forgot(client, "ida@test.dk")

    assert reset(client, token_in(mails[0])).status_code == 200

    assert login(client, ida, NEW_PASSWORD).status_code == 200
    client.cookies.clear()
    assert login(client, jonas).status_code == 200  # Jonas' adgangskode er uændret
    client.cookies.clear()
    assert login(client, jonas, NEW_PASSWORD).status_code == 401


def test_a_deactivated_user_cannot_use_an_old_link(client):
    customer = make_customer(client)
    mails = use_resend(client)
    forgot(client)
    db_execute(client, "UPDATE users SET is_active = false WHERE id = %s", (customer["user_id"],))

    assert reset(client, token_in(mails[0])).status_code == 400


def test_wrong_links_are_rate_limited_per_ip(client):
    statuses = [reset(client, f"gæt-{i}").status_code for i in range(21)]
    assert statuses[:20] == [400] * 20
    assert statuses[20] == 429


def test_reset_input_is_validated(client):
    for payload in [{}, {"token": "abc"}, {"newPassword": NEW_PASSWORD}, {"token": "", "newPassword": NEW_PASSWORD}]:
        assert client.post("/api/auth/reset-password", json=payload).status_code == 400


# ---- mail når adgangskoden er ændret ---------------------------------------------


def changed_mails(mails):
    """De mails der fortæller at adgangskoden er ændret."""
    return [json.loads(m.content) for m in mails if "er ændret" in json.loads(m.content)["subject"]]


def test_changing_password_while_logged_in_sends_a_warning_mail(client):
    logged_in(client)
    mails = use_resend(client)

    response = client.post("/api/auth/password", json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})

    assert response.status_code == 200
    (mail,) = changed_mails(mails)
    assert mail["to"] == ["ejer@test.dk"]
    assert mail["subject"] == "Din adgangskode til Stamgæst er ændret"
    assert "Glemt adgangskode" in mail["text"]
    assert "https://stamgaest.com" in mail["text"]
    # Mailen må ikke give adgang til kontoen, hvis en anden læser den.
    assert "#nulstil=" not in mail["text"]


def test_a_failed_password_change_sends_no_mail(client):
    logged_in(client)
    mails = use_resend(client)
    client.post("/api/auth/password", json={"currentPassword": "forkert", "newPassword": NEW_PASSWORD})
    assert mails == []


def test_choosing_a_new_password_from_the_link_sends_a_warning_mail_too(client):
    make_customer(client)
    mails = use_resend(client)
    forgot(client)
    assert reset(client, token_in(mails[0])).status_code == 200
    assert len(changed_mails(mails)) == 1


def test_the_warning_mail_never_breaks_the_password_change(client):
    logged_in(client)
    use_resend(client, lambda request: httpx.Response(500, json={"message": "nede"}))
    response = client.post("/api/auth/password", json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
    assert response.status_code == 200
    assert login(client, {"email": "ejer@test.dk", "password": NEW_PASSWORD}).status_code == 200


# ---- selve mailservicen (uden database) ------------------------------------------


async def test_a_network_error_to_resend_is_swallowed():
    from app.services import email_service
    from tests.conftest import make_settings

    def broken(request):
        raise httpx.ConnectError("nede")

    http = httpx.AsyncClient(transport=httpx.MockTransport(broken))
    settings = make_settings(resend_api_key="k")
    sent = await email_service.send_email(http, settings, to="a@b.dk", subject="Hej", text="Hej")
    assert sent is False


async def test_the_warning_mail_shows_danish_time():
    from datetime import datetime, timezone

    from app.services import email_service
    from tests.conftest import make_settings

    sent = []

    def recording(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "mail-1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    settings = make_settings(resend_api_key="k")
    # 12:30 UTC om sommeren er 14:30 i Danmark.
    await email_service.send_password_changed(http, settings, to="a@b.dk", when=datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc))
    assert "01.07.2026 kl. 14:30" in sent[0]["text"]
