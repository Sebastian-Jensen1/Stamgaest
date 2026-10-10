"""Måler hvor meget ejeren retter i Claudes udkast, før svaret sendes.

Det er vores tal for "er udkastene gode nok?". Ændrer vi prompten eller modellen,
kan vi se om tallet går ned. Uden det ville vi gætte.

Målingen sker på ord, ikke på bogstaver: ét udskiftet ord tæller som én ændring,
uanset hvor langt ordet er. Det ligner bedre hvordan et menneske oplever "meget rettet".
"""

import re
import statistics
from dataclasses import dataclass
from difflib import SequenceMatcher

# Grænserne for de fire grupper. Rettes der under 20 %, er det småting som et navn
# eller en hilsen. Fra 60 % er det meste skrevet om.
SMALL_EDIT = 0.20
REWRITE = 0.60

# Ord og tegn for sig, så "tak!" og "tak." kun adskiller sig på tegnet.
_TOKENS = re.compile(r"\w+|[^\w\s]")


def _tokens(text: str) -> list[str]:
    """Deler en tekst op i ord og tegn. Mellemrum og linjeskift tæller ikke."""
    return _TOKENS.findall(text)


def edit_ratio(draft: str, final: str) -> float:
    """Hvor stor en del af udkastet der er ændret: 0.0 = sendt uændret, 1.0 = skrevet helt om."""
    a, b = _tokens(draft), _tokens(final)
    if not a and not b:
        return 0.0
    # ratio() er 1.0 når teksterne er ens og 0.0 når de intet har til fælles.
    return 1.0 - SequenceMatcher(None, a, b, autojunk=False).ratio()


@dataclass(frozen=True)
class EditSummary:
    """Samlet billede af en række rettelser."""

    count: int
    unchanged: int
    small: int  # under SMALL_EDIT
    large: int  # fra SMALL_EDIT op til REWRITE
    rewritten: int  # REWRITE eller mere
    mean: float
    median: float


def summarize(ratios: list[float]) -> EditSummary | None:
    """Laver et samlet billede ud fra en liste af edit_ratio-værdier. None hvis listen er tom."""
    if not ratios:
        return None
    return EditSummary(
        count=len(ratios),
        unchanged=sum(1 for r in ratios if r == 0),
        small=sum(1 for r in ratios if 0 < r < SMALL_EDIT),
        large=sum(1 for r in ratios if SMALL_EDIT <= r < REWRITE),
        rewritten=sum(1 for r in ratios if r >= REWRITE),
        mean=statistics.fmean(ratios),
        median=statistics.median(ratios),
    )
