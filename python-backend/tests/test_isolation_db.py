"""Tester at en kunde ALDRIG kan se eller ændre en anden kundes data.

Det er den vigtigste egenskab ved et system med flere kunder, så den har sin egen fil.
"""

import pytest

from tests.conftest import (
    FakeClaude,
    as_user,
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
