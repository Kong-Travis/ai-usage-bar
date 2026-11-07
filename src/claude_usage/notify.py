"""Desktop notifications when a usage limit gets close or runs out.

LimitAlerts decides when to alert (once per level and limit period, remembered in the JSON
cache, because SwiftBar and Argos start a fresh process every minute). A Notifier only shows
the text: osascript on macOS, notify-send on Linux, the tray icon's balloon on Windows.
"""
from __future__ import annotations

import json
import os
import subprocess
from urllib.parse import quote, urlencode
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from .domain import PROVIDER_NAMES, AccountRow, Clock, Window
from .i18n import EN, VI, Translator
from .infra import Cli, CliLocator, JsonCache

NOTIFY_TIMEOUT_SECONDS = 3
NOTIFY_OSASCRIPT = "/usr/bin/osascript"  # Apple's own, never a shim earlier on PATH
NOTIFY_OPEN = "/usr/bin/open"
NOTIFY_CACHE_NAME = "alerts"
# Resets reported within this many seconds of each other belong to the same limit period
# (a reset time computed from "resets in N seconds" moves a little on every probe).
NOTIFY_SAME_PERIOD_SECONDS = 3600
# An alert for a limit without a known reset is forgotten after this long, or as soon as the
# limit is seen below the level again.
NOTIFY_FORGET_SECONDS = 8 * 86400
NOTIFY_CRIT, NOTIFY_FULL = "crit", "full"

NOTIFY_TEXT = {
    "notify_crit": {VI: "{account}, {window}: đã dùng {pct}%",
                    EN: "{account}, {window}: {pct}% used"},
    "notify_full": {VI: "{account}, {window}: đã hết hạn mức",
                    EN: "{account}, {window}: limit reached"},
    "notify_reset": {VI: ", reset {left}", EN: ", resets {left}"},
}


class Notifier(Protocol):
    def notify(self, title: str, body: str) -> None:
        """Show a desktop notification. Never raises."""


class NullNotifier:
    """No notifications (an OS without a notifier, or `notify = off`)."""

    def notify(self, title: str, body: str) -> None:
        pass


def osascript_args(title: str, body: str) -> List[str]:
    """osascript arguments that show a notification. The text goes in as run-handler
    arguments after `--`, never into the script source, so nothing in it is interpreted."""
    return ["-e", "on run argv",
            "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e", "end run", "--", title, body]


def notify_send_args(title: str, body: str) -> List[str]:
    """notify-send arguments; `--` keeps a text starting with "-" from reading as an option."""
    return ["--", title, body]


class CommandNotifier:
    """Shows a notification by running a command-line tool, e.g. osascript or notify-send."""

    def __init__(self, cli: Cli, args: Callable[[str, str], List[str]],
                 timeout: float = NOTIFY_TIMEOUT_SECONDS):
        self._cli = cli
        self._args = args
        self._timeout = timeout

    def notify(self, title: str, body: str) -> None:
        try:
            self._cli.run(self._args(title, body), self._timeout)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass  # a notification is a nicety: never break the menu for it


def osascript_notifier() -> Notifier:
    """macOS notifications through Apple's osascript."""
    return CommandNotifier(Cli((NOTIFY_OSASCRIPT,), os.environ), osascript_args)


def swiftbar_notify_args(plugin: str) -> Callable[[str, str], List[str]]:
    """`open -g swiftbar://notify?...` arguments: SwiftBar shows the notification as itself,
    with the permission the user already gave it, and -g keeps the active app in front."""
    def args(title: str, body: str) -> List[str]:
        query = urlencode({"plugin": plugin, "title": title, "body": body}, quote_via=quote)
        return ["-g", "swiftbar://notify?" + query]
    return args


def macos_notifier(env: Mapping[str, str]) -> Notifier:
    """Through SwiftBar when it runs the plugin (SWIFTBAR_PLUGIN_PATH names the file, and
    SwiftBar addresses a plugin by its file name up to the first dot), else osascript."""
    plugin = os.path.basename(env.get("SWIFTBAR_PLUGIN_PATH") or "").split(".")[0]
    if not plugin:
        return osascript_notifier()
    return CommandNotifier(Cli((NOTIFY_OPEN,), env), swiftbar_notify_args(plugin))


def notify_send_notifier(locator: CliLocator) -> Notifier:
    """Linux notifications through libnotify's notify-send, when it is installed."""
    cli = locator.find("notify-send")
    return CommandNotifier(cli, notify_send_args) if cli else NullNotifier()


class LimitAlerts:
    """Alerts once when a limit reaches `crit` percent and once more when it is used up.

    An alert is remembered per account, limit and level until its period resets, so the next
    period alerts again. Rows with an error are skipped: their numbers are old.
    time_left: epoch -> "in 1h 10m" (Presenter.time_left). window_name: Window -> "5-hour"
    (Presenter.window_name).
    """

    def __init__(self, notifier: Notifier, cache: JsonCache, tr: Translator, crit: float,
                 time_left: Callable[[int], str], window_name: Callable[[Window], str],
                 clock: Clock):
        self._notifier = notifier
        self._cache = cache
        self._tr = tr.with_catalog(NOTIFY_TEXT)
        self._crit = crit
        self._time_left = time_left
        self._window_name = window_name
        self._clock = clock

    def check(self, rows: Sequence[AccountRow]) -> None:
        now = int(self._clock.now())
        loaded = self._cache.load(NOTIFY_CACHE_NAME)
        sent: Dict[str, Dict[str, int]] = loaded if isinstance(loaded, dict) else {}
        before = dict(sent)
        sent = {k: v for k, v in sent.items() if self._valid(v) and not self._expired(v, now)}
        messages = []
        for row in rows:
            if row.error or row.usage is None:
                continue
            for w in row.usage.windows:
                message = self._check_window(row, w, now, sent)
                if message:
                    messages.append(message)
        if sent != before and not self._cache.save(NOTIFY_CACHE_NAME, sent):
            return  # unsaved alerts would repeat every minute: stay quiet instead
        for title, body in messages:
            try:
                self._notifier.notify(title, body)
            except Exception:
                pass

    def _check_window(self, row: AccountRow, w: Window, now: int,
                      sent: Dict[str, Dict[str, int]]) -> Optional[Tuple[str, str]]:
        """Update `sent` for one limit; the (title, body) to show, or None."""
        if w.pct is None or (w.reset and w.reset <= now):
            return None  # no such limit, or numbers from a period that has already ended
        keys = {level: json.dumps([row.provider, row.label, w.kind, w.name, level])
                for level in (NOTIFY_CRIT, NOTIFY_FULL)}
        if not w.reset:  # no reset to tell periods apart: below the level again starts anew
            for level, threshold in ((NOTIFY_CRIT, self._crit), (NOTIFY_FULL, 100.0)):
                if w.pct < threshold:
                    sent.pop(keys[level], None)
        level = NOTIFY_FULL if w.pct >= 100 else NOTIFY_CRIT if w.pct >= self._crit else None
        if level is None or self._same_period(sent.get(keys[level]), w.reset):
            return None
        record = {"reset": w.reset, "at": now}
        sent[keys[level]] = record
        if level == NOTIFY_FULL:
            sent[keys[NOTIFY_CRIT]] = record  # one toast, not "92%" and "used up" at once
        return self._message(row, w, level)

    @staticmethod
    def _valid(entry: Any) -> bool:
        """A remembered alert as written by check(); anything else in the file is dropped."""
        return (isinstance(entry, dict) and isinstance(entry.get("reset"), int)
                and isinstance(entry.get("at"), int))

    @staticmethod
    def _same_period(entry: Optional[Dict[str, int]], reset: int) -> bool:
        return entry is not None and abs(entry["reset"] - reset) < NOTIFY_SAME_PERIOD_SECONDS

    @staticmethod
    def _expired(entry: Dict[str, int], now: int) -> bool:
        if entry["reset"]:
            return entry["reset"] + NOTIFY_SAME_PERIOD_SECONDS < now
        return entry["at"] + NOTIFY_FORGET_SECONDS < now

    def _message(self, row: AccountRow, w: Window, level: str) -> Tuple[str, str]:
        tr = self._tr
        account = "%s %s" % (row.short, row.label)
        window = self._window_name(w)
        if level == NOTIFY_FULL:
            body = tr("notify_full", account=account, window=window)
        else:
            body = tr("notify_crit", account=account, window=window, pct="%d" % round(w.pct))
        if w.reset:
            body += tr("notify_reset", left=self._time_left(w.reset))
        return PROVIDER_NAMES.get(row.provider, row.provider), body
