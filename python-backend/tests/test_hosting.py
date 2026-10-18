"""Tests for det der skal virke hos en host: adresse, health check og kundens rigtige IP.

Ingen af dem kræver database, Google eller Claude.
"""

import ipaddress

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import CLOUDFLARE_IP_RANGES, load_settings
from app.main import create_app
from tests.conftest import FakeClaude, make_settings

LOGIN = {"email": "ejer@cafe.dk", "password": "forkert-kode"}


@pytest.fixture
def env(monkeypatch):
    """Et rent miljø for de indstillinger testene her ændrer, uanset hvad der står i .env."""
    for name in ("HOST", "TRUSTED_PROXIES", "COOKIE_SECURE", "APP_BASE_URL", "PORT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _client(**overrides) -> TestClient:
    """En testklient uden database med de givne indstillinger."""
    app = create_app(make_settings(**overrides))
    app.state.http = httpx.AsyncClient()
    app.state.anthropic = FakeClaude()
    return TestClient(app, base_url="http://localhost")


def _block_ip(client: TestClient, ip: str) -> None:
    """Fylder grænsen for forkerte logins op for én IP, så næste login fra den afvises med 429."""
    limiter = client.app.state.limits.login_ip
    for _ in range(limiter.max_events):
        limiter.record(ip)


# ---- adresse og port -----------------------------------------------------------


def test_server_only_listens_on_this_machine_by_default(env):
    settings = load_settings()
    assert settings.host == "127.0.0.1"
    assert settings.trusted_proxies == ("127.0.0.1",)


def test_host_reads_the_port_the_host_gives_us(env):
    env.setenv("PORT", "10000")
    assert load_settings().port == 10000


def test_listening_on_the_network_without_https_is_refused(env):
    env.setenv("HOST", "0.0.0.0")
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        load_settings()


def test_listening_on_the_network_works_behind_https(env):
    env.setenv("HOST", "0.0.0.0")
    env.setenv("COOKIE_SECURE", "1")
    env.setenv("APP_BASE_URL", "https://app.stamgaest.com")
    assert load_settings().host == "0.0.0.0"


@pytest.mark.parametrize("host", ["localhost", "min-server", "0.0.0.0; rm -rf"])
def test_host_must_be_an_ip_address(env, host):
    env.setenv("HOST", host)
    with pytest.raises(RuntimeError, match="HOST"):
        load_settings()


# ---- TRUSTED_PROXIES -------------------------------------------------------------


def test_trusted_proxies_accepts_addresses_networks_and_cloudflare(env):
    env.setenv("TRUSTED_PROXIES", "127.0.0.1, 10.0.0.0/8, cloudflare")
    proxies = load_settings().trusted_proxies
    assert proxies[:2] == ("127.0.0.1", "10.0.0.0/8")
    assert proxies[2:] == CLOUDFLARE_IP_RANGES


@pytest.mark.parametrize("value", ["*", "127.0.0.1,*"])
def test_trusting_everyone_is_refused(env, value):
    # "*" ville lade enhver selv vælge sin IP og dermed slippe uden om grænsen for gætte-forsøg.
    env.setenv("TRUSTED_PROXIES", value)
    with pytest.raises(RuntimeError, match=r"\*"):
        load_settings()


@pytest.mark.parametrize("value", ["10.0.0.0/33", "cloudfare", "render.com"])
def test_a_typo_in_trusted_proxies_stops_the_server(env, value):
    env.setenv("TRUSTED_PROXIES", value)
    with pytest.raises(RuntimeError, match="TRUSTED_PROXIES"):
        load_settings()


def test_cloudflare_ranges_are_valid_networks():
    assert CLOUDFLARE_IP_RANGES
    for net in CLOUDFLARE_IP_RANGES:
        ipaddress.ip_network(net)


# ---- health check ----------------------------------------------------------------


def test_health_answers_ok_without_login_or_database(unit_client):
    response = unit_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_health_still_checks_the_host_header(unit_client):
    assert unit_client.get("/api/health", headers={"Host": "evil.example.com"}).status_code == 400


# ---- kundens rigtige IP bag en proxy ---------------------------------------------
# TestClient kalder altid fra adressen "testclient". Står den i trusted_proxies, opfører den
# sig som en proxy (fx Cloudflare-tunnelen eller hosten).


def test_ip_from_a_trusted_proxy_is_used():
    client = _client(trusted_proxies=("testclient",))
    _block_ip(client, "203.0.113.7")
    response = client.post("/api/auth/login", json=LOGIN, headers={"X-Forwarded-For": "203.0.113.7"})
    assert response.status_code == 429


def test_forwarded_ip_from_an_unknown_caller_is_ignored():
    # Uden en kendt proxy foran kunne en angriber skrive en ny falsk IP i hvert kald og gætte løs.
    client = _client()  # stoler kun på 127.0.0.1, ikke på "testclient"
    _block_ip(client, "testclient")
    response = client.post("/api/auth/login", json=LOGIN, headers={"X-Forwarded-For": "203.0.113.7"})
    assert response.status_code == 429  # den rigtige afsender er stadig spærret


def test_a_fake_ip_in_front_of_the_real_one_is_ignored():
    # Angriberen skriver selv "1.2.3.4". Proxyen tilføjer angriberens rigtige IP til sidst.
    client = _client(trusted_proxies=("testclient",))
    _block_ip(client, "198.51.100.9")
    response = client.post(
        "/api/auth/login", json=LOGIN, headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.9"}
    )
    assert response.status_code == 429


def test_cloudflare_in_front_of_the_host_gives_the_customers_ip():
    # Kunde -> Cloudflare -> hostens proxy -> os. Hostens proxy tilføjer Cloudflares IP til sidst.
    client = _client(trusted_proxies=("testclient", *CLOUDFLARE_IP_RANGES))
    _block_ip(client, "203.0.113.7")
    response = client.post(
        "/api/auth/login", json=LOGIN, headers={"X-Forwarded-For": "203.0.113.7, 162.158.1.1"}
    )
    assert response.status_code == 429

