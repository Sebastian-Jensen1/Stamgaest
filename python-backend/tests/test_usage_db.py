"""Tester lofterne for betalte kald (Claude og Google), forbrugsloggen, advarsler og nødbremserne."""

import argparse
import asyncio
import dataclasses
import json

import httpx
import pytest

from app import admin
from app.core.config import Caps, load_settings
from app.models import usage
from tests.conftest import (
    FakeClaude,
    PLACE,
    allow_refresh_now,
    as_user,
    db_rows,
    logged_in,
    make_customer,
    place,
    use_claude,
)

BIG = Caps(cafe_day=1000, cafe_month=1000, all_day=1000, all_month=1000)


@pytest.fixture(autouse=True)
def restore_settings(request):
    """Lægger de oprindelige indstillinger tilbage efter hver test, så fx en nødbremse ikke hænger ved."""
    if "client" not in request.fixturenames:
        yield
        return
    client = request.getfixturevalue("client")
    original = client.app.state.settings
    yield
    client.app.state.settings = original


def caps(client, *, drafts: Caps | None = None, fetches: Caps | None = None, **other):
    """Sætter lofterne (og evt. andre indstillinger) for appen i testen."""
    client.app.state.settings = dataclasses.replace(
        client.app.state.settings, draft_caps=drafts or BIG, fetch_caps=fetches or BIG, **other
    )


def use_services(client, google=None):
    """Falsk Google og falsk Resend i ét. Giver (Google-kald, mails)."""
    google_calls, mails = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.resend.com":
            mails.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "mail"})
        google_calls.append(request)
        return google(request) if google else httpx.Response(200, json=PLACE)

    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return google_calls, mails


def setup_cafe(client, **kwargs):
    """Opretter en kunde, logger ind og henter anmeldelserne. Giver anmeldelsernes id'er."""
    customer = logged_in(client, **kwargs)
    response = client.post("/api/reviews/refresh", json={})
    assert response.status_code == 200, response.text
    return customer, [r["id"] for r in response.json()["reviews"]]


def generate(client, ids):
    return client.post("/api/generate", json={"reviews": [{"id": i} for i in ids]})


def run(client, coro_factory):
    return client.portal.call(coro_factory)


# ---- loggen ----------------------------------------------------------------------


def test_every_draft_is_logged_with_tokens_and_never_the_review_text(client):
    caps(client)
    use_services(client)
    customer, ids = setup_cafe(client)
    use_claude(client, FakeClaude())

    assert generate(client, ids).status_code == 200

    rows = db_rows(
        client,
        "SELECT kind, status, model, input_tokens, output_tokens, restaurant_id, user_id FROM usage_events "
        "WHERE kind = 'claude_draft' ORDER BY id",
    )
    assert len(rows) == 2
    for row in rows:
        assert row[:5] == ("claude_draft", "done", "test-model", 500, 100)
        assert row[5] == customer["restaurant_id"] and row[6] == customer["user_id"]
    everything = str(db_rows(client, "SELECT * FROM usage_events"))
    assert "Fantastisk kaffe" not in everything and "Ida" not in everything


def test_google_fetches_are_logged_too(client):
    caps(client)
    use_services(client)
    setup_cafe(client)
    assert db_rows(client, "SELECT kind, status FROM usage_events") == [("places_fetch", "done")]


# ---- lofter for udkast -----------------------------------------------------------


def test_cafe_daily_cap_refuses_before_calling_claude(client):
    caps(client, drafts=Caps(cafe_day=3, cafe_month=100, all_day=100, all_month=100))
    use_services(client)
    _, ids = setup_cafe(client)
    claude = use_claude(client, FakeClaude())

    assert generate(client, ids).status_code == 200
    refused = generate(client, ids)

    assert refused.status_code == 429
    assert refused.json() == {"error": "Din café har 1 udkast tilbage i dag. Vælg færre anmeldelser, eller prøv igen i morgen."}
    assert len(claude.calls) == 2  # det afviste kald nåede aldrig Claude
    assert generate(client, ids[:1]).status_code == 200  # den sidste plads kan stadig bruges


def test_caps_survive_a_restart_because_they_live_in_the_database(client):
    caps(client, drafts=Caps(cafe_day=2, cafe_month=100, all_day=100, all_month=100))
    use_services(client)
    _, ids = setup_cafe(client)
    use_claude(client, FakeClaude())
    assert generate(client, ids).status_code == 200

    client.app.state.limits.reset_all()  # det der ligger i hukommelsen, er væk efter en genstart

    assert generate(client, ids[:1]).status_code == 429


def test_cafe_monthly_cap(client):
    caps(client, drafts=Caps(cafe_day=100, cafe_month=2, all_day=100, all_month=100))
    use_services(client)
    _, ids = setup_cafe(client)
    use_claude(client, FakeClaude())
    assert generate(client, ids).status_code == 200

    refused = generate(client, ids[:1])

    assert refused.status_code == 429
    assert "denne måned" in refused.json()["error"]


def test_the_total_cap_holds_across_cafes_and_does_not_reveal_itself(client):
    caps(client, drafts=Caps(cafe_day=100, cafe_month=100, all_day=3, all_month=100))
    use_services(client)
    _, ids_a = setup_cafe(client, email="a@test.dk", place_id="ChIJa", name="A")
    use_claude(client, FakeClaude())
    assert generate(client, ids_a).status_code == 200

    client.cookies.clear()
    use_services(client, google=lambda request: httpx.Response(200, json=place("ChIJb", "B")))
    _, ids_b = setup_cafe(client, email="b@test.dk", place_id="ChIJb", name="B")

    refused = generate(client, ids_b)

    assert refused.status_code == 429
    assert refused.json() == {"error": "Udkast er midlertidigt ikke tilgængelige. Prøv igen senere."}
    assert generate(client, ids_b[:1]).status_code == 200  # én plads tilbage i alt


def test_simultaneous_requests_cannot_get_past_the_cap(client):
    customer = make_customer(client)

    async def go():
        tight = Caps(cafe_day=3, cafe_month=100, all_day=100, all_month=100)
        results = await asyncio.gather(*[
            usage.reserve(kind=usage.DRAFT, restaurant_id=customer["restaurant_id"], user_id=None, count=1, caps=tight)
            for _ in range(10)
        ])
        return [r.refused is None for r in results]

    accepted = run(client, go)

    assert accepted.count(True) == 3
    assert db_rows(client, "SELECT count(*) FROM usage_events") == [(3,)]


def test_a_failed_claude_call_still_counts_but_the_unsent_ones_are_released(client):
    caps(client, drafts=Caps(cafe_day=2, cafe_month=100, all_day=100, all_month=100))
    use_services(client)
    _, ids = setup_cafe(client)
    use_claude(client, FakeClaude(fail_on_call=1))

    assert generate(client, ids).status_code == 502

    assert db_rows(client, "SELECT status FROM usage_events WHERE kind = 'claude_draft' ORDER BY id") == [
        ("failed",), ("skipped",)
    ]
    use_claude(client, FakeClaude())
    assert generate(client, ids[:1]).status_code == 200  # 1 brugt (det fejlede) + 1 = loftet
    assert generate(client, ids[:1]).status_code == 429


# ---- lofter for hentning fra Google ----------------------------------------------


def test_google_fetch_cap_refuses_and_does_not_start_the_cooldown(client):
    caps(client, fetches=Caps(cafe_day=1, cafe_month=100, all_day=100, all_month=100))
    google_calls, _ = use_services(client)
    setup_cafe(client)
    allow_refresh_now(client)

    refused = client.post("/api/reviews/refresh", json={})

    assert refused.status_code == 429
    assert refused.json() == {"error": "Anmeldelserne er hentet mange gange i dag. Prøv igen i morgen."}
    assert len(google_calls) == 1

    caps(client, fetches=Caps(cafe_day=2, cafe_month=100, all_day=100, all_month=100))
    assert client.post("/api/reviews/refresh", json={}).status_code == 200  # ingen ventetid på et minut


def test_failing_google_calls_count_so_nobody_can_retry_forever(client):
    caps(client, fetches=Caps(cafe_day=2, cafe_month=100, all_day=100, all_month=100))
    google_calls, _ = use_services(client, google=lambda request: httpx.Response(500, json={}))
    logged_in(client)

    statuses = [client.post("/api/reviews/refresh", json={}).status_code for _ in range(3)]

    assert statuses == [502, 502, 429]
    assert len(google_calls) == 2


# ---- nødbremser ------------------------------------------------------------------


def test_global_kill_switch_stops_all_drafts(client):
    caps(client, ai_paused=True)
    use_services(client)
    _, ids = setup_cafe(client)
    claude = use_claude(client, FakeClaude())

    response = generate(client, ids)

    assert response.status_code == 503
    assert claude.calls == []
    assert db_rows(client, "SELECT count(*) FROM usage_events WHERE kind = 'claude_draft'") == [(0,)]


def test_kill_switch_for_one_cafe_leaves_other_cafes_alone(client):
    caps(client)
    use_services(client)
    a, ids_a = setup_cafe(client, email="a@test.dk", place_id="ChIJa", name="A")
    client.cookies.clear()
    use_services(client, google=lambda request: httpx.Response(200, json=place("ChIJb", "B")))
    b, ids_b = setup_cafe(client, email="b@test.dk", place_id="ChIJb", name="B")
    claude = use_claude(client, FakeClaude())

    async def off():
        await admin.set_ai_enabled(a["restaurant_id"], False)

    run(client, off)

    as_user(client, a)
    blocked = generate(client, ids_a)
    assert blocked.status_code == 403
    assert blocked.json() == {"error": "Udkast er slået fra for din café. Kontakt Stamgæst."}
    assert claude.calls == []

    as_user(client, b)
    assert generate(client, ids_b).status_code == 200

    async def on():
        await admin.set_ai_enabled(a["restaurant_id"], True)

    run(client, on)
    as_user(client, a)
    assert generate(client, ids_a).status_code == 200


def test_kill_switch_for_unknown_cafe_is_reported(client):
    from uuid import UUID

    async def go():
        await admin.set_ai_enabled(UUID("00000000-0000-0000-0000-000000000000"), False)

    with pytest.raises(admin.CliError, match="ingen café"):
        run(client, go)


# ---- advarsler på mail -----------------------------------------------------------


def test_we_are_warned_at_80_and_100_percent_once_each(client):
    caps(
        client,
        drafts=Caps(cafe_day=5, cafe_month=1000, all_day=1000, all_month=1000),
        admin_email="stamgaest.kontakt@gmail.com",
        resend_api_key="test-resend-key",
    )
    _, mails = use_services(client)
    _, ids = setup_cafe(client)
    use_claude(client, FakeClaude())

    generate(client, ids)  # 2
    generate(client, ids)  # 4 = 80 %
    assert len(mails) == 1
    assert mails[0]["to"] == ["stamgaest.kontakt@gmail.com"]
    assert "80 %" in mails[0]["subject"] and "Test Café" in mails[0]["subject"]

    generate(client, ids[:1])  # 5 = 100 %
    generate(client, ids[:1])  # afvist
    generate(client, ids[:1])  # afvist igen

    assert len(mails) == 2  # kun én mail for 100 %, selvom loftet blev ramt flere gange
    assert "NÅET" in mails[1]["subject"]
    for mail in mails:
        assert "Fantastisk kaffe" not in mail["text"] and "Ida" not in mail["text"]


def test_without_an_admin_email_nothing_is_sent_but_the_cap_still_holds(client):
    caps(client, drafts=Caps(cafe_day=1, cafe_month=100, all_day=100, all_month=100), resend_api_key="k")
    _, mails = use_services(client)
    _, ids = setup_cafe(client)
    use_claude(client, FakeClaude())

    assert generate(client, ids[:1]).status_code == 200
    assert generate(client, ids[:1]).status_code == 429
    assert mails == []


# ---- admin-værktøjet -------------------------------------------------------------


def test_usage_report_shows_each_cafe_and_the_estimated_price(client, capsys):
    caps(client)
    use_services(client)
    _, ids = setup_cafe(client)
    use_claude(client, FakeClaude())
    generate(client, ids)

    async def go():
        await admin._usage(client.app.state.settings, argparse.Namespace(days=30))

    run(client, go)
    out = capsys.readouterr().out

    assert "Test Café" in out
    assert "1000" in out  # tokens ind: 2 udkast x 500
    assert "$0.01" in out  # (1000 x 3 + 200 x 15) / 1.000.000 = 0,006 USD
    assert "i dag      2 af 1000" in out


def test_a_customer_created_from_the_command_line_counts_its_google_fetch(client):
    import httpx as _httpx

    async def go():
        await admin.create_customer(
            email="ny@test.dk", place_id="ChIJtest", http=_httpx.AsyncClient(
                transport=_httpx.MockTransport(lambda request: _httpx.Response(200, json=PLACE))
            ), api_key="k",
        )

    run(client, go)
    assert db_rows(client, "SELECT kind, status FROM usage_events") == [("places_fetch", "done")]


# ---- indstillinger (uden database) -----------------------------------------------


def test_caps_are_read_from_env_and_bad_values_stop_the_server(monkeypatch):
    monkeypatch.setenv("CAP_DRAFTS_CAFE_DAY", "7")
    monkeypatch.setenv("CAP_FETCHES_TOTAL_MONTH", "99")
    monkeypatch.setenv("AI_PAUSED", "1")
    monkeypatch.setenv("ADMIN_EMAIL", "stamgaest.kontakt@gmail.com")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    settings = load_settings()
    assert settings.draft_caps.cafe_day == 7
    assert settings.fetch_caps.all_month == 99
    assert settings.ai_paused and settings.admin_email == "stamgaest.kontakt@gmail.com"

    for bad in ("mange", "-1"):
        monkeypatch.setenv("CAP_DRAFTS_CAFE_DAY", bad)
        with pytest.raises(RuntimeError, match="CAP_DRAFTS_CAFE_DAY"):
            load_settings()
