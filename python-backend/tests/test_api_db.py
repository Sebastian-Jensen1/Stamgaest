"""Tester en kundes eget flow gennem API'et mod en rigtig database (et midlertidigt testschema).

Kunden logger ind, henter anmeldelser, får udkast fra Claude og sender svar. Google og Claude er
falske. Alt andet er ægte: routes, login, validering, SQL, transaktioner og migrationer.
Se conftest.py for hvordan testdatabasen laves og ryddes op, og test_isolation_db.py for at kunder
er adskilt fra hinanden.
"""

import httpx
import psycopg
import pytest

from tests.conftest import (
    PLACE,
    FakeClaude,
    allow_refresh_now,
    db_rows,
    google_ok,
    logged_in,
    make_customer,
    use_claude,
    use_google,
)


def refresh(client):
    return client.post("/api/reviews/refresh", json={})


def by_external_id(data):
    return {r["externalId"].rsplit("/", 1)[-1]: r for r in data["reviews"]}


def seed(client, **kwargs):
    """Opretter en kunde, logger ind og henter anmeldelser. Giver (kunde, {r1: ..., r2: ...})."""
    customer = logged_in(client, **kwargs)
    use_google(client, google_ok())
    data = refresh(client).json()
    return customer, by_external_id(data)


# ---- migrationer -----------------------------------------------------------------


def test_migrations_apply_once_and_are_idempotent(migration_result):
    first, second = migration_result
    assert first == 2  # 001_init.sql og 002_auth.sql
    assert second == 0  # næste kørsel gør ingenting


# ---- hent anmeldelser ------------------------------------------------------------


def test_dashboard_starts_empty_and_shows_the_customers_own_cafe(client):
    logged_in(client)
    data = client.get("/api/reviews").json()
    assert data["businessName"] == "Test Café"
    assert data["businessType"] == "café"
    assert data["reviews"] == []
    assert data["reviewsFetchedAt"] is None


def test_refresh_fetches_the_customers_own_place_and_saves_it(client):
    logged_in(client)
    calls = use_google(client, google_ok())

    response = refresh(client)
    assert response.status_code == 200
    data = response.json()

    assert data["businessName"] == "Test Café"
    assert data["address"] == "Testvej 1, 2200 København"
    assert data["rating"] == 4.4
    assert data["userRatingCount"] == 120
    assert data["businessType"] == "café"  # ejerens branche overskrives ikke af Google
    assert data["reviewsFetchedAt"] is not None
    assert {r["status"] for r in data["reviews"]} == {"new"}
    assert by_external_id(data)["r1"]["reviewerName"] == "Ida"
    assert by_external_id(data)["r1"]["publishedAt"].startswith("2026-09-01T10:00:00")

    # Nøglen sendes i header, ikke i URL'en, og stien er præcis kundens eget sted.
    (call,) = calls
    assert call.headers["X-Goog-Api-Key"] == "test-google-key"
    assert "test-google-key" not in str(call.url)
    assert call.url.path == "/v1/places/ChIJtest"

    # ...og det er blevet gemt: en ny læsning fra databasen giver det samme.
    assert client.get("/api/reviews").json()["reviews"] == data["reviews"]


def test_the_browser_cannot_point_refresh_at_another_place(client):
    logged_in(client)
    calls = use_google(client, google_ok())
    client.post("/api/reviews/refresh", json={"placeId": "ChIJandet", "restaurantId": "00000000-0000-0000-0000-000000000000"})
    assert calls[0].url.path == "/v1/places/ChIJtest"


def test_refresh_has_a_cooldown_so_double_clicks_cost_one_google_call(client):
    logged_in(client)
    calls = use_google(client, google_ok())

    assert refresh(client).status_code == 200
    second = refresh(client)
    assert second.status_code == 429
    assert "Prøv igen om" in second.json()["error"]
    assert len(calls) == 1  # Google blev kun spurgt én gang

    allow_refresh_now(client)
    assert refresh(client).status_code == 200
    assert len(calls) == 2


def test_a_failed_refresh_does_not_start_the_cooldown(client):
    logged_in(client)
    use_google(client, lambda request: httpx.Response(500, text="nede"))
    assert refresh(client).status_code == 502

    use_google(client, google_ok())
    assert refresh(client).status_code == 200  # kunden kan prøve igen med det samme


def test_fetching_twice_creates_no_duplicates(client):
    logged_in(client)
    use_google(client, google_ok())
    refresh(client)
    allow_refresh_now(client)
    second = refresh(client).json()
    assert len(second["reviews"]) == 2
    assert db_rows(client, "SELECT count(*) FROM restaurants") == [(1,)]


def test_refetching_does_not_reset_answered_reviews(client):
    # Den vigtigste regel i hele databasedesignet.
    _, reviews = seed(client)
    assert client.post(f"/api/reviews/{reviews['r1']['id']}/send", json={"finalText": "Tak!"}).json() == {"ok": True}

    allow_refresh_now(client)
    again = by_external_id(refresh(client).json())["r1"]
    assert again["status"] == "sent"
    assert again["draft"] == "Tak!"


def test_old_reviews_google_no_longer_sends_are_kept(client):
    seed(client)
    use_google(client, google_ok({**PLACE, "reviews": PLACE["reviews"][:1]}))
    allow_refresh_now(client)
    assert len(refresh(client).json()["reviews"]) == 2


def test_cafe_without_google_link_gets_a_clear_message(client):
    logged_in(client, place_id=None)
    calls = use_google(client, google_ok())
    response = refresh(client)
    assert response.status_code == 409
    assert "koblet til Google" in response.json()["error"]
    assert calls == []


def test_google_review_text_is_stored_verbatim_and_escaping_is_left_to_the_frontend(client):
    _, reviews = seed(client)
    assert reviews["r2"]["reviewerName"] == "<img src=x onerror=alert(1)>"


# ---- fejl fra Google -------------------------------------------------------------


@pytest.mark.parametrize("status, expected_status", [(403, 502), (404, 502), (429, 503), (500, 502)])
def test_google_errors_are_reported_without_leaking_details(client, status, expected_status):
    logged_in(client)
    use_google(client, lambda request: httpx.Response(status, text="INTERN-DETALJE test-google-key"))
    response = refresh(client)
    assert response.status_code == expected_status
    assert "INTERN-DETALJE" not in response.text
    assert "test-google-key" not in response.text
    assert client.get("/api/reviews").json()["reviews"] == []  # intet halvt gemt


def test_google_timeout_becomes_502(client):
    logged_in(client)

    def boom(request):
        raise httpx.ConnectTimeout("langsom")

    use_google(client, boom)
    assert refresh(client).status_code == 502


def test_google_returning_garbage_becomes_502(client):
    logged_in(client)
    use_google(client, lambda request: httpx.Response(200, text="<html>ikke json</html>"))
    assert refresh(client).status_code == 502


# ---- generér udkast --------------------------------------------------------------


def generate(client, ids, **extra):
    return client.post("/api/generate", json={"reviews": [{"id": i} for i in ids], **extra})


def test_generate_saves_drafts_and_marks_reviews_ready(client):
    _, reviews = seed(client)
    claude = use_claude(client, FakeClaude())

    response = generate(client, [reviews["r1"]["id"], reviews["r2"]["id"]], tone="varmt")
    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["draft"] for r in results] == ["Udkast nr. 1", "Udkast nr. 2"]  # trimmet
    assert {r["model"] for r in results} == {"test-model"}
    assert results[0]["reviewerName"] == "Ida"

    saved = client.get("/api/reviews").json()
    assert saved["defaultTone"] == "varmt"  # tonen huskes
    assert by_external_id(saved)["r1"]["status"] == "ready"
    assert by_external_id(saved)["r1"]["draft"] == "Udkast nr. 1"

    # Prompten bygges af det der ligger i databasen: anmeldelsen og CAFÉENS navn og branche.
    prompt = claude.calls[0]["messages"][0]["content"]
    assert "Fantastisk kaffe!" in prompt
    assert "Test Café (café)" in prompt
    assert claude.calls[0]["model"] == "test-model"


def test_generate_ignores_everything_the_client_claims_about_the_cafe_and_review(client):
    _, reviews = seed(client)
    claude = use_claude(client, FakeClaude())
    generate(
        client, [reviews["r1"]["id"]],
        businessName="HACKER-CAFE", businessType="hacker", restaurantId="00000000-0000-0000-0000-000000000000",
    )
    client.post(
        "/api/generate",
        json={"reviews": [{"id": reviews["r1"]["id"], "reviewText": "SNYDETEKST", "reviewerName": "SNYD", "rating": 1}]},
    )
    for call in claude.calls:
        prompt = call["messages"][0]["content"]
        assert "HACKER" not in prompt and "SNYD" not in prompt
        assert "Fantastisk kaffe!" in prompt and "Test Café" in prompt


def test_generate_for_unknown_review_costs_nothing(client):
    seed(client)
    claude = use_claude(client, FakeClaude())
    response = generate(client, ["00000000-0000-0000-0000-000000000000"])
    assert response.status_code == 404
    assert claude.calls == []  # ingen betalte kald


def test_generate_same_review_twice_only_drafts_once(client):
    _, reviews = seed(client)
    claude = use_claude(client, FakeClaude())
    rid = reviews["r1"]["id"]
    generate(client, [rid, rid])
    assert len(claude.calls) == 1


def test_generate_keeps_drafts_made_before_a_failure(client):
    _, reviews = seed(client)
    use_claude(client, FakeClaude(fail_on_call=2))
    response = generate(client, [reviews["r1"]["id"], reviews["r2"]["id"]])
    assert response.status_code == 502
    assert response.json() == {"error": "Claude fejlede (test)."}

    saved = by_external_id(client.get("/api/reviews").json())
    assert {saved["r1"]["status"], saved["r2"]["status"]} == {"ready", "new"}  # første er gemt og betalt for


def test_regenerating_keeps_history_and_shows_newest(client):
    _, reviews = seed(client)
    payload = [reviews["r1"]["id"]]
    use_claude(client, FakeClaude())
    generate(client, payload)
    use_claude(client, FakeClaude())
    generate(client, payload)

    assert by_external_id(client.get("/api/reviews").json())["r1"]["draft"] == "Udkast nr. 1"  # nyt FakeClaude tæller forfra
    assert db_rows(client, "SELECT count(*) FROM replies") == [(2,)]


def test_generate_is_limited_per_user_to_keep_the_bill_down(client):
    _, reviews = seed(client)
    claude = use_claude(client, FakeClaude())
    client.app.state.limits.generate.max_events = 2
    try:
        response = generate(client, [reviews["r1"]["id"], reviews["r2"]["id"]])
        assert response.status_code == 200
        again = generate(client, [reviews["r1"]["id"]])
        assert again.status_code == 429
        assert "Prøv igen om" in again.json()["error"]
        assert len(claude.calls) == 2  # den blokerede anmodning kostede ingenting
    finally:
        client.app.state.limits.generate.max_events = 100


@pytest.mark.parametrize(
    "payload",
    [
        {"reviews": []},
        {"reviews": [{"id": "1' OR '1'='1"}]},
        {"reviews": [{"id": "00000000-0000-0000-0000-000000000000"}] * 26},
        {"tone": "x" * 101, "reviews": [{"id": "00000000-0000-0000-0000-000000000000"}]},
        {},
    ],
)
def test_generate_rejects_bad_input_before_any_paid_call(client, payload):
    logged_in(client)
    claude = use_claude(client, FakeClaude())
    assert client.post("/api/generate", json=payload).status_code == 400
    assert claude.calls == []


# ---- send og markér besvaret -----------------------------------------------------


def test_send_stores_edited_text_next_to_the_draft(client):
    _, reviews = seed(client)
    use_claude(client, FakeClaude())
    rid = reviews["r1"]["id"]
    generate(client, [rid])

    assert client.post(f"/api/reviews/{rid}/send", json={"finalText": "  Redigeret svar  "}).status_code == 200

    saved = by_external_id(client.get("/api/reviews").json())["r1"]
    assert saved["status"] == "sent"
    assert saved["draft"] == "Redigeret svar"  # det sendte vinder over modellens udkast
    ((draft, final, status, model),) = db_rows(client, "SELECT draft_text, final_text, status, model FROM replies")
    assert (draft, final, status, model) == ("Udkast nr. 1", "Redigeret svar", "sent", "test-model")


def test_send_without_a_draft_stores_it_as_handwritten(client):
    _, reviews = seed(client)
    client.post(f"/api/reviews/{reviews['r2']['id']}/send", json={"finalText": "Skrevet i hånden"})
    ((final, status, model),) = db_rows(client, "SELECT final_text, status, model FROM replies")
    assert (final, status, model) == ("Skrevet i hånden", "sent", None)  # model NULL = ikke AI


def test_send_touches_only_the_newest_draft(client):
    _, reviews = seed(client)
    rid = reviews["r1"]["id"]
    use_claude(client, FakeClaude())
    generate(client, [rid])
    generate(client, [rid])
    client.post(f"/api/reviews/{rid}/send", json={"finalText": "endeligt"})
    assert db_rows(client, "SELECT status FROM replies ORDER BY created_at") == [("draft",), ("sent",)]


def test_send_validation(client):
    _, reviews = seed(client)
    rid = reviews["r1"]["id"]
    assert client.post(f"/api/reviews/{rid}/send", json={"finalText": ""}).status_code == 400
    assert client.post(f"/api/reviews/{rid}/send", json={"finalText": "   "}).status_code == 400
    assert client.post(f"/api/reviews/{rid}/send", json={}).status_code == 400
    assert client.post(f"/api/reviews/{rid}/send", json={"finalText": "x" * 4097}).status_code == 400
    assert client.post(f"/api/reviews/{rid}/send", json={"finalText": "x" * 4096}).status_code == 200
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/api/reviews/{unknown}/send", json={"finalText": "x"}).status_code == 404


def test_validation_error_never_echoes_what_was_sent(client):
    logged_in(client)
    response = client.post(
        "/api/reviews/00000000-0000-0000-0000-000000000000/send",
        json={"finalText": 12345, "hemmelig": "SKAL-IKKE-VISES"},
    )
    assert response.status_code == 400
    assert "SKAL-IKKE-VISES" not in response.text and "12345" not in response.text


def test_mark_answered_without_text(client):
    _, reviews = seed(client)
    rid = reviews["r1"]["id"]
    assert client.post(f"/api/reviews/{rid}/answered", json={}).json() == {"ok": True}
    saved = by_external_id(client.get("/api/reviews").json())["r1"]
    assert saved["status"] == "sent" and saved["draft"] == ""
    assert db_rows(client, "SELECT count(*) FROM replies") == [(0,)]  # ingen svartekst gemt


def test_mark_answered_unknown_review(client):
    logged_in(client)
    assert client.post("/api/reviews/00000000-0000-0000-0000-000000000000/answered", json={}).status_code == 404


def test_invalid_review_id_is_rejected(client):
    logged_in(client)
    assert client.post("/api/reviews/ikke-et-uuid/answered", json={}).status_code == 400
    assert client.post("/api/reviews/1'; DROP TABLE reviews;--/send", json={"finalText": "x"}).status_code in (400, 404)


# ---- SQL og transaktioner --------------------------------------------------------


def test_sql_injection_attempts_are_inert(client):
    _, reviews = seed(client)
    evil = "'; DROP TABLE reviews; --"
    use_claude(client, FakeClaude())
    generate(client, [reviews["r1"]["id"]], tone=evil)
    assert client.get("/api/reviews").json()["defaultTone"] == evil  # gemt som ren tekst
    assert db_rows(client, "SELECT count(*) FROM reviews") == [(2,)]  # tabellen findes stadig


def test_transaction_rolls_back_the_whole_google_fetch_on_failure(client):
    # Anden anmeldelse bryder CHECK (rating BETWEEN 1 AND 5). Parsing i places_service
    # filtrerer sådan noget fra, så fejlen tvinges ind direkte i model-laget.
    from types import SimpleNamespace

    from app.models import restaurants, reviews

    async def run():
        restaurant = await restaurants.upsert_by_place_id(
            google_place_id="ChIJrollback", name="R", business_type=None, address=None,
            google_rating=None, google_rating_count=None,
        )
        good = SimpleNamespace(external_id="ok", reviewer_name="A", rating=5, review_text="t", published_at=None)
        bad = SimpleNamespace(external_id="bad", reviewer_name="B", rating=99, review_text="t", published_at=None)
        with pytest.raises(psycopg.errors.CheckViolation):
            await reviews.upsert_many(restaurant["id"], [good, bad])
        return await reviews.list_by_restaurant(restaurant["id"])

    # Køres i appens egen event loop, hvor databasepoolen lever.
    assert client.portal.call(run) == []  # den gode blev ikke gemt alene
