"""Et endpoint hosten bruger til at tjekke at serveren kører ("health check").

Det kræver ikke login og svarer kun "ok". Det spørger bevidst ikke databasen: hosten kalder
det måske hvert halve minut, og så ville den fælles Neon-database aldrig få lov at sove (og
koste mere). Det fortæller heller ikke noget om versioner eller opsætning.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """GET /api/health  Svarer {"status": "ok"}, så længe serveren kører."""
    return {"status": "ok"}
