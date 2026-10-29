"""Samler statistikken for én café ud fra anmeldelserne og deres emner.

Alt regnes ud fra `reviews` og `review_topics`, ikke ud fra øjebliksbilleder. Så virker den samme
kode, når vi en dag får ALLE anmeldelser via Business Profile API, og udviklingen kan regnes bagud i
tiden fra dag ét.
"""

from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.models import review_topics, reviews
from app.services import topics_service

# Så mange måneder tilbage viser grafen.
MONTHS = 12
# En måned med færre anmeldelser end dette får intet gennemsnit. Ét enkelt 1-stjernet ville
# ellers tegne et dramatisk fald, der ikke betyder noget.
MIN_REVIEWS_PER_MONTH = 3


def _float(value) -> float | None:
    """Laver et tal fra databasen (fx Decimal) om til float, eller None."""
    return None if value is None else round(float(value), 2)


def topics_status(restaurant: dict, pending: int, settings: Settings) -> str:
    """Hvor står mærkningen: "analyzing" (kører), "paused" (der mangler nogen, men Claude er slået fra) eller "ready"."""
    started = restaurant.get("topics_started_at")
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=topics_service.STALE_MINUTES)
    if started is not None and started > stale_before:
        return "analyzing"
    if pending and not topics_service.ai_allowed(settings, restaurant):
        return "paused"
    return "ready"


async def build(restaurant: dict, settings: Settings) -> dict:
    """Bygger hele svaret til statistiksiden for caféen."""
    restaurant_id = restaurant["id"]
    version = topics_service.TAXONOMY_VERSION
    summary = await reviews.rating_summary(restaurant_id)
    response = await reviews.response_hours(restaurant_id, restaurant.get("first_import_at"))
    months = await reviews.monthly(restaurant_id, MONTHS)
    counts = await review_topics.topic_counts(restaurant_id, version)
    pending = await review_topics.count_pending(restaurant_id, version)

    by_topic = {key: {"positiv": 0, "negativ": 0, "blandet": 0} for key in topics_service.TOPICS}
    for row in counts:
        if row["topic"] in by_topic and row["sentiment"] in by_topic[row["topic"]]:
            by_topic[row["topic"]][row["sentiment"]] = int(row["n"])
    topics = [
        {
            "key": key,
            "label": topics_service.TOPICS[key][0],
            "positive": c["positiv"],
            "negative": c["negativ"],
            "mixed": c["blandet"],
            "total": c["positiv"] + c["negativ"] + c["blandet"],
        }
        for key, c in by_topic.items()
    ]
    # Mest omtalte først. sorted() er stabil, så emner med lige mange står i listens rækkefølge.
    topics = sorted(topics, key=lambda t: -t["total"])

    return {
        "business_name": restaurant["name"],
        "google_rating": _float(restaurant.get("google_rating")),
        "google_rating_count": restaurant.get("google_rating_count"),
        "review_count": int(summary["total"]),
        "average_rating": _float(summary["average"]),
        "stars": [int(summary[f"stars_{n}"]) for n in range(1, 6)],
        "answered_count": int(summary["answered"]),
        "median_response_hours": _float(response["median"]),
        "response_sample": int(response["sample"]),
        "months": [
            {
                "month": row["month"],
                "count": int(row["count"]),
                "average_rating": _float(row["average"]) if int(row["count"]) >= MIN_REVIEWS_PER_MONTH else None,
            }
            for row in months
        ],
        "min_reviews_per_month": MIN_REVIEWS_PER_MONTH,
        "topics": topics,
        "analyzed_count": await review_topics.count_analyzed(restaurant_id, version),
        "pending_count": pending,
        "topics_status": topics_status(restaurant, pending, settings),
    }
