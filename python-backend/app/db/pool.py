"""Forbindelsen til databasen (Postgres hos Neon).

Al kontakt med databasen går gennem dette modul. Resten af koden bruger kun
fetch_all, fetch_one og transaction herfra.

En "pool" er et lille lager af åbne forbindelser, der genbruges. Det er meget
hurtigere end at åbne en ny forbindelse til Neon for hvert kald.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

import certifi
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

logger = logging.getLogger("review_assistant")

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_pool: AsyncConnectionPool | None = None  # den ene delte pool; oprettes af init_pool()


def build_conninfo(database_url: str) -> str:
    """Gør forbindelsesstrengen klar og bestemmer kryptering ud fra HVEM vi taler med.

    - Ekstern database (Neon): kræver ALTID gyldigt certifikat, uanset hvad der står i
      strengen. Så kan en streng med fx sslmode=disable ikke slå sikkerheden fra.
    - Lokal database: kryptering bruges hvis den findes, men kræves ikke.
    """
    params = conninfo_to_dict(database_url)
    host = params.get("host") or "localhost"

    if host in LOCAL_HOSTS or host.startswith("/"):  # "/" = unix socket, altså lokal
        params["sslmode"] = "prefer"
    else:
        params["sslmode"] = "verify-full"  # tjek certifikat OG at navnet passer
        params["sslrootcert"] = certifi.where()  # de certifikater vi stoler på

    # Neon kan være et sekund om at vågne, så vi venter op til 10 sekunder.
    params["connect_timeout"] = 10
    return make_conninfo(**params)


async def init_pool(conninfo: str, *, wait_seconds: float = 30) -> None:
    """Åbner den delte pool og venter til der er forbindelse. Fejler tydeligt hvis det ikke lykkes."""
    global _pool
    if _pool is not None:
        raise RuntimeError("Databasepoolen er allerede åben.")

    pool = AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=10,
        kwargs={
            "row_factory": dict_row,  # rækker kommer som dicts: row["name"]
            # Slår "prepared statements" fra, fordi Neons pooler ikke kan lide dem.
            "prepare_threshold": None,
        },
        # Tjekker at en forbindelse stadig lever, før den lånes ud. Neon lukker
        # inaktive forbindelser, og uden tjekket ville første kald efter en pause fejle.
        check=AsyncConnectionPool.check_connection,
        timeout=10,  # så længe venter et kald på en ledig forbindelse
        open=False,
        name="review-assistant",
    )
    try:
        await pool.open(wait=True, timeout=wait_seconds)
    except PoolTimeout:
        await pool.close()
        # "from None" skjuler den tekniske fejl, så forbindelsesstrengen aldrig
        # kan komme med i en fejlbesked.
        raise RuntimeError(
            "Kunne ikke forbinde til databasen. Tjek DATABASE_URL og din "
            "internetforbindelse (se afsnittet 'Database' i README)."
        ) from None
    _pool = pool


async def close_pool() -> None:
    """Lukker poolen og alle forbindelser (når serveren slukkes)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _get_pool() -> AsyncConnectionPool:
    """Giver den åbne pool, eller fejler hvis init_pool() ikke er kaldt."""
    if _pool is None:
        raise RuntimeError("Databasepoolen er ikke åbnet.")
    return _pool


async def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[dict]:
    """Kører en SQL-forespørgsel og returnerer ALLE rækker som en liste af dicts.

    Værdier sendes ALTID i `params` og skrives som %s i SQL'en, aldrig sat ind i
    teksten med f-strings. Det gør SQL-injection umuligt.
    """
    async with _get_pool().connection() as conn:
        cursor = await conn.execute(sql, params)
        return await cursor.fetchall()


async def fetch_one(sql: str, params: Sequence[Any] | None = None) -> dict | None:
    """Som fetch_all, men returnerer kun den første række (eller None hvis der ingen er)."""
    rows = await fetch_all(sql, params)
    return rows[0] if rows else None


async def fetch_one_required(sql: str, params: Sequence[Any] | None = None) -> dict:
    """Som fetch_one, men fejler hvis der ingen række kom.

    Bruges til INSERT ... RETURNING, som altid giver en række tilbage.
    """
    row = await fetch_one(sql, params)
    if row is None:
        raise RuntimeError("Forventede en række fra databasen, men fik ingen.")
    return row


@asynccontextmanager
async def transaction() -> AsyncIterator[psycopg.AsyncConnection]:
    """Kører flere forespørgsler som ÉN enhed: enten lykkes alle, eller ingen.

    Bruges sådan:  `async with transaction() as conn: await conn.execute(...)`
    Går alt godt, gemmes ændringerne. Kastes en fejl, rulles alt tilbage. Forbindelsen
    afleveres tilbage til poolen i begge tilfælde.
    """
    async with _get_pool().connection() as conn:
        yield conn
