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


class ForgotPasswordRequest(CamelModel):
    """Det browseren sender for at få et link til at vælge ny adgangskode."""

    email: str = Field(min_length=3, max_length=254)


class ResetPasswordRequest(PasswordModel):
    """Det browseren sender for at vælge ny adgangskode med nøglen fra mailen."""

    token: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=256)


class ChangeEmailRequest(PasswordModel):
    """Det browseren sender for at skifte e-mail. Den nye adresse tjekkes og gøres ens i koden."""

    new_email: str = Field(min_length=3, max_length=254)
    current_password: str = Field(min_length=1, max_length=128)


class ConfirmEmailRequest(CamelModel):
    """Det browseren sender for at bekræfte en ny e-mail med nøglen fra mailen."""

    token: str = Field(min_length=1, max_length=200)


class MeResponse(CamelModel):
    """Hvem er logget ind, og hvilken café de hører til."""

    email: str
    restaurant_id: UUID
    business_name: str
