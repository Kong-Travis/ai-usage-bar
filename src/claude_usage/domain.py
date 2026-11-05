"""Core types shared by every layer. No I/O here."""
from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


@dataclass(frozen=True)
class ModelWindow:
    """A per-model weekly limit, e.g. Fable."""
    name: str
    pct: float
    reset: int  # epoch seconds, 0 when unknown


@dataclass(frozen=True)
class Usage:
    """Plan usage of one account, in percent of each limit. five_hour is None when the plan has
    no 5-hour limit (a verified Ultra account)."""
    five_hour: Optional[float]
    five_hour_reset: int
    weekly: float
    weekly_reset: int
    models: Tuple[ModelWindow, ...] = ()

    def percentages(self) -> List[Optional[float]]:
        """5-hour % (None without that limit), weekly %, then each per-model weekly %."""
        return [self.five_hour, self.weekly] + [m.pct for m in self.models]

    def to_dict(self) -> Dict[str, Any]:
        # Same shape as the cache files written by v1.2.x, so an upgrade keeps them.
        return {
            "5h": self.five_hour, "5h_reset": self.five_hour_reset,
            "7d": self.weekly, "7d_reset": self.weekly_reset,
            "models": [{"name": m.name, "pct": m.pct, "reset": m.reset} for m in self.models],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Usage:
        return cls(
            five_hour=None if d.get("5h") is None else float(d["5h"]),
            five_hour_reset=int(d.get("5h_reset") or 0),
            weekly=float(d["7d"]), weekly_reset=int(d["7d_reset"]),
            models=tuple(ModelWindow(str(m["name"]), float(m["pct"]), int(m.get("reset") or 0))
                         for m in d.get("models", [])),
        )


class LoginExpired(Exception):
    """A browser login whose access token has expired; only Claude Code can renew it."""


class Account(ABC):
    """One Claude Code identity whose limits can be read."""

    #: True for a browser login (it has a profile email), False for a setup-token.
    is_login = False

    def __init__(self, label: str):
        self.label = label

    @property
    @abstractmethod
    def identity(self) -> str:
        """Stable key: two accounts with the same identity are the same account."""

    @abstractmethod
    def token(self) -> str:
        """The OAuth access token. May raise (locked keychain, expired login...)."""

    @property
    def cache_key(self) -> str:
        return hashlib.sha256(self.identity.encode()).hexdigest()[:16]


class TokenAccount(Account):
    """A `claude setup-token` token found in a shell file, the environment or the conf file."""

    def __init__(self, label: str, token: str):
        super().__init__(label)
        self._token = token

    @property
    def identity(self) -> str:
        return self._token

    def token(self) -> str:
        return self._token


class AccountSource(Protocol):
    """Finds accounts of one kind (keychain, credentials file, shell files...)."""

    def accounts(self) -> List[Account]: ...


@dataclass(frozen=True)
class AccountRow:
    """Everything the menu shows for one account."""
    short: str
    label: str
    detail: str
    usage: Optional[Usage]
    error: Optional[str]
