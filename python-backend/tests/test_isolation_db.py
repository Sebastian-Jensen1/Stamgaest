"""Tester at en kunde ALDRIG kan se eller ændre en anden kundes data.

Det er den vigtigste egenskab ved et system med flere kunder, så den har sin egen fil.
"""

import pytest

from tests.conftest import (
    FakeClaude,
    as_user,
    db_execute,
    db_rows,
    google_ok,
    make_customer,
    place,
    use_claude,
    use_google,
)


@pytest.fixture
def two_cafes(client):
    """To caféer, hver med en bruger og to anmeldelser. Giver (a, b, a_anmeldelser, b_anmeldelser)."""
    a = make_customer(client, email="a@test.dk", name="Café A", place_id="ChIJa")
    b = make_customer(client, email="b@test.dk", name="Café B", place_id="ChIJb")

    as_user(client, a)
    use_google(client, google_ok(place("ChIJa", "Café A")))
    client.post("/api/reviews/refresh", json={})
    a_reviews = client.get("/api/reviews").json()["reviews"]

    as_user(client, b)
    use_google(client, google_ok(place("ChIJb", "Café B")))
    client.post("/api/reviews/refresh", json={})
    b_reviews = client.get("/api/reviews").json()["reviews"]

    return a, b, a_reviews, b_reviews


def test_each_customer_only_sees_their_own_reviews(client, two_cafes):
    a, b, a_reviews, b_reviews = two_cafes
    assert len(a_reviews) == len(b_reviews) == 2
    assert not {r["id"] for r in a_reviews} & {r["id"] for r in b_reviews}

    as_user(client, a)
    data = client.get("/api/reviews").json()
    assert data["businessName"] == "Café A"
    assert {r["id"] for r in data["reviews"]} == {r["id"] for r in a_reviews}

    as_user(client, b)
    assert client.get("/api/reviews").json()["businessName"] == "Café B"


def test_a_customer_cannot_send_a_reply_to_someone_elses_review(client, two_cafes):
    a, b, _, b_reviews = two_cafes
    as_user(client, a)
    response = client.post(f"/api/reviews/{b_reviews[0]['id']}/send", json={"finalText": "Hacket!"})
    assert response.status_code == 404  # "findes ikke", ikke "forbudt": vi afslører ikke at det findes

    assert db_rows(client, "SELECT count(*) FROM replies") == [(0,)]  # intet gemt
    assert db_rows(client, "SELECT count(*) FROM reviews WHERE status = 'answered'") == [(0,)]  # og B's er urørt


def test_a_customer_cannot_mark_someone_elses_review_answered(client, two_cafes):
    a, b, _, b_reviews = two_cafes
    as_user(client, a)
    assert client.post(f"/api/reviews/{b_reviews[0]['id']}/answered", json={}).status_code == 404
    assert db_rows(client, "SELECT count(*) FROM reviews WHERE status = 'answered'") == [(0,)]


def test_a_customer_cannot_spend_money_generating_drafts_for_someone_elses_review(client, two_cafes):
    a, b, a_reviews, b_reviews = two_cafes
    as_user(client, a)
    claude = use_claude(client, FakeClaude())

    only_b = client.post("/api/generate", json={"reviews": [{"id": b_reviews[0]["id"]}]})
    mixed = client.post("/api/generate", json={"reviews": [{"id": a_reviews[0]["id"]}, {"id": b_reviews[0]["id"]}]})

    assert only_b.status_code == mixed.status_code == 404
    assert claude.calls == []  # ikke ét betalt kald, heller ikke for den egne i blandingen
    assert db_rows(client, "SELECT count(*) FROM replies") == [(0,)]


def test_drafts_and_sent_replies_are_private_too(client, two_cafes):
    a, b, _, b_reviews = two_cafes
    as_user(client, b)
    use_claude(client, FakeClaude())
    client.post("/api/generate", json={"reviews": [{"id": b_reviews[0]["id"]}]})
    client.post(f"/api/reviews/{b_reviews[1]['id']}/send", json={"finalText": "Hemmeligt svar fra B"})

    as_user(client, a)
    everything_a_can_see = client.get("/api/reviews").text
    assert "Udkast nr. 1" not in everything_a_can_see
    assert "Hemmeligt svar fra B" not in everything_a_can_see


def test_the_browser_cannot_choose_which_cafe_it_acts_on(client, two_cafes):
    a, b, a_reviews, b_reviews = two_cafes
    as_user(client, a)
    calls = use_google(client, google_ok(place("ChIJa", "Café A")))
    claude = use_claude(client, FakeClaude())
    b_id = str(b["restaurant_id"])

    # Alle måder man kan forsøge at pege på café B på:
    client.post("/api/reviews/refresh", json={"restaurantId": b_id, "placeId": "ChIJb"})
    client.post(
        "/api/generate",
        json={"restaurantId": b_id, "businessName": "Café B", "reviews": [{"id": a_reviews[0]["id"]}]},
    )
    assert client.get(f"/api/reviews?restaurantId={b_id}").json()["businessName"] == "Café A"

    assert all(call.url.path == "/v1/places/ChIJa" for call in calls)  # Google blev kun spurgt om A
    assert "Café B" not in claude.calls[0]["messages"][0]["content"]


def test_the_old_open_endpoints_that_listed_other_cafes_are_gone(client, two_cafes):
    a, b, _, _ = two_cafes
    as_user(client, a)
    assert client.get("/api/restaurants").status_code == 404
    assert client.get(f"/api/restaurants/{b['restaurant_id']}/reviews").status_code == 404
    assert client.get("/api/places/search?query=x").status_code == 404


def test_two_users_at_the_same_cafe_share_it_and_nobody_else_does(client):
    owner = make_customer(client, email="ejer@test.dk", name="Café A", place_id="ChIJa")
    from app.models import users
    from app.services import auth_service

    async def add_staff():
        await users.create(
            email="souschef@test.dk",
            password_hash=await auth_service.hash_password("en-god-adgangskode-42"),
            restaurant_id=owner["restaurant_id"],
        )

    client.portal.call(add_staff)
    other = make_customer(client, email="b@test.dk", name="Café B", place_id="ChIJb")

    as_user(client, owner)
    use_google(client, google_ok(place("ChIJa", "Café A")))
    client.post("/api/reviews/refresh", json={})

    client.cookies.clear()
    staff = client.post("/api/auth/login", json={"email": "souschef@test.dk", "password": "en-god-adgangskode-42"})
    assert staff.status_code == 200
    assert len(client.get("/api/reviews").json()["reviews"]) == 2  # samme café, samme anmeldelser

    as_user(client, other)
    assert client.get("/api/reviews").json()["reviews"] == []


def test_each_user_has_their_own_new_since_last_visit(client):
    # To ejere af samme café er inde på forskellige tidspunkter. Den enes besøg må ikke få
    # anmeldelserne til at se "set" ud for den anden, og en anden café rører ingen af dem.
    owner = make_customer(client, email="ejer@test.dk", name="Café A", place_id="ChIJa")
    partner = make_customer(client, email="partner@test.dk", name="Café A", place_id="ChIJa")
    other = make_customer(client, email="b@test.dk", name="Café B", place_id="ChIJb")

    as_user(client, owner)
    use_google(client, google_ok(place("ChIJa", "Café A")))
    client.post("/api/reviews/refresh", json={})
    # Importen var for 3 timer siden. En ny anmeldelse kom for 100 minutter siden, og ejeren
    # var inde for 70 minutter siden. Ejeren har altså set den, partneren har ikke.
    db_execute(client, "UPDATE reviews SET created_at = now() - interval '3 hours'")
    db_execute(client, "UPDATE restaurants SET first_import_at = now() - interval '3 hours'")
    db_execute(
        client,
        "INSERT INTO reviews (restaurant_id, external_id, rating, created_at) "
        "VALUES (%s, 'places/ChIJa/reviews/r3', 4, now() - interval '100 minutes')",
        (owner["restaurant_id"],),
    )
    db_execute(
        client, "UPDATE users SET reviews_seen_at = now() - interval '70 minutes' WHERE id = %s", (owner["user_id"],)
    )

    def new_ones():
        return [r["externalId"] for r in client.get("/api/reviews").json()["reviews"] if r["newSinceLastVisit"]]

    as_user(client, owner)
    assert new_ones() == []

    as_user(client, other)
    assert client.get("/api/reviews").json()["reviews"] == []

    as_user(client, partner)
    assert new_ones() == ["places/ChIJa/reviews/r3"]


# ---- indstillinger for svar ------------------------------------------------------


def _settings(facts="", **overrides):
    values = {
        "replyLength": "auto", "tone": "", "signature": "", "avoid": "", "contact": "", "examples": "",
        "about": {"concept": facts, "items": [], "traits": [], "atmosphere": ""},
    }
    values.update(overrides)
    return values


def test_each_cafe_has_its_own_reply_settings(client, two_cafes):
    a, b, _, _ = two_cafes
    as_user(client, a)
    assert client.put("/api/reply-settings", json=_settings(signature="Hilsen A", facts="A's hemmelige fakta")).status_code == 200

    as_user(client, b)
    data = client.get("/api/reply-settings").json()
    assert data["signature"] == "" and data["about"]["concept"] == ""  # B ser intet fra A

    client.put("/api/reply-settings", json=_settings(signature="Hilsen B"))
    as_user(client, a)
    assert client.get("/api/reply-settings").json()["signature"] == "Hilsen A"  # B ændrede ikke A's


def test_the_browser_cannot_choose_whose_settings_it_changes(client, two_cafes):
    a, b, _, _ = two_cafes
    as_user(client, a)
    response = client.put(
        "/api/reply-settings",
        json={**_settings(signature="Hacket"), "restaurantId": str(b["restaurant_id"])},
    )
    assert response.status_code == 200  # ekstra felter ignoreres ...
    rows = db_rows(client, "SELECT restaurant_id, signature FROM reply_settings")
    assert rows == [(a["restaurant_id"], "Hacket")]  # ... og kun A's egen række blev ændret


def test_discarding_a_suggestion_only_touches_your_own_cafe(client, two_cafes):
    from tests.conftest import db_execute

    a, b, _, _ = two_cafes
    for cafe in (a, b):
        db_execute(
            client,
            """INSERT INTO reply_settings (restaurant_id, about_suggestion) VALUES (%s, '{"concept": "forslag"}')""",
            (cafe["restaurant_id"],),
        )
    as_user(client, a)
    assert client.delete("/api/reply-settings/suggestion").status_code == 200

    rows = dict(db_rows(client, "SELECT restaurant_id, about_suggestion FROM reply_settings"))
    assert rows[a["restaurant_id"]] is None
    assert rows[b["restaurant_id"]] == {"concept": "forslag"}


def test_a_customer_cannot_preview_a_draft_for_someone_elses_review(client, two_cafes):
    a, b, _, b_reviews = two_cafes
    as_user(client, a)
    claude = use_claude(client, FakeClaude())
    response = client.post(
        "/api/generate/preview", json={"reviewId": b_reviews[0]["id"], "settings": _settings()}
    )
    assert response.status_code == 404  # "findes ikke", ligesom andre fremmede id'er
    assert claude.calls == []  # og det kostede intet


def test_another_cafes_sent_replies_never_end_up_in_your_prompt(client, two_cafes):
    a, b, a_reviews, b_reviews = two_cafes
    as_user(client, b)
    client.post(f"/api/reviews/{b_reviews[0]['id']}/send", json={"finalText": "B's private svar"})
    client.put("/api/reply-settings", json=_settings(facts="B's fakta", examples="B's eksempel"))

    as_user(client, a)
    claude = use_claude(client, FakeClaude())
    assert client.post("/api/generate", json={"reviews": [{"id": a_reviews[0]["id"]}]}).status_code == 200

    system = str(claude.calls[0]["system"])
    assert "B's private svar" not in system and "B's fakta" not in system and "B's eksempel" not in system
    assert "Café A" in system


def test_changing_email_only_ever_touches_your_own_account(client, two_cafes):
    """Kontoen afgøres af login og af nøglen i linket, aldrig af noget browseren kan vælge."""
    import json
    import re

    from tests.test_password_reset_db import use_resend

    a, b, _, _ = two_cafes
    mails = use_resend(client)
    as_user(client, a)
    # Et forsøg på at pege på B's konto ignoreres: der er intet felt til det.
    response = client.post(
        "/api/auth/email",
        json={"newEmail": "ny-a@test.dk", "currentPassword": a["password"], "userId": str(b["user_id"])},
    )
    assert response.status_code == 200
    token = re.search(r"#skiftmail=(\S+)", json.loads(mails[0].content)["text"]).group(1)

    # B åbner A's link: det er A's e-mail der skiftes, ikke den der er logget ind.
    as_user(client, b)
    assert client.post("/api/auth/confirm-email", json={"token": token}).status_code == 200
    rows = dict(db_rows(client, "SELECT id, email FROM users"))
    assert rows[a["user_id"]] == "ny-a@test.dk"
    assert rows[b["user_id"]] == "b@test.dk"
    assert client.get("/api/auth/me").json()["email"] == "b@test.dk"
