"""Selve appen. Her samles alle delene til én FastAPI-app.

- routers/  = de adresser browseren kan kalde (login, anmeldelser, udkast)
- core/     = sikkerhed og fejlhåndtering
- db/       = forbindelsen til databasen

Serveren startes med `uv run python -m app` (se __main__.py).
"""

from contextlib import asynccontextmanager

import anthropic
import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import Settings, load_settings
from app.core.errors import register_error_handlers
from app.core.rate_limit import Limits
from app.core.security import (
    BodySizeLimitMiddleware,
    OriginCheckMiddleware,
    SecurityHeadersMiddleware,
    build_csp,
    build_docs_csp,
    inline_script_hashes,
)
from app.db import pool as db_pool
from app.routers import auth, generate, reviews


def create_app(settings: Settings | None = None) -> FastAPI:
    """Bygger og returnerer appen. Testene bruger den også, med egne indstillinger."""
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Kører når serveren starter (før `yield`) og når den slukkes (efter)."""
        # Fejl tydeligt med det samme, hvis databasen ikke er sat op, i stedet for
        # først ved det første kald.
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL mangler. Kopiér .env.example til .env og indsæt forbindelses-"
                "strengen fra Neon (se afsnittet 'Database' i README)."
            )
        await db_pool.init_pool(db_pool.build_conninfo(settings.database_url))

        # Én delt HTTP-klient til Google. Den har timeouts, og den følger ikke
        # redirects, så API-nøglen aldrig sendes videre til en anden adresse.
        http = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False)
        claude = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=60.0, max_retries=2)
            if settings.anthropic_api_key
            else None
        )
        app.state.http = http
        app.state.anthropic = claude
        try:
            yield  # ← serveren kører her
        finally:
            await http.aclose()
            if claude is not None:
                await claude.close()
            await db_pool.close_pool()

    # /docs og /openapi.json viser hele API'et. Nyttigt under udvikling, men de er
    # slukket, medmindre ENABLE_DOCS=1 er sat i .env.
    docs = {} if settings.enable_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Stamgæst", lifespan=lifespan, **docs)
    app.state.settings = settings
    app.state.limits = Limits()  # tællere til at begrænse gætte-forsøg og udkast

    register_error_handlers(app)

    app.include_router(auth.router, prefix="/api")
    app.include_router(generate.router, prefix="/api")
    app.include_router(reviews.router, prefix="/api")

    # Server siden (index.html). Kun mappen public/ er tilgængelig, ikke koden eller .env.
    if settings.public_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.public_dir, html=True), name="static")

    # Middleware kører i omvendt rækkefølge af hvordan de tilføjes: den SIDSTE
    # tilføjede møder requesten FØRST.
    app.add_middleware(BodySizeLimitMiddleware)
    # Afviser ændrende kald der kommer fra en anden hjemmeside end vores egen (CSRF).
    app.add_middleware(OriginCheckMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware,
        csp=build_csp(inline_script_hashes(settings.public_dir / "index.html")),
        # /docs og /redoc får en lempeligere CSP, men kun hvis de er tændt.
        docs_csp=build_docs_csp() if settings.enable_docs else None,
    )
    # Afviser requests hvis Host-headeren ikke er en vi kender (standard: localhost
    # og 127.0.0.1). Det beskytter mod at en fremmed hjemmeside får din browser til
    # at kalde din lokale server via sit eget domæne ("DNS rebinding").
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    return app


app = create_app()  # den færdige app som uvicorn starter
