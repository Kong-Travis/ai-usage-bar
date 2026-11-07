"""OpenAI Codex: find its logins and read plan limits through `codex app-server`."""
from __future__ import annotations

import glob
import json
import os
import subprocess
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from .domain import (CODEX, FIVE_HOUR, FIVE_HOUR_SECONDS, WEEK_SECONDS, WEEKLY, Account, Usage,
                     Window)
from .infra import Cli, LineReader, kill_tree

CODEX_MAIN_LIMIT = "codex"
CODEX_REQUESTS = (
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"clientInfo": {"name": "ai-usage-bar", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "account/read", "params": {}},
    {"jsonrpc": "2.0", "id": 3, "method": "account/rateLimits/read"},
)
CODEX_ANSWERS_NEEDED = (2, 3)


class CodexAccount(Account):
    """A Codex home directory holding a ChatGPT login."""

    provider = CODEX

    def __init__(self, home: str):
        name = os.path.basename(os.path.normpath(home))
        super().__init__(name.lstrip(".") or "codex")
        self.home = home

    @property
    def identity(self) -> str:
        return os.path.normcase(os.path.realpath(self.home))


def codex_has_plan_login(home: str) -> bool:
    """True when auth.json holds a ChatGPT login. An API-key login has no plan limits."""
    try:
        with open(os.path.join(home, "auth.json"), encoding="utf-8") as f:
            auth = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(auth, dict):
        return False
    tokens = auth.get("tokens")
    return auth.get("auth_mode") == "chatgpt" or (isinstance(tokens, dict) and bool(tokens))


class CodexSource:
    """Finds Codex homes: $CODEX_HOME, ~/.codex, then ~/.codex-* and ~/.codex_*."""

    def __init__(self, home: str, env: Mapping[str, str], cli: Optional[Cli]):
        self._home = home
        self._env = env
        self._cli = cli

    def _candidates(self) -> List[str]:
        found = []  # type: List[str]
        if self._env.get("CODEX_HOME"):
            found.append(self._env["CODEX_HOME"])
        found.append(os.path.join(self._home, ".codex"))
        extra = glob.glob(os.path.join(self._home, ".codex-*"))
        extra += glob.glob(os.path.join(self._home, ".codex_*"))
        found += sorted(d for d in extra if os.path.isdir(d))
        return found

    def accounts(self) -> List[Account]:
        if self._cli is None:
            return []
        accounts = {}  # type: Dict[str, Account]
        for home in self._candidates():
            if codex_has_plan_login(home):
                account = CodexAccount(home)
                accounts.setdefault(account.identity, account)
        return list(accounts.values())


def codex_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def codex_kind(seconds: int) -> str:
    if seconds == FIVE_HOUR_SECONDS:
        return FIVE_HOUR
    if seconds == WEEK_SECONDS:
        return WEEKLY
    if seconds <= 0:
        return "limit"
    for size, unit in ((86400, "d"), (3600, "h")):
        if seconds % size == 0:
            return "%d%s" % (seconds // size, unit)
    return "%dm" % (seconds // 60)


def codex_window(raw: Any, name: str) -> Optional[Window]:
    """One primary or secondary entry of a limit; None when it reports no usedPercent."""
    if not isinstance(raw, dict):
        return None
    used = codex_number(raw.get("usedPercent"))
    if used is None:
        return None
    minutes = codex_number(raw.get("windowDurationMins"))
    seconds = int(minutes * 60) if minutes else 0
    reset = codex_number(raw.get("resetsAt"))
    return Window(codex_kind(seconds), max(0.0, min(100.0, used)), int(reset or 0), seconds, name)


def codex_limit_windows(limit_id: str, snapshot: Any) -> List[Window]:
    """The windows of one limit, shortest first. The main limit covers the whole plan."""
    if not isinstance(snapshot, dict):
        return []
    name = ""
    if limit_id != CODEX_MAIN_LIMIT:
        name = str(snapshot.get("limitName") or limit_id)
    windows = [codex_window(snapshot.get(key), name) for key in ("primary", "secondary")]
    found = [w for w in windows if w is not None]
    return sorted(found, key=lambda w: w.seconds or 1 << 40)


def codex_build_usage(account: Any, limits: Any) -> Usage:
    """Usage from the account/read and account/rateLimits/read results."""
    account = account if isinstance(account, dict) else {}
    limits = limits if isinstance(limits, dict) else {}
    info = account.get("account") if isinstance(account.get("account"), dict) else {}
    snapshots = limits.get("rateLimitsByLimitId")
    if not isinstance(snapshots, dict) or not snapshots:
        snapshots = {CODEX_MAIN_LIMIT: limits.get("rateLimits")}
    ordered = sorted(snapshots, key=lambda k: k != CODEX_MAIN_LIMIT)  # stable: main limit first
    windows = []  # type: List[Window]
    for limit_id in ordered:
        windows += codex_limit_windows(str(limit_id), snapshots[limit_id])
    if not windows:
        raise RuntimeError("no Codex limits")
    main = limits.get("rateLimits") if isinstance(limits.get("rateLimits"), dict) else {}
    plan = str(info.get("planType") or main.get("planType") or "")
    return Usage(tuple(windows), plan.title(), str(info.get("email") or ""))


class CodexReader:
    """Asks `codex app-server` (newline-delimited JSON-RPC on stdio) for the plan limits."""

    def __init__(self, cli: Cli, timeout: float = 20):
        self._cli = cli
        self._timeout = timeout

    def read(self, account: Account) -> Usage:
        home = getattr(account, "home", "")
        cli = Cli(self._cli.argv, dict(self._cli.env, CODEX_HOME=home))
        proc = cli.start(["app-server"])
        lines = LineReader(proc.stdout)
        deadline = time.monotonic() + self._timeout
        try:
            results = self._converse(proc, lambda: lines.readline(deadline - time.monotonic()))
        except TimeoutError:
            raise RuntimeError("codex app-server timed out") from None
        finally:
            self._stop(proc)
        return codex_build_usage(results[2], results[3])

    @staticmethod
    def _converse(proc: subprocess.Popen, readline: Callable[[], str]) -> Dict[int, Any]:
        # The server quits on stdin EOF before answering, so stdin stays open until done.
        try:
            for request in CODEX_REQUESTS:
                proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
        except OSError:
            pass  # it died already: the read below reports the early end
        results = {}  # type: Dict[int, Any]
        while not all(i in results for i in CODEX_ANSWERS_NEEDED):
            line = readline()
            if not line:
                raise RuntimeError("codex app-server ended early")
            message = CodexReader._parse(line)
            if message is None:
                continue
            ident = message["id"]
            if ident in CODEX_ANSWERS_NEEDED and "error" in message:
                err = message["error"]
                raise RuntimeError(str(err.get("message") if isinstance(err, dict) else err))
            if "result" in message:
                results[ident] = message["result"]
        return results

    @staticmethod
    def _parse(line: str) -> Optional[Dict[str, Any]]:
        """A JSON object response (it has an id), or None for blanks, noise and notifications."""
        try:
            message = json.loads(line)
        except ValueError:
            return None
        if isinstance(message, dict) and isinstance(message.get("id"), int):
            return message
        return None

    @staticmethod
    def _stop(proc: subprocess.Popen) -> None:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
