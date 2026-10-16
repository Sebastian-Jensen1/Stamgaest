"""Skemaer for caféens indstillinger for svar: hvad browseren må sende, og hvad den får tilbage.

Lofterne på længde og antal er de samme som i services/settings_service.py, som også renser alt,
før det gemmes eller sendes til Claude. Skemaet afviser det, der er for stort, med en tydelig fejl.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from app.schemas.common import CamelModel
from app.services import settings_service as limits

Item = Annotated[str, StringConstraints(strip_whitespace=True, max_length=limits.ITEM_CHARS)]
Trait = Annotated[str, StringConstraints(strip_whitespace=True, max_length=limits.TRAIT_CHARS)]
ReplyLength = Literal["auto", "meget_kort", "kort", "mellem", "lang"]


class About(CamelModel):
    """Fakta om stedet, delt op i felter."""

    concept: str = Field(default="", max_length=limits.CONCEPT_CHARS)
    items: list[Item] = Field(default_factory=list, max_length=limits.MAX_ITEMS)
    traits: list[Trait] = Field(default_factory=list, max_length=limits.MAX_TRAITS)
    atmosphere: str = Field(default="", max_length=limits.ATMOSPHERE_CHARS)


class ReplySettingsIn(CamelModel):
    """Det ejeren gemmer. Der er bevidst intet café-id: serveren ved det ud fra login."""

    reply_length: ReplyLength = "auto"
    tone: str = Field(default="", max_length=200)
    signature: str = Field(default="", max_length=150)
    avoid: str = Field(default="", max_length=600)
    contact: str = Field(default="", max_length=200)
    examples: str = Field(default="", max_length=3000)
    about: About = Field(default_factory=About)


class SaveReplySettingsRequest(ReplySettingsIn):
    """Som ReplySettingsIn, plus om ejeren har taget stilling til Claudes forslag."""

    # True når ejeren har valgt fra forslaget (eller kasseret det), så det kan fjernes.
    clear_suggestion: bool = False


class ReplySettingsOut(CamelModel):
    """Caféens indstillinger, Claudes eventuelle forslag og status for at bygge profilen."""

    reply_length: ReplyLength
    tone: str
    signature: str
    avoid: str
    contact: str
    examples: str
    about: About
    about_suggestion: About | None
    facts_approved_at: datetime | None
    profile_status: Literal["none", "building", "ready", "failed"]
    profile_message: str
    website_url: str | None
