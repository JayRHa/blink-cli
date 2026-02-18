"""Constants and mappings for the Blink CLI."""

from __future__ import annotations

from typing import Final

API_TIMEOUT_SECONDS: Final = 35
DEFAULT_DOWNLOAD_DELAY_SECONDS: Final = 5
DEFAULT_DOWNLOAD_STOP_SECONDS: Final = 30

BOOL_RENDER: Final[dict[bool, str]] = {
    True: "yes",
    False: "no",
}

AUTH_ERROR_HINTS: Final[tuple[str, ...]] = (
    "auth",
    "login",
    "credential",
    "password",
    "unauthor",
    "forbidden",
    "2fa",
    "two-factor",
    "verification",
    "token",
)

TWO_FACTOR_HINTS: Final[tuple[str, ...]] = (
    "2fa",
    "two-factor",
    "verify",
    "verification",
    "pin",
    "challenge",
)

RATE_LIMIT_HINTS: Final[tuple[str, ...]] = (
    "rate limit",
    "too many",
    "throttle",
    "429",
    "requests exceeded",
)
