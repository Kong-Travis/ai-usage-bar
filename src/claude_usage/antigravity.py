"""Google Antigravity CLI (`agy`): limits from its read-only `/usage` print-mode command.

`agy -p /usage --output-format json` runs no agent turn and spends no quota. It reports groups
of models, each with buckets (a window, the fraction still available and when it resets).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .domain import AGY, FIVE_HOUR, FIVE_HOUR_SECONDS, WEEK_SECONDS, WEEKLY, Account, Usage, Window
from .infra import Cli

AGY_DAY = "1d"
AGY_DAY_SECONDS = 86400
AGY_ERROR_MAX = 160
AGY_STATE_DIR = os.path.join(".gemini", "antigravity-cli")
_AGY_TIME = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?"
                       r"(?:(Z)|([+-])(\d\d):?(\d\d))?$", re.IGNORECASE)


class AgyAccount(Account):
    """The one account agy is signed in to on this machine."""

    provider = AGY

    def __init__(self):
        super().__init__("agy")

    @property
    def identity(self) -> str:
        return "agy"


class AgySource:
    """agy is installed and has been run on this machine, so it has a state folder."""

    def __init__(self, home: str, cli: Optional[Cli]):
        self._state_dir = os.path.join(home, AGY_STATE_DIR)
        self._cli = cli

    def accounts(self) -> List[Account]:
        if self._cli is not None and os.path.isdir(self._state_dir):
            return [AgyAccount()]
        return []


def _agy_epoch(text: Any) -> int:
    """ISO-8601 to epoch seconds; 0 when absent or unparsable. No zone means UTC."""
    m = _AGY_TIME.match(text.strip()) if isinstance(text, str) else None
    if not m:
        return 0
    year, month, day, hour, minute, second = (int(g) for g in m.groups()[:6])
    offset = timedelta(0)
    if m.group(8):
        offset = timedelta(hours=int(m.group(9)), minutes=int(m.group(10)))
        if m.group(8) == "-":
            offset = -offset
    try:
        moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone(offset))
    except ValueError:
        return 0
    return int(moment.timestamp())


def _agy_window_kind(window: Any) -> Tuple[str, int]:
    """(kind, seconds) of a bucket's window word, e.g. "weekly", "five_hour", "5h", "daily"."""
    word = str(window or "")
    norm = re.sub(r"[^a-z0-9]", "", word.lower())
    if "week" in norm:
        return WEEKLY, WEEK_SECONDS
    if "five" in norm or "5h" in norm or ("5" in norm and "hour" in norm):
        return FIVE_HOUR, FIVE_HOUR_SECONDS
    if "daily" in norm or norm == "day":
        return AGY_DAY, AGY_DAY_SECONDS
    return word, 0


def _agy_group_name(name: Any) -> str:
    """"Gemini Models" -> "Gemini", "Claude and GPT models" -> "Claude + GPT"."""
    short = re.sub(r"\s+models?\s*$", "", str(name or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+and\s+", " + ", short, flags=re.IGNORECASE)


def _agy_fraction(bucket: Dict[str, Any]) -> Optional[float]:
    value = bucket.get("remaining_fraction")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return float(value)


def _agy_windows(group: Dict[str, Any]) -> List[Window]:
    name = _agy_group_name(group.get("name"))
    found = []
    for bucket in group.get("buckets") or []:
        if not isinstance(bucket, dict):
            continue
        fraction = _agy_fraction(bucket)
        if fraction is None:
            continue  # no data is never shown as 0 percent
        kind, seconds = _agy_window_kind(bucket.get("window"))
        pct = round(min(100.0, max(0.0, (1 - fraction) * 100)), 2)
        found.append(Window(kind, pct, _agy_epoch(bucket.get("reset_time")), seconds, name))
    return sorted(found, key=lambda w: w.seconds or float("inf"))


def _agy_json(stdout: str) -> Optional[Dict[str, Any]]:
    """The last stdout line that is a JSON object, else the whole output."""
    for text in reversed([ln.strip() for ln in stdout.splitlines()] + [stdout.strip()]):
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


class AgyReader:
    """Reads agy's limits through `agy -p /usage`, logging to our own file so that polling
    does not rotate the user's own agy logs away."""

    def __init__(self, cli: Cli, log_path: str, timeout: float = 30):
        self._cli = cli
        self._log_path = log_path
        self._timeout = timeout

    def read(self, account: Account) -> Usage:
        os.makedirs(os.path.dirname(os.path.abspath(self._log_path)), exist_ok=True)
        # agy appends to --log-file; emptying it first keeps only the last run, for diagnosis.
        open(self._log_path, "w").close()
        done = self._cli.run(["-p", "/usage", "--output-format", "json",
                              "--log-file", self._log_path], self._timeout)
        reply = _agy_json(done.stdout or "")
        if reply is None:
            raise RuntimeError("agy exited with code %s" % done.returncode)
        if reply.get("status") != "SUCCESS":
            text = str(reply.get("error") or "").strip()
            raise RuntimeError(text[:AGY_ERROR_MAX] or "agy exited with code %s" % done.returncode)
        command = reply.get("command")
        data = command.get("data") if isinstance(command, dict) else None
        groups = data.get("groups") if isinstance(data, dict) else None
        windows = [w for g in groups or [] if isinstance(g, dict) for w in _agy_windows(g)]
        if not windows:
            raise RuntimeError("no Antigravity limits")
        return Usage(tuple(windows))
