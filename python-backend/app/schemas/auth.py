"""Skemaer for login: hvad browseren sender, og hvad den får tilbage."""

from uuid import UUID

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel


class PasswordModel(CamelModel):
    """Som CamelModel, men uden at fjerne mellemrum i enderne.

    En adgangskode må ikke ændres i det skjulte. Ville vi fjerne mellemrum, ville "abc " og
    "abc" være den samme adgangskode.
    """

    model_config = ConfigDict(str_strip_whitespace=False)


class LoginRequest(PasswordModel):
    """Det browseren sender til login."""

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(PasswordModel):
    """Det browseren sender for at skifte adgangskode. Kravene til den nye tjekkes i koden."""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=256)


class MeResponse(CamelModel):
    """Hvem er logget ind, og hvilken café de hører til."""

    email: str
    restaurant_id: UUID
    business_name: str
