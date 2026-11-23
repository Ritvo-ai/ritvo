"""Validate an inbound token (docs/SPEC.md section 5.10)."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt

from ritvo.adapter_contract.canonical import sha256_hex
from ritvo.config.schema import ClaimsMap
from ritvo.enforcement.identity.jwks import JwksError, JwksSource

ALGORITHMS = ("RS256", "ES256", "EdDSA")


class TokenError(Exception):
    """A token is refused. `code` is one of the codes of section 5.10; the token is never shown."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Issuer:
    name: str
    iss: str
    audience: str
    claims_map: ClaimsMap
    jwks: JwksSource


@dataclass(frozen=True)
class ValidatedToken:
    issuer: Issuer
    claims: dict[str, Any]
    token_ref: str


class TokenValidator:
    def __init__(
        self,
        issuers: list[Issuer],
        leeway_seconds: int = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._by_iss = {issuer.iss: issuer for issuer in issuers}
        self._leeway = leeway_seconds
        self._clock = clock

    def validate(self, token: str) -> ValidatedToken:
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            raise TokenError("malformed") from None
        alg, kid, iss = header.get("alg"), header.get("kid"), unverified.get("iss")
        if not isinstance(alg, str) or not isinstance(kid, str) or not isinstance(iss, str):
            raise TokenError("malformed")
        issuer = self._by_iss.get(iss)
        if issuer is None:
            raise TokenError("unknown_issuer")
        if alg not in ALGORITHMS:
            raise TokenError("unsupported_alg")
        try:
            key = issuer.jwks.key_for(kid)
        except JwksError:
            raise TokenError("bad_signature") from None
        if key is None:
            raise TokenError("bad_signature")
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[alg],
                audience=issuer.audience,
                options={
                    "require": ["exp", "iss", "aud"],
                    # Time is checked below, against this validator's clock.
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except jwt.InvalidAudienceError:
            raise TokenError("wrong_audience") from None
        except jwt.MissingRequiredClaimError:
            raise TokenError("malformed") from None
        except (jwt.PyJWTError, TypeError, ValueError):
            # A key of another type than the algorithm asks for raises TypeError, not a JWT error.
            raise TokenError("bad_signature") from None
        self._check_time(claims)
        return ValidatedToken(issuer, claims, "sha256:" + sha256_hex(token.encode("utf-8")))

    def _check_time(self, claims: dict[str, Any]) -> None:
        now = self._clock()
        exp, nbf = claims["exp"], claims.get("nbf")
        if isinstance(exp, bool) or not isinstance(exp, int | float):
            raise TokenError("malformed")
        if nbf is not None and (isinstance(nbf, bool) or not isinstance(nbf, int | float)):
            raise TokenError("malformed")
        if exp + self._leeway <= now or (nbf is not None and nbf - self._leeway > now):
            raise TokenError("expired")
