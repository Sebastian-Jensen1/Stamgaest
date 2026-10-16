"""Tester caféens indstillinger for svar, profilen fra hjemmesiden og at udkastene bruger dem."""

import dataclasses
import json
from types import SimpleNamespace

import httpx

from app.core.config import Caps
from tests.conftest import (
    PLACE,
    FakeClaude,
    db_execute,
    db_rows,
    logged_in,
    use_claude,
)

WEBSITE = "https://www.testcafe.dk/"


ABOUT = {"concept": "Café med morgenmad", "items": ["Croissant"], "traits": ["Bager selv brødet"], "atmosphere": "Hyggeligt"}
NO_ABOUT = {"concept": "", "items": [], "traits": [], "atmosphere": ""}


def settings_body(**overrides):
    """Et komplet sæt indstillinger, som browseren ville sende det."""
    values = {
        "replyLength": "auto", "tone": "", "signature": "", "avoid": "", "contact": "", "examples": "",
        "about": NO_ABOUT,
    }
    values.update(overrides)
    return values


def cafe_with_reviews(client, website=WEBSITE):
    """Logger en kunde ind og henter anmeldelser fra en falsk Google (med hjemmeside). Giver id'erne."""
    customer = logged_in(client)
    place = {**PLACE, "websiteUri": website} if website else PLACE
    client.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=place))
    )
    response = client.post("/api/reviews/refresh", json={})
    assert response.status_code == 200, response.text
    return customer, [r["id"] for r in response.json()["reviews"]]


class FakeProfileClaude:
    """En falsk Claude, der "læser hjemmesiden" og svarer med en profil i det aftalte format."""

    def __init__(self, data=None, stop_reason="end_turn", raise_error=None):
        self.calls = []
        self.data = data if data is not None else {
            "website_readable": True,
            "concept": "Café med morgenmad og kaffe.",
            "items": ["Croissant", "Cortado", "croissant"],
            "traits": ["Bager selv brødet"],
            "atmosphere": "Hyggeligt hjørne på Nørrebro.",
        }
        self.stop_reason = stop_reason
        self.raise_error = raise_error
        self.messages = self

    def with_options(self, **kwargs):
        """Den rigtige klient giver en kopi med andre indstillinger. Den falske er bare sig selv."""
        self.options = kwargs
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_error:
            raise self.raise_error
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.data))],
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=8000, output_tokens=300),
        )

    async def close(self):
        """Gør ingenting."""


def build(client):
    return client.post("/api/reply-settings/profile", json={})


def current(client):
    return client.get("/api/reply-settings").json()


# ---- gemme og hente --------------------------------------------------------------


def test_a_new_cafe_gets_sensible_defaults(client):
    cafe_with_reviews(client)
    data = current(client)
    assert data["replyLength"] == "auto" and data["tone"] == ""
    assert data["about"] == NO_ABOUT and data["aboutSuggestion"] is None
    assert data["profileStatus"] == "none"
    assert data["websiteUrl"] == WEBSITE  # hentet fra Google


def test_settings_are_saved_and_saving_facts_approves_them(client):
    cafe_with_reviews(client)
    body = settings_body(
        replyLength="lang", tone="uformel", signature="Kh. Mette", avoid="Rabatter",
        contact="hej@test.dk", examples="Tak for besøget!", about=ABOUT,
    )
    response = client.put("/api/reply-settings", json=body)

    assert response.status_code == 200
    data = current(client)
    for key, value in body.items():
        assert data[key] == value
    assert data["factsApprovedAt"] is not None


def test_settings_are_validated(client):
    cafe_with_reviews(client)
    for bad in (
        settings_body(replyLength="uendelig"),
        settings_body(tone="x" * 201),
        settings_body(signature="x" * 151),
        settings_body(examples="x" * 3001),
        settings_body(about={**NO_ABOUT, "concept": "x" * 301}),
        settings_body(about={**NO_ABOUT, "items": ["x"] * 21}),
        settings_body(about={**NO_ABOUT, "items": ["x" * 81]}),
        settings_body(about={**NO_ABOUT, "traits": ["x"] * 13}),
    ):
        assert client.put("/api/reply-settings", json=bad).status_code == 400
    assert db_rows(client, "SELECT count(*) FROM reply_settings") == [(0,)]


def test_settings_require_login(client):
    client.cookies.clear()
    assert client.get("/api/reply-settings").status_code == 401
    assert client.put("/api/reply-settings", json=settings_body()).status_code == 401
    assert build(client).status_code == 401


# ---- profil fra hjemmesiden ------------------------------------------------------


def test_saved_facts_are_cleaned_like_everything_else(client):
    cafe_with_reviews(client)
    client.put("/api/reply-settings", json=settings_body(about={
        "concept": "  Café   med kaffe ", "items": ["Kaffe", "kaffe", "  "], "traits": [], "atmosphere": "",
    }))
    assert current(client)["about"] == {"concept": "Café med kaffe", "items": ["Kaffe"], "traits": [], "atmosphere": ""}


def test_old_facts_text_from_before_is_shown_in_the_new_fields(client):
    customer, _ = cafe_with_reviews(client)
    db_execute(
        client,
        "INSERT INTO reply_settings (restaurant_id, facts) VALUES (%s, %s)",
        (customer["restaurant_id"], "Koncept: Bageri\nKendte retter og drikke: Kanelsnegl, Kaffe"),
    )
    assert current(client)["about"] == {"concept": "Bageri", "items": ["Kanelsnegl", "Kaffe"], "traits": [], "atmosphere": ""}


def test_building_a_profile_gives_a_suggestion_that_is_not_used_until_chosen(client):
    _, ids = cafe_with_reviews(client)
    profile_claude = use_claude(client, FakeProfileClaude())

    assert build(client).status_code == 202

    data = current(client)
    assert data["profileStatus"] == "ready"
    assert data["aboutSuggestion"]["items"] == ["Croissant", "Cortado"]  # renset: dubletten er væk
    assert data["aboutSuggestion"]["traits"] == ["Bager selv brødet"]
    assert data["about"] == NO_ABOUT  # IKKE godkendt endnu

    # Claude måtte kun læse caféens eget domæne
    tool = profile_claude.calls[0]["tools"][0]
    assert tool["type"] == "web_fetch_20260209"
    assert tool["allowed_domains"] == ["testcafe.dk"]
    assert tool["max_uses"] <= 5

    # Et udkast bruger IKKE forslaget, før det er godkendt
    drafts = use_claude(client, FakeClaude())
    client.post("/api/generate", json={"reviews": [{"id": ids[0]}]})
    assert "Croissant" not in str(drafts.calls[0]["system"])

    # Ejeren vælger fra forslaget og gemmer: nu er det godkendt og bruges
    client.put("/api/reply-settings", json=settings_body(about=data["aboutSuggestion"], clearSuggestion=True))
    approved = current(client)
    assert approved["aboutSuggestion"] is None and "Croissant" in approved["about"]["items"]
    client.post("/api/generate", json={"reviews": [{"id": ids[0]}]})
    assert "Croissant" in str(drafts.calls[1]["system"])


def test_building_a_profile_is_logged_and_counted(client):
    cafe_with_reviews(client)
    use_claude(client, FakeProfileClaude())
    build(client)
    assert db_rows(
        client, "SELECT status, input_tokens, output_tokens FROM usage_events WHERE kind = 'claude_profile'"
    ) == [("done", 8000, 300)]


def test_an_unreadable_website_fails_with_a_clear_message(client):
    cafe_with_reviews(client)
    use_claude(client, FakeProfileClaude(data={
        "website_readable": False, "concept": "", "items": [], "traits": [], "atmosphere": "",
    }))
    build(client)
    data = current(client)
    assert data["profileStatus"] == "failed"
    assert "Skriv fakta om stedet selv" in data["profileMessage"]
    assert data["aboutSuggestion"] is None


def test_a_claude_error_while_building_is_shown_safely_and_still_counts(client):
    from app.core.errors import UpstreamError

    cafe_with_reviews(client)
    use_claude(client, FakeProfileClaude(raise_error=UpstreamError("Der er travlt lige nu. Prøv igen om lidt.")))
    build(client)
    data = current(client)
    assert data["profileStatus"] == "failed"
    assert data["profileMessage"] == "Der er travlt lige nu. Prøv igen om lidt."
    assert db_rows(client, "SELECT status FROM usage_events WHERE kind = 'claude_profile'") == [("failed",)]


def test_a_truncated_or_paused_answer_never_becomes_a_draft(client):
    cafe_with_reviews(client)
    claude = use_claude(client, FakeProfileClaude(stop_reason="pause_turn"))
    build(client)
    assert current(client)["profileStatus"] == "failed"
    assert len(claude.calls) == 4  # første kald + højst 3 fortsættelser, så det kan aldrig løbe løbsk


def test_no_website_on_google_means_no_profile_and_no_cost(client):
    cafe_with_reviews(client, website=None)
    claude = use_claude(client, FakeProfileClaude())
    response = build(client)
    assert response.status_code == 409
    assert "Skriv fakta om stedet selv" in response.json()["error"]
    assert claude.calls == []


def test_a_bad_website_from_google_is_ignored(client):
    cafe_with_reviews(client, website="javascript:alert(1)")
    assert current(client)["websiteUrl"] is None


def test_only_one_profile_can_be_built_at_a_time(client):
    customer, _ = cafe_with_reviews(client)
    db_execute(
        client,
        "INSERT INTO reply_settings (restaurant_id, profile_status, profile_started_at) VALUES (%s, 'building', now())",
        (customer["restaurant_id"],),
    )
    claude = use_claude(client, FakeProfileClaude())

    response = build(client)

    assert response.status_code == 409
    assert claude.calls == []
    assert db_rows(client, "SELECT status FROM usage_events WHERE kind = 'claude_profile'") == [("skipped",)]

    # Hænger en gammel bygning (fx efter en genstart), kan man prøve igen efter 10 minutter.
    db_execute(client, "UPDATE reply_settings SET profile_started_at = now() - interval '11 minutes'")
    assert build(client).status_code == 202


def test_profile_building_respects_kill_switches_and_caps(client):
    customer, _ = cafe_with_reviews(client)
    claude = use_claude(client, FakeProfileClaude())
    original = client.app.state.settings
    try:
        client.app.state.settings = dataclasses.replace(original, ai_paused=True)
        assert build(client).status_code == 503

        client.app.state.settings = dataclasses.replace(
            original, profile_caps=Caps(cafe_day=1, cafe_month=10, all_day=10, all_month=10)
        )
        assert build(client).status_code == 202
        refused = build(client)
        assert refused.status_code == 429
        assert "mange gange i dag" in refused.json()["error"]
    finally:
        client.app.state.settings = original
    assert len(claude.calls) == 1

    db_execute(client, "UPDATE restaurants SET ai_enabled = false WHERE id = %s", (customer["restaurant_id"],))
    assert build(client).status_code == 403


def test_discarding_a_suggestion(client):
    cafe_with_reviews(client)
    use_claude(client, FakeProfileClaude())
    build(client)
    assert client.delete("/api/reply-settings/suggestion").status_code == 200
    assert current(client)["aboutSuggestion"] is None


# ---- udkastene bruger profilen ---------------------------------------------------


def test_drafts_use_the_cafes_style_facts_and_examples(client):
    _, ids = cafe_with_reviews(client)
    client.put("/api/reply-settings", json=settings_body(
        replyLength="kort", tone="uformel og lidt fræk", signature="Kh. Mette", avoid="Rabatter",
        about=ABOUT, examples="Tak, kom igen!",
    ))
    claude = use_claude(client, FakeClaude())

    client.post("/api/generate", json={"reviews": [{"id": ids[0]}]})

    system = claude.calls[0]["system"]
    profile = system[1]["text"]
    assert "2-3 sætninger" in profile and "Tone: uformel og lidt fræk" in profile
    assert "Kh. Mette" in profile and "Skriv aldrig: Rabatter" in profile
    assert "Retter og drikke: Croissant" in profile and "- Bager selv brødet" in profile
    assert "<eksempler>\nTak, kom igen!\n</eksempler>" in profile
    assert system[1]["cache_control"] == {"type": "ephemeral"}
    # Tonen gemmes ved udkastet, så `stats` kan se hvilken tone der blev brugt
    assert db_rows(client, "SELECT tone FROM replies") == [("uformel og lidt fræk",)]


def test_drafts_learn_from_the_replies_the_cafe_actually_sent(client):
    _, ids = cafe_with_reviews(client)
    claude = use_claude(client, FakeClaude())
    client.post("/api/generate", json={"reviews": [{"id": ids[0]}]})
    client.post(f"/api/reviews/{ids[0]}/send", json={"finalText": "Tusind tak, Ida! Kh. Mette"})

    client.post("/api/generate", json={"reviews": [{"id": ids[1]}]})

    last_block = claude.calls[-1]["system"][-1]
    assert "<tidligere_svar>" in last_block["text"] and "Tusind tak, Ida! Kh. Mette" in last_block["text"]
    assert "cache_control" not in last_block  # skifter hele tiden, så den står efter cachen


# ---- "Prøv et udkast" ------------------------------------------------------------


def preview(client, review_id, **settings):
    return client.post("/api/generate/preview", json={"reviewId": review_id, "settings": settings_body(**settings)})


def test_preview_uses_the_unsaved_settings_and_saves_nothing(client):
    _, ids = cafe_with_reviews(client)
    claude = use_claude(client, FakeClaude())

    response = preview(client, ids[0], signature="Kh. Prøve", about=ABOUT)

    assert response.status_code == 200
    assert response.json() == {"draft": "Udkast nr. 1"}
    assert "Kh. Prøve" in claude.calls[0]["system"][1]["text"]
    assert db_rows(client, "SELECT count(*) FROM replies") == [(0,)]  # intet udkast gemt
    assert db_rows(client, "SELECT count(*) FROM reply_settings") == [(0,)]  # indstillingerne er ikke gemt
    assert db_rows(client, "SELECT kind, status FROM usage_events WHERE kind = 'claude_draft'") == [("claude_draft", "done")]


def test_preview_is_validated_and_cleaned_like_saved_settings(client):
    _, ids = cafe_with_reviews(client)
    claude = use_claude(client, FakeClaude())
    assert preview(client, ids[0], signature="x" * 151).status_code == 400
    preview(client, ids[0], about={**NO_ABOUT, "items": ["Kaffe", "kaffe"]})
    assert "Retter og drikke: Kaffe\n" in claude.calls[0]["system"][1]["text"]


def test_preview_respects_kill_switches_and_caps(client):
    customer, ids = cafe_with_reviews(client)
    claude = use_claude(client, FakeClaude())
    original = client.app.state.settings
    try:
        client.app.state.settings = dataclasses.replace(original, ai_paused=True)
        assert preview(client, ids[0]).status_code == 503
        client.app.state.settings = dataclasses.replace(
            original, draft_caps=Caps(cafe_day=0, cafe_month=10, all_day=10, all_month=10)
        )
        assert preview(client, ids[0]).status_code == 429
    finally:
        client.app.state.settings = original
    db_execute(client, "UPDATE restaurants SET ai_enabled = false WHERE id = %s", (customer["restaurant_id"],))
    assert preview(client, ids[0]).status_code == 403
    assert claude.calls == []


def test_preview_of_an_unknown_review_costs_nothing(client):
    cafe_with_reviews(client)
    claude = use_claude(client, FakeClaude())
    response = preview(client, "00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert claude.calls == []
    assert db_rows(client, "SELECT count(*) FROM usage_events WHERE kind = 'claude_draft'") == [(0,)]
