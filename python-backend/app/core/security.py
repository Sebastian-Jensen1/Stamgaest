"""Sikkerhed omkring hele appen.

Tre ting sker for ALLE requests:
  1. Der tilføjes sikkerhedsheaders til svaret (SecurityHeadersMiddleware).
  2. Requests med for stor body afvises (BodySizeLimitMiddleware).
  3. Ændrende kald fra en fremmed hjemmeside afvises (OriginCheckMiddleware).

En "middleware" er kode der sidder mellem browseren og vores routes og ser alle
requests og svar igennem.
"""

import base64
import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Største tilladte request: 256 KB. En normal request er et par hundrede bytes.
MAX_BODY_BYTES = 256 * 1024

# Finder <script>...</script> uden src= (altså JavaScript skrevet direkte i HTML-filen).
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def inline_script_hashes(index_html: Path) -> list[str]:
    """Regner hash (et fingeraftryk) ud for scriptet i index.html.

    Browseren må kun køre scripts hvis fingeraftryk står på listen. Får en
    angriber sneget et script ind i en anmeldelse, har det et andet fingeraftryk
    og bliver blokeret.
    """
    if not index_html.is_file():
        return []
    # Browseren regner på tekst med \n som linjeskift, så vi gør det samme.
    html = index_html.read_text(encoding="utf-8").replace("\r\n", "\n")
    return [
        "'sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode() + "'"
        for m in _INLINE_SCRIPT.finditer(html)
    ]


def build_csp(script_hashes: list[str]) -> str:
    """Bygger Content-Security-Policy: en liste over hvad siden må indlæse og køre.

    Alt er forbudt som udgangspunkt, og så åbnes der kun for det siden har brug for.
    """
    return "; ".join(
        [
            "default-src 'none'",  # som udgangspunkt: intet må indlæses
            "script-src 'self' " + " ".join(script_hashes),  # kun vores eget script
            "style-src 'self' 'unsafe-inline'",  # siden bruger style-attributter
            "img-src 'self' data:",
            "connect-src 'self'",  # fetch() må kun kalde vores egen server
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",  # siden må ikke vises i en iframe (clickjacking)
        ]
    )


# Adresserne til de indbyggede API-oversigter (findes kun hvis ENABLE_DOCS=1).
DOCS_PATHS = ("/docs", "/redoc")


def build_docs_csp() -> str:
    """En lempeligere CSP til /docs og /redoc, som kun bruges når ENABLE_DOCS=1.

    De to sider henter deres design og JavaScript fra et eksternt CDN og har et
    indlejret script. Med den strenge CSP ville de blive helt hvide. De viser ingen
    brugerdata, og de er slukket som standard, så det er en acceptabel undtagelse.
    """
    return "; ".join(
        [
            "default-src 'none'",
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
            "font-src https://fonts.gstatic.com",
            "img-src 'self' data: https://fastapi.tiangolo.com",
            "worker-src blob:",
            "connect-src 'self'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
        ]
    )


def _is_docs_path(path: str) -> bool:
    """Er dette en af API-oversigternes adresser (/docs, /redoc eller noget under dem)?"""
    return any(path == docs or path.startswith(docs + "/") for docs in DOCS_PATHS)


class SecurityHeadersMiddleware:
    """Tilføjer sikkerhedsheaders til hvert eneste svar."""

    def __init__(self, app: ASGIApp, csp: str, docs_csp: str | None = None):
        self.app = app
        self.csp = csp
        self.docs_csp = docs_csp  # None = /docs er slukket, så der er ingen undtagelse

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Kører for hver request. Sender den videre og retter svarets headers på vejen tilbage."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_api = scope["path"].startswith("/api/")
        csp = self.docs_csp if self.docs_csp and _is_docs_path(scope["path"]) else self.csp

        async def send_with_headers(message: Message) -> None:
            """Tilføjer headers når svaret begynder at blive sendt."""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = csp
                headers["X-Content-Type-Options"] = "nosniff"  # browseren må ikke gætte filtype
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Cross-Origin-Resource-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                if is_api:
                    # API-svar er forretningsdata og skal ikke gemmes i browserens cache.
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class OriginCheckMiddleware:
    """Afviser ændrende kald (POST osv.) der kommer fra en ANDEN hjemmeside end vores egen.

    Uden det kunne en fremmed side få en logget-ind brugers browser til at sende kald til
    os ("CSRF"), og browseren ville selv lægge login-cookien på. Browsere skriver altid
    hvilken side et kald kommer fra i headeren Origin, så vi sammenligner den med vores egen
    adresse. Cookien er desuden SameSite=Lax, som er en anden, uafhængig sikring.
    """

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Kører for hver request. Afviser med 403 hvis Origin ikke passer til Host."""
        if scope["type"] == "http" and scope["method"] not in self.SAFE_METHODS:
            headers = dict(scope["headers"])
            origin = headers.get(b"origin")
            # Origin mangler hos programmer som curl (og hos en side der kalder sig selv i
            # ældre browsere). Så er der ingen fremmed side involveret, og vi lader den passe.
            if origin is not None and urlparse(origin.decode("latin-1")).netloc.encode() != headers.get(b"host"):
                await self._reject(send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        """Sender svaret 403 "Forespørgslen er afvist"."""
        body = '{"error":"Forespørgslen kom fra en anden side og blev afvist."}'.encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    """Bruges internt når en request viser sig at være for stor."""


class BodySizeLimitMiddleware:
    """Afviser requests der er større end grænsen (svarer 413)."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Kører for hver request. Tjekker størrelsen både på forhånd og mens data læses."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Tjek 1: klienten oplyser selv en størrelse i headeren Content-Length.
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            """Tæller bytes efterhånden som de læses. Tjek 2, fordi Content-Length kan være løgn."""
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            """Husker om vi er begyndt at svare (så vi ikke svarer to gange)."""
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        """Sender svaret 413 "Forespørgslen er for stor"."""
        body = '{"error":"Forespørgslen er for stor."}'.encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
