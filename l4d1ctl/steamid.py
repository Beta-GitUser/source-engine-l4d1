"""SteamID conversions.

Only *identifiers* are handled here. Nothing in this module authenticates:
a SteamID64 read from a local Steam install is an offline label, not proof of
ownership. See docs/L4D1/LAUNCHER.md for the policy on Steam identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

# SteamID64 = 76561197960265728 + account id (universe 1, individual account).
STEAMID64_BASE = 76561197960265728
STEAMID64_MIN = STEAMID64_BASE
STEAMID64_MAX = STEAMID64_BASE + 2**32 - 1

_STEAMID64_RE = re.compile(r"^\d{17}$")
_STEAM2_RE = re.compile(r"^STEAM_(\d+):([01]):(\d+)$", re.IGNORECASE)
_STEAM3_RE = re.compile(r"^\[([A-Z]):([01]):(\d+)\]$", re.IGNORECASE)
_PROFILE_RE = re.compile(r"^https?://steamcommunity\.com/profiles/(\d{17})/?$", re.IGNORECASE)


class SteamIDError(ValueError):
    pass


def from_account_id(account_id: int) -> int:
    if not 0 <= account_id < 2**32:
        raise SteamIDError(f"account id out of range: {account_id}")
    return STEAMID64_BASE + account_id


def account_id(steamid64: int) -> int:
    _check(steamid64)
    return steamid64 - STEAMID64_BASE


def _check(steamid64: int) -> None:
    if not STEAMID64_MIN <= steamid64 <= STEAMID64_MAX:
        raise SteamIDError(f"not a valid individual SteamID64: {steamid64}")


def is_valid(steamid64: int) -> bool:
    return STEAMID64_MIN <= int(steamid64) <= STEAMID64_MAX


def to_steam2(steamid64: int) -> str:
    acc = account_id(steamid64)
    return f"STEAM_0:{acc & 1}:{acc >> 1}"


def to_steam3(steamid64: int) -> str:
    return f"[U:1:{account_id(steamid64)}]"


def parse(value: Union[str, int]) -> int:
    """Accept SteamID64, STEAM_X:Y:Z, [U:1:n] or a /profiles/ URL."""
    if isinstance(value, int):
        _check(value)
        return value

    text = str(value).strip().strip('"')
    if not text:
        raise SteamIDError("empty SteamID")

    if _STEAMID64_RE.match(text):
        steamid64 = int(text)
        _check(steamid64)
        return steamid64

    match = _STEAM2_RE.match(text)
    if match:
        y = int(match.group(2))
        z = int(match.group(3))
        return from_account_id(z * 2 + y)

    match = _STEAM3_RE.match(text)
    if match:
        return from_account_id(int(match.group(3)))

    match = _PROFILE_RE.match(text)
    if match:
        steamid64 = int(match.group(1))
        _check(steamid64)
        return steamid64

    raise SteamIDError(f"unrecognised SteamID: {value!r}")


@dataclass(frozen=True)
class SteamID:
    """Convenience wrapper with the usual renderings."""

    steamid64: int

    def __post_init__(self) -> None:
        _check(self.steamid64)

    @property
    def account_id(self) -> int:
        return account_id(self.steamid64)

    @property
    def steam2(self) -> str:
        return to_steam2(self.steamid64)

    @property
    def steam3(self) -> str:
        return to_steam3(self.steamid64)

    @property
    def profile_url(self) -> str:
        return f"https://steamcommunity.com/profiles/{self.steamid64}"

    def __str__(self) -> str:
        return str(self.steamid64)


def try_parse(value: Optional[Union[str, int]]) -> Optional[int]:
    """Parse, returning ``None`` instead of raising."""
    if value is None or value == "":
        return None
    try:
        return parse(value)
    except SteamIDError:
        return None


__all__ = [
    "STEAMID64_BASE",
    "SteamID",
    "SteamIDError",
    "account_id",
    "from_account_id",
    "is_valid",
    "parse",
    "to_steam2",
    "to_steam3",
    "try_parse",
]
