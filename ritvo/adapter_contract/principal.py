"""The principal and the types shared by messages and the ledger (docs/SPEC.md 4.2, 6.1)."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

Boundary = Literal["edge", "mcp", "data"]
Mode = Literal["attended", "unattended"]

# A hash or identifier. No `.`, so a token (which holds dots) cannot be written here.
TokenRef = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9:_-]{1,128}$")]
Sha256Ref = Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
EventId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
Source = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
Timestamp = Annotated[StrictStr, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")]
Count = Annotated[StrictInt, Field(ge=0)]


class Model(BaseModel):
    """Every message and entry refuses fields it does not define."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Subject(Model):
    id: StrictStr
    issuer: StrictStr


class Actor(Model):
    id: StrictStr
    platform: StrictStr | None


class Hop(Model):
    boundary: Boundary
    audience: StrictStr
    scopes: list[StrictStr]
    token_ref: TokenRef


class Principal(Model):
    mode: Mode
    subject: Subject | None
    actor: Actor
    owner: StrictStr | None
    chain: list[Hop]

    @model_validator(mode="after")
    def _subject_follows_mode(self) -> Self:
        if self.mode == "attended" and self.subject is None:
            raise ValueError("an attended principal has a subject")
        if self.mode == "unattended" and self.subject is not None:
            raise ValueError("an unattended principal has no subject")
        return self
