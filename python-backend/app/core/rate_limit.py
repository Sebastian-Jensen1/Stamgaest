"""Begrænsning af hvor ofte noget må ske ("rate limiting").

Bruges til at stoppe én der gætter adgangskoder, og til at holde udgifterne til Claude nede.
Tællerne ligger i hukommelsen, så de nulstilles når serveren genstarter, og de gælder kun
for én serverproces. Kører I senere flere processer, skal de flyttes til databasen.
"""

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


def describe_wait(seconds: int) -> str:
    """Gør et ventetids-tal til tekst, fx 90 -> "90 sekunder" og 600 -> "10 minutter"."""
    if seconds >= 120:
        return f"{math.ceil(seconds / 60)} minutter"
    return f"{seconds} sekunder"


class RateLimiter:
    """Tillader højst `max_events` hændelser pr. nøgle inden for `window_seconds`.

    En nøgle er fx en e-mail, en IP-adresse eller et bruger-id. Brug check() for at spørge
    "må dette ske?" og record() for at skrive at det skete.
    """

    def __init__(self, max_events: int, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.max_events = max_events
        self.window = window_seconds
        self._clock = clock
        self._events: dict[str, deque[tuple[float, int]]] = {}

    def _live(self, key: str, now: float) -> deque | None:
        """Giver nøglens hændelser inden for tidsvinduet og fjerner de gamle."""
        events = self._events.get(key)
        if events is None:
            return None
        while events and now - events[0][0] >= self.window:
            events.popleft()
        if not events:
            del self._events[key]
            return None
        return events

    def check(self, key: str, cost: int = 1) -> int | None:
        """Spørger om `cost` flere hændelser er tilladt. Returnerer None (ja), eller sekunder at vente (nej)."""
        now = self._clock()
        events = self._live(key, now)
        used = sum(c for _, c in events) if events else 0
        if used + cost <= self.max_events:
            return None

        # Find hvornår nok gamle hændelser er udløbet til at der bliver plads.
        needed = used + cost - self.max_events
        freed = 0
        for timestamp, c in events or []:
            freed += c
            if freed >= needed:
                return max(1, math.ceil(timestamp + self.window - now))
        return math.ceil(self.window)

    def record(self, key: str, cost: int = 1) -> None:
        """Skriver at der skete `cost` hændelser nu."""
        now = self._clock()
        self._events.setdefault(key, deque()).append((now, cost))
        if len(self._events) > 10_000:  # rydder op, så hukommelsen ikke vokser uendeligt
            for other in list(self._events):
                self._live(other, now)

    def reset(self, key: str) -> None:
        """Glemmer alt om en nøgle (fx efter et vellykket login)."""
        self._events.pop(key, None)

    def reset_all(self) -> None:
        """Glemmer alt. Bruges kun af testene."""
        self._events.clear()


@dataclass
class Limits:
    """Alle grænser i appen samlet ét sted."""

    # Forkerte logins: højst 5 pr. e-mail og 20 pr. IP-adresse på 15 minutter.
    login_email: RateLimiter = field(default_factory=lambda: RateLimiter(5, 15 * 60))
    login_ip: RateLimiter = field(default_factory=lambda: RateLimiter(20, 15 * 60))
    # Udkast fra Claude: højst 100 pr. bruger i timen (hvert udkast koster penge).
    generate: RateLimiter = field(default_factory=lambda: RateLimiter(100, 60 * 60))

    def reset_all(self) -> None:
        """Nulstiller alle tællere. Bruges kun af testene."""
        for limiter in (self.login_email, self.login_ip, self.generate):
            limiter.reset_all()
