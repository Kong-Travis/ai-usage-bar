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


# Providers: the coding agents whose limits are shown.
CLAUDE, CODEX, AGY = "claude", "codex", "agy"
PROVIDERS = (CLAUDE, CODEX, AGY)  # menu order

# Window kinds every provider shares. Anything else is shown under its own name.
FIVE_HOUR, WEEKLY = "5h", "7d"
FIVE_HOUR_SECONDS, WEEK_SECONDS = 5 * 3600, 7 * 86400


@dataclass(frozen=True)
class Window:
    """One limit: percent used of a rolling window.

    kind: FIVE_HOUR, WEEKLY, or the provider's own word for another cadence.
    pct: None when the plan has no such limit (a verified Claude Ultra has no 5-hour limit).
    reset: epoch seconds, 0 when unknown. seconds: window length, 0 when unknown.
    name: the model or model group the limit covers, e.g. "Fable"; "" for the whole plan.
    """
    kind: str
    pct: Optional[float]
    reset: int = 0
    seconds: int = 0
    name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "pct": self.pct, "reset": self.reset,
                "seconds": self.seconds, "name": self.name}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Window:
        pct = d.get("pct")
        return cls(str(d["kind"]), None if pct is None else float(pct), int(d.get("reset") or 0),
                   int(d.get("seconds") or 0), str(d.get("name") or ""))


@dataclass(frozen=True)
class Usage:
    """Plan usage of one account: its windows in display order, plus what the provider says
    about the account itself (plan name, email), "" when it says nothing."""
    windows: Tuple[Window, ...]
    plan: str = ""
    email: str = ""

    def percentages(self) -> List[Optional[float]]:
        """Each window's %, None for a limit the plan does not have."""
        return [w.pct for w in self.windows]

    def to_dict(self) -> Dict[str, Any]:
        return {"windows": [w.to_dict() for w in self.windows], "plan": self.plan,
                "email": self.email}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Usage:
        return cls(tuple(Window.from_dict(w) for w in d["windows"]), str(d.get("plan") or ""),
                   str(d.get("email") or ""))


class LoginExpired(Exception):
    """A browser login whose access token has expired; only Claude Code can renew it."""


class Account(ABC):
    """One identity of one provider whose limits can be read."""

    provider = CLAUDE
    #: True for a Claude browser login (it has a profile email), False otherwise.
    is_login = False

    def __init__(self, label: str):
        self.label = label

    @property
    @abstractmethod
    def identity(self) -> str:
        """Stable key within the provider: equal identities are the same account."""

    @property
    def cache_key(self) -> str:
        # Claude keys stay unprefixed, so its caches survive the upgrade.
        key = self.identity if self.provider == CLAUDE else self.provider + ":" + self.identity
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class ClaudeAccount(Account):
    """A Claude Code identity, read with its OAuth access token."""

    @abstractmethod
    def token(self) -> str:
        """The OAuth access token. May raise (locked keychain, expired login...)."""


class TokenAccount(ClaudeAccount):
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


class UsageReader(Protocol):
    """Reads the current usage of the accounts of one provider. Raises when it cannot."""

    def read(self, account: Account) -> Usage: ...


@dataclass(frozen=True)
class AccountRow:
    """Everything the menu shows for one account."""
    short: str
    label: str
    detail: str
    usage: Optional[Usage]
    error: Optional[str]
    provider: str = CLAUDE
