"""Opretter og opdaterer tabellerne i databasen ("migrationer").

Kør med:  uv run python -m app.db.migrate

Filerne i migrations/ køres i rækkefølge (001_, 002_, ...). Databasen husker i
tabellen schema_migrations hvilke der er kørt, så samme fil aldrig køres to gange.
Man kan derfor køre kommandoen igen og igen uden at skade noget.

Ret ALDRIG i en migration der allerede er kørt. Lav en ny fil i stedet (fx 002_...).
"""

import asyncio
import sys
from pathlib import Path

from app.core.config import load_settings
from app.db import pool

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Et vilkårligt tal, som alle der kører migrationer bruger som "lås". Kører to
# personer migrate samtidig, må kun den ene af gangen oprette tabellerne.
MIGRATION_LOCK_ID = 727_274


def migration_files() -> list[Path]:
    """Finder alle .sql-filer i migrations/, sorteret efter filnavn."""
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda path: path.name)


async def migrate() -> int:
    """Kører de migrationer der ikke er kørt endnu. Returnerer hvor mange der blev kørt."""
    await pool.fetch_all("SELECT 1")  # vækker Neon, hvis den sover

    # Opret tabellen der holder styr på hvad der er kørt (hvis den ikke findes).
    async with pool.transaction() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              filename   TEXT PRIMARY KEY,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

    applied = {row["filename"] for row in await pool.fetch_all("SELECT filename FROM schema_migrations")}
    pending = [path for path in migration_files() if path.name not in applied]

    if not pending:
        print("Databasen er allerede opdateret — ingen nye migrationer.")
        return 0

    ran = 0
    for path in pending:
        sql = path.read_text(encoding="utf-8")

        # SQL'en og "denne fil er kørt" gemmes i samme transaktion. Fejler noget
        # halvvejs, rulles ALT tilbage, og filen tæller ikke som kørt.
        async with pool.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))

            # Tjek igen efter låsen: en anden kan have nået den før os.
            cursor = await conn.execute(
                "SELECT 1 FROM schema_migrations WHERE filename = %s", (path.name,)
            )
            if await cursor.fetchone():
                continue

            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))

        print(f"Kørte migration: {path.name}")
        ran += 1

    print(f"Færdig — {ran} migration(er) kørt.")
    return ran


async def main() -> int:
    """Åbner databasen, kører migrationerne og lukker igen. Returnerer 0 ved succes, 1 ved fejl."""
    settings = load_settings()
    if not settings.database_url:
        print(
            "DATABASE_URL mangler. Kopiér .env.example til .env og indsæt forbindelses-"
            "strengen fra Neon (se afsnittet 'Database' i README).",
            file=sys.stderr,
        )
        return 1

    try:
        await pool.init_pool(pool.build_conninfo(settings.database_url))
        await migrate()
        return 0
    except Exception as exc:
        # Kun typen og en kort besked, aldrig forbindelsesstrengen.
        print(f"Migration fejlede: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await pool.close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
