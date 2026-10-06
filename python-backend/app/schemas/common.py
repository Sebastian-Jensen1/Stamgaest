"""Fælles grundklasse for alle skemaer."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Grundklasse: Python skriver `reviewer_name`, men JSON til og fra browseren hedder `reviewerName`.

    Frontenden er uændret og forventer camelCase, så navnene oversættes automatisk.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,  # reviewer_name <-> reviewerName
        populate_by_name=True,  # begge navne virker ind
        str_strip_whitespace=True,  # mellemrum i enderne fjernes, så "   " ikke tæller som tekst
    )
