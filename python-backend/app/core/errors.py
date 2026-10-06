"""Fejlhåndtering: hvad browseren får at se, når noget går galt.

Frontenden læser altid `data.error`, så ALLE fejl svarer med {"error": "tekst"}.

Vigtig regel: teksten til brugeren skriver vi selv. Det der faktisk gik galt
(svar fra Google, SQL-fejl osv.) skrives kun i serverens log, så interne
detaljer ikke lækker til browseren.
"""

import logging

import psycopg
import psycopg_pool
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("review_assistant")


class ApiError(Exception):
    """En fejl vi selv kaster, med en statuskode og en tekst der er sikker at vise for brugeren."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class UpstreamError(ApiError):
    """Google eller Claude fejlede. Bruger 502 = "tjenesten vi kalder svarede ikke ordentligt"."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(status_code, message)


def _json_error(status_code: int, message: str) -> JSONResponse:
    """Bygger svaret {"error": "..."} med den givne statuskode."""
    return JSONResponse(status_code=status_code, content={"error": message})


def _field_name(loc: tuple) -> str:
    """Laver feltets placering om til et navn, fx ("body", "reviews", 0, "id") -> "reviews.0.id"."""
    return ".".join(str(part) for part in loc[1:]) or str(loc[0])


def register_error_handlers(app: FastAPI) -> None:
    """Fortæller appen hvordan hver slags fejl skal laves om til et svar."""

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        """Vores egne fejl: send den tekst vi selv valgte."""
        return _json_error(exc.status_code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        """Ugyldigt input: sig hvilke felter der er forkerte, men ikke hvad klienten sendte."""
        fields = sorted({_field_name(err["loc"]) for err in exc.errors()})
        return _json_error(400, "Ugyldigt eller manglende felt: " + ", ".join(fields) + ".")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        """Standardfejl som "siden findes ikke" (404)."""
        messages = {404: "Ikke fundet.", 405: "Metoden er ikke tilladt her."}
        return _json_error(exc.status_code, messages.get(exc.status_code, "Forespørgslen kunne ikke gennemføres."))

    @app.exception_handler(psycopg_pool.PoolTimeout)
    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(_: Request, exc: Exception):
        """Databasen kunne ikke nås. Neon lukker ned når den ikke bruges, så det kan være midlertidigt."""
        logger.error("Databasen er ikke tilgængelig: %s", type(exc).__name__)
        return _json_error(503, "Databasen er ikke tilgængelig lige nu. Prøv igen om et øjeblik.")

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception):
        """Alt andet (en fejl i vores kode): log alt, men vis kun en generel tekst."""
        logger.error("Uventet fejl", exc_info=exc)
        return _json_error(500, "Der skete en uventet fejl på serveren.")
