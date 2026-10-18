"""Tester invitation af nye kunder: velkomstmailen, 7-dages linket, gensend og træk tilbage."""

import json
import re

import httpx
import pytest

from app import admin
from tests.conftest import PASSWORD, db_execute, db_rows, login, make_customer
from tests.test_password_reset_db import NEW_PASSWORD, forgot, reset, token_in, use_resend


def run(client, coro_factory):
    """Kører en async funktion i appens egen event loop, hvor databasepoolen lever."""
    return client.portal.call(coro_factory)


def invited_customer(client, email="ejer@test.dk", name="Test Café"):
    """Opretter en café og en inviteret kunde (uden at sende mailen). Giver brugeren tilbage."""
    existing = make_customer(client, email="andre@test.dk", name=name, place_id=None)

    async def go():
        user, _, _ = await admin.create_customer(
            email=email, restaurant_id=existing["restaurant_id"], invite_pending=True
        )
        return user

    return run(client, go)


def send(client, email="ejer@test.dk"):
    """Sender velkomstmailen via admin-værktøjet."""

    async def go():
        return await admin.send_invitation(
            email=email, http=client.app.state.http, settings=client.app.state.settings
        )

    return run(client, go)


def revoke(client, email="ejer@test.dk"):
    async def go():
        await admin.revoke_invite(email)

    run(client, go)


def status_of(client, email="ejer@test.dk") -> str:
    """Kundens status, som `list-customers` viser den."""

    async def go():
        from app.models import users

        return [admin.customer_status(r) for r in await users.list_with_restaurants() if r["email"] == email]

    ((status,),) = [run(client, go)]
    return status


# ---- velkomstmailen --------------------------------------------------------------


def test_invitation_mail_has_a_link_that_lets_the_customer_choose_a_password(client):
    invited_customer(client, name="Café Gotland")
    mails = use_resend(client)

    send(client)

    assert len(mails) == 1
    body = json.loads(mails[0].content)
    assert body["to"] == ["ejer@test.dk"]
    assert body["from"] == "Stamgæst <noreply@mail.stamgaest.com>"
    assert "Café Gotland" in body["text"]
    assert "7 dage" in body["text"]

    assert reset(client, token_in(mails[0])).status_code == 200
    customer = {"email": "ejer@test.dk", "password": NEW_PASSWORD}
    assert login(client, customer).status_code == 200
    # Den første adgangskode er ikke en "ændring", så der kommer ingen advarselsmail.
    assert len(mails) == 1


def test_the_link_works_for_7_days_not_an_hour(client):
    invited_customer(client)
    mails = use_resend(client)
    send(client)

    ((minutes,),) = db_rows(client, "SELECT extract(epoch FROM expires_at - now()) / 60 FROM password_tokens")
    assert 7 * 24 * 60 - 5 < float(minutes) <= 7 * 24 * 60

    db_execute(client, "UPDATE password_tokens SET expires_at = now() + interval '6 days'")
    assert reset(client, token_in(mails[0])).status_code == 200  # 6 dage senere virker den stadig


def test_choosing_a_password_ends_the_invitation_state(client):
    invited_customer(client)
    mails = use_resend(client)
    send(client)
    assert db_rows(client, "SELECT invite_pending FROM users WHERE email = 'ejer@test.dk'") == [(True,)]

    reset(client, token_in(mails[0]))

    assert db_rows(client, "SELECT invite_pending FROM users WHERE email = 'ejer@test.dk'") == [(False,)]
    assert status_of(client) == "aktiv"


def test_a_new_invitation_makes_the_old_link_useless(client):
    invited_customer(client)
    mails = use_resend(client)
    send(client)
    send(client)

    assert len(mails) == 2
    assert reset(client, token_in(mails[0])).status_code == 400
    assert reset(client, token_in(mails[1])).status_code == 200


def test_a_failing_mail_service_gives_a_clear_error_and_leaves_no_link_behind(client):
    invited_customer(client)
    use_resend(client, lambda request: httpx.Response(403, json={"message": "domæne ikke bekræftet"}))

    with pytest.raises(admin.CliError, match="Mailen kunne ikke sendes"):
        send(client)

    assert db_rows(client, "SELECT count(*) FROM password_tokens") == [(0,)]


# ---- en inviteret kunde kan ikke omgå invitationen -------------------------------


def test_an_invited_customer_cannot_use_forgot_password_before_choosing_one(client):
    """Ellers kunne en fejlindtastet e-mail overtage kontoen, også efter at invitationen er trukket tilbage."""
    invited_customer(client)
    mails = use_resend(client)

    response = forgot(client)

    assert response.status_code == 200  # samme svar som altid
    assert mails == []
    assert db_rows(client, "SELECT count(*) FROM password_tokens") == [(0,)]


def test_nobody_can_log_in_to_an_invited_account_before_the_link_is_used(client):
    invited_customer(client)
    for password in (PASSWORD, "", "password1234"):
        response = client.post("/api/auth/login", json={"email": "ejer@test.dk", "password": password or "x"})
        assert response.status_code == 401


# ---- trække tilbage --------------------------------------------------------------


def test_a_revoked_invitation_cannot_be_used_and_forgot_password_stays_closed(client):
    invited_customer(client)
    mails = use_resend(client)
    send(client)

    revoke(client)

    assert reset(client, token_in(mails[0])).status_code == 400
    assert forgot(client).status_code == 200
    assert len(mails) == 1  # ingen ny mail fra "glemt adgangskode"
    assert status_of(client) == "INVITATION UDLØBET"


def test_a_revoked_customer_can_be_invited_again(client):
    invited_customer(client)
    mails = use_resend(client)
    send(client)
    revoke(client)

    send(client)

    assert reset(client, token_in(mails[1])).status_code == 200


def test_cannot_revoke_or_resend_for_a_customer_who_already_has_a_password(client):
    make_customer(client)
    use_resend(client)

    with pytest.raises(admin.CliError, match="allerede valgt en adgangskode"):
        send(client)
    with pytest.raises(admin.CliError, match="allerede valgt en adgangskode"):
        revoke(client)


def test_invite_for_unknown_or_deactivated_customers_is_refused(client):
    invited_customer(client)
    use_resend(client)
    with pytest.raises(admin.CliError, match="ingen bruger"):
        send(client, "findes-ikke@test.dk")

    db_execute(client, "UPDATE users SET is_active = false WHERE email = 'ejer@test.dk'")
    with pytest.raises(admin.CliError, match="slået fra"):
        send(client)


# ---- status i list-customers -----------------------------------------------------


def test_status_shows_how_far_the_invitation_is(client):
    invited_customer(client)
    use_resend(client)
    assert status_of(client) == "INVITATION UDLØBET"  # oprettet, men ingen mail sendt endnu

    send(client)
    assert status_of(client) == "afventer invitation"

    db_execute(client, "UPDATE password_tokens SET expires_at = now() - interval '1 minute'")
    assert status_of(client) == "INVITATION UDLØBET"

    db_execute(client, "UPDATE users SET is_active = false WHERE email = 'ejer@test.dk'")
    assert status_of(client) == "SLÅET FRA"


# ---- den gamle måde (adgangskode vist én gang) og reset-password -----------------


def test_a_customer_created_with_show_password_is_active_at_once_and_can_use_forgot_password(client):
    existing = make_customer(client, email="andre@test.dk", place_id=None)

    async def go():
        return await admin.create_customer(email="ejer@test.dk", restaurant_id=existing["restaurant_id"])

    _, _, password = run(client, go)
    mails = use_resend(client)

    assert login(client, {"email": "ejer@test.dk", "password": password}).status_code == 200
    forgot(client)
    assert len(mails) == 1


def test_reset_password_from_the_command_line_ends_the_invitation_state(client):
    invited_customer(client)
    mails = use_resend(client)
    send(client)

    async def go():
        return await admin.reset_password("ejer@test.dk")

    password = run(client, go)

    assert login(client, {"email": "ejer@test.dk", "password": password}).status_code == 200
    assert reset(client, token_in(mails[0])).status_code == 400  # det gamle invitationslink overskriver ikke koden
    assert status_of(client) == "aktiv"


# ---- bekræftelse af e-mail (uden database) ---------------------------------------


def test_email_confirmation_ignores_case_and_spaces_but_catches_typos():
    assert admin.email_is_confirmed("ejer@gotland.dk", "  Ejer@Gotland.dk ")
    assert not admin.email_is_confirmed("ejer@gotland.dk", "ejer@gotlnd.dk")
    assert not admin.email_is_confirmed("ejer@gotland.dk", "")


def test_the_prompt_stops_when_the_email_is_typed_differently(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "ejer@gotlnd.dk")
    with pytest.raises(admin.CliError, match="ikke ens"):
        admin._confirm_email_interactively("ejer@gotland.dk")

    monkeypatch.setattr("builtins.input", lambda prompt="": "EJER@gotland.dk")
    admin._confirm_email_interactively("ejer@gotland.dk")  # ingen fejl


def test_the_prompt_without_a_terminal_asks_for_yes(monkeypatch):
    def no_terminal(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_terminal)
    with pytest.raises(admin.CliError, match="--yes"):
        admin._confirm_email_interactively("ejer@gotland.dk")


def test_command_line_options_for_invitations_exist_and_never_take_a_password():
    parser = admin._parser()
    args = parser.parse_args(["create-customer", "--email", "a@b.dk", "--place-id", "ChIJx"])
    assert args.yes is False and args.show_password is False
    args = parser.parse_args(["create-customer", "--email", "a@b.dk", "--place-id", "ChIJx", "--yes", "--show-password"])
    assert args.yes and args.show_password
    assert parser.parse_args(["invite", "--email", "a@b.dk"]).command == "invite"
    assert parser.parse_args(["revoke-invite", "--email", "a@b.dk"]).command == "revoke-invite"
