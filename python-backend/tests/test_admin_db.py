"""Tester værktøjet vi selv bruger til at oprette kunder (app/admin.py) mod en rigtig database."""

import httpx
import pytest

from app import admin
from app.services.auth_service import validate_new_password
from tests.conftest import PLACE, PASSWORD, db_execute, db_rows, login, make_customer, token_of, with_token


def run(client, coro_factory):
    """Kører en async funktion i appens egen event loop, hvor databasepoolen lever."""
    return client.portal.call(coro_factory)


def google_client(place=PLACE, status=200):
    """En falsk Google som admin-værktøjet kan kalde."""
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=place)))


def create(client, **kwargs):
    async def go():
        return await admin.create_customer(**kwargs)

    return run(client, go)


def test_create_customer_from_a_google_place_saves_cafe_reviews_and_login(client):
    user, restaurant, password = create(
        client, email="Ejer@Gotland.dk", place_id="ChIJtest", business_type="café",
        http=google_client(), api_key="test-key",
    )

    assert user["email"] == "ejer@gotland.dk"  # normaliseret
    assert restaurant["name"] == "Test Café" and restaurant["business_type"] == "café"
    assert validate_new_password(password) is None
    assert db_rows(client, "SELECT count(*) FROM reviews") == [(2,)]  # første login viser allerede anmeldelser
    assert db_rows(client, "SELECT reviews_fetched_at IS NOT NULL FROM restaurants") == [(True,)]
    ((stored,),) = db_rows(client, "SELECT password_hash FROM users")
    assert stored.startswith("$argon2id$") and password not in stored

    response = client.post("/api/auth/login", json={"email": "ejer@gotland.dk", "password": password})
    assert response.status_code == 200 and response.json()["businessName"] == "Test Café"
    assert len(client.get("/api/reviews").json()["reviews"]) == 2


def test_create_customer_for_a_cafe_that_already_exists(client):
    existing = make_customer(client, email="gammel@test.dk", name="Eksisterende Café")
    user, restaurant, password = create(
        client, email="ny@test.dk", restaurant_id=existing["restaurant_id"], business_type="bar",
    )
    assert restaurant["id"] == existing["restaurant_id"]
    assert db_rows(client, "SELECT business_type FROM restaurants") == [("bar",)]
    assert db_rows(client, "SELECT count(*) FROM users WHERE restaurant_id = %s", (existing["restaurant_id"],)) == [(2,)]


def test_every_new_customer_gets_a_different_password(client):
    _, _, first = create(client, email="a@test.dk", place_id="ChIJa", http=google_client(), api_key="k")
    _, _, second = create(client, email="b@test.dk", place_id="ChIJb", http=google_client(), api_key="k")
    assert first != second


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"email": "a@test.dk"}, "præcis én"),
        ({"email": "a@test.dk", "place_id": "ChIJa", "restaurant_id": "00000000-0000-0000-0000-000000000000"}, "præcis én"),
        ({"email": "ikke-en-mail", "place_id": "ChIJa"}, "Ugyldig e-mail"),
        ({"email": "a@test.dk", "place_id": "../../hack"}, "Ugyldigt place-id"),
        ({"email": "a@test.dk", "place_id": "ChIJa"}, "GOOGLE_PLACES_API_KEY"),  # uden Google-klient
        ({"email": "a@test.dk", "restaurant_id": "00000000-0000-0000-0000-000000000000"}, "ingen café"),
    ],
)
def test_bad_input_gives_a_clear_error_and_creates_nothing(client, kwargs, message):
    from uuid import UUID

    if isinstance(kwargs.get("restaurant_id"), str):
        kwargs = {**kwargs, "restaurant_id": UUID(kwargs["restaurant_id"])}
    with pytest.raises(admin.CliError, match=message):
        create(client, **kwargs)
    assert db_rows(client, "SELECT count(*) FROM users") == [(0,)]


def test_a_second_customer_with_the_same_email_is_refused_ignoring_case(client):
    create(client, email="ejer@test.dk", place_id="ChIJa", http=google_client(), api_key="k")
    with pytest.raises(admin.CliError, match="allerede en bruger"):
        create(client, email="EJER@test.dk", place_id="ChIJb", http=google_client(), api_key="k")
    assert db_rows(client, "SELECT count(*) FROM users") == [(1,)]


def test_reset_password_gives_a_new_one_and_logs_the_customer_out_everywhere(client):
    customer = make_customer(client)
    token = token_of(login(client, customer))

    async def go():
        return await admin.reset_password("EJER@test.dk")

    new_password = run(client, go)

    assert with_token(client, "GET", "/api/auth/me", token).status_code == 401  # gammel session er dræbt
    assert login(client, customer).status_code == 401  # gammel adgangskode virker ikke
    assert login(client, customer, new_password).status_code == 200


def test_deactivate_and_activate(client):
    customer = make_customer(client)
    token = token_of(login(client, customer))

    async def off():
        await admin.set_active(customer["email"], False)

    async def on():
        await admin.set_active(customer["email"], True)

    run(client, off)
    assert with_token(client, "GET", "/api/auth/me", token).status_code == 401
    assert login(client, customer).status_code == 401
    run(client, on)
    assert login(client, customer).status_code == 200


def test_unknown_email_is_reported(client):
    async def go():
        await admin.reset_password("findes-ikke@test.dk")

    with pytest.raises(admin.CliError, match="ingen bruger"):
        run(client, go)


def test_the_command_line_never_takes_a_password_because_it_would_end_up_in_shell_history():
    parser = admin._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["create-customer", "--email", "a@b.dk", "--place-id", "x", "--password", "hemmelig"])
    args = parser.parse_args(["create-customer", "--email", "a@b.dk", "--place-id", "ChIJx"])
    assert not hasattr(args, "password")


# ---- stats: hvor meget rettes der i Claudes udkast -------------------------------


def add_review(client, restaurant_id, external_id, replies_):
    """Lægger en anmeldelse og dens svar direkte i databasen. replies_ er (udkast, sendt, model) i rækkefølge."""
    ((review_id,),) = db_rows(
        client,
        "INSERT INTO reviews (restaurant_id, external_id, rating, review_text) VALUES (%s, %s, 4, 'God kaffe') RETURNING id",
        (restaurant_id, external_id),
    )
    db_execute(client, "UPDATE reviews SET status = 'answered' WHERE id = %s", (review_id,))
    for i, (draft, final, model) in enumerate(replies_):
        db_execute(
            client,
            """
            INSERT INTO replies (review_id, draft_text, final_text, model, status, sent_at, created_at)
            VALUES (%s, %s, %s, %s, %s, CASE WHEN %s::text IS NULL THEN NULL ELSE now() END,
                    now() - make_interval(mins => %s))
            """,
            (review_id, draft, final, model, "sent" if final else "draft", final, 10 - i),
        )
    return review_id


def report(client, **kwargs):
    async def go():
        return await admin.edit_report(**kwargs)

    return run(client, go)


def test_stats_measures_only_replies_drafted_by_claude(client):
    cafe = make_customer(client)
    add_review(client, cafe["restaurant_id"], "r1", [("Tak for besøget!", "Tak for besøget!", "m1")])
    # To udkast ("Generér igen"), og det sidste blev skrevet helt om før det blev sendt.
    add_review(client, cafe["restaurant_id"], "r2", [("Første forsøg", None, "m1"), ("Hej med dig", "Vi ses snart igen", "m1")])
    add_review(client, cafe["restaurant_id"], "r3", [("Skrevet i hånden", "Skrevet i hånden", None)])  # ikke Claude
    add_review(client, cafe["restaurant_id"], "r4", [("Aldrig sendt", None, "m1")])  # kun et udkast

    result = report(client)

    overall = result["overall"]
    assert (overall.count, overall.unchanged, overall.rewritten) == (2, 1, 1)
    assert result["drafts_per_reply"] == 1.5
    assert [row["final_text"] for row in result["replies"]] == ["Vi ses snart igen", "Tak for besøget!"]  # mest rettet først


def test_stats_can_be_limited_to_one_cafe_and_recent_days(client):
    first = make_customer(client, email="a@test.dk", name="Café A", place_id="ChIJa")
    second = make_customer(client, email="b@test.dk", name="Café B", place_id="ChIJb")
    add_review(client, first["restaurant_id"], "r1", [("Tak", "Tak", "m1")])
    old = add_review(client, second["restaurant_id"], "r2", [("Tak", "Hej", "m2")])

    assert set(report(client)["by_restaurant"]) == {"Café A", "Café B"}
    assert set(report(client)["by_model"]) == {"m1", "m2"}
    assert set(report(client, restaurant_id=first["restaurant_id"])["by_restaurant"]) == {"Café A"}

    db_execute(client, "UPDATE replies SET sent_at = now() - interval '40 days' WHERE review_id = %s", (old,))
    assert set(report(client, days=30)["by_restaurant"]) == {"Café A"}


def test_stats_with_no_replies_and_bad_days(client):
    assert report(client)["overall"] is None
    with pytest.raises(admin.CliError):
        report(client, days=0)
