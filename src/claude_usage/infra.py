"""Small I/O adapters: HTTP client, JSON file cache, installed command-line tools."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from .domain import Clock


@dataclass
class HttpResponse:
    status: int
    headers: Dict[str, str] = field(default_factory=dict)  # lower-case names
    body: bytes = b""

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class HttpClient(Protocol):
    def request(self, url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None,
                body: Optional[bytes] = None, timeout: float = 15) -> HttpResponse: ...


class UrllibHttpClient:
    """HttpClient over urllib. An HTTP error status is a response, not an exception;
    network failures still raise (URLError, timeout)."""

    def request(self, url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None,
                body: Optional[bytes] = None, timeout: float = 15) -> HttpResponse:
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return HttpResponse(r.status, _lower_headers(r.headers), r.read())
        except urllib.error.HTTPError as e:
            return HttpResponse(e.code, _lower_headers(e.headers), e.read() or b"")


def _lower_headers(headers) -> Dict[str, str]:
    return {k.lower(): v for k, v in headers.items()} if headers else {}


class JsonCache:
    """Named JSON documents in one directory, with their age."""

    def __init__(self, directory: str, clock: Clock):
        self.directory = directory
        self._clock = clock

    def path(self, name: str) -> str:
        return os.path.join(self.directory, name + ".json")

    def load(self, name: str) -> Any:
        try:
            with open(self.path(name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def save(self, name: str, data: Any) -> bool:
        """Best effort: a cache that cannot be written (full disk, a concurrent writer, a file
        held open on Windows) must never break the menu. Returns False when nothing was saved."""
        tmp = None
        try:
            os.makedirs(self.directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path(name))
            return True
        except OSError:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return False

    def age(self, name: str) -> float:
        try:
            return self._clock.now() - os.path.getmtime(self.path(name))
        except OSError:
            return float("inf")

    def fresh(self, name: str, max_age: float) -> Any:
        """The document if it is younger than max_age seconds, else None."""
        return self.load(name) if self.age(name) < max_age else None


# ---------- command-line tools (codex, agy) ----------

# SwiftBar, Argos and a login-started tray run with a minimal PATH, so the usual install
# folders are searched first. They also come before the shell PATH, where wrapper shims of
# terminal apps may shadow the real tool.
POSIX_CLI_DIRS = (".local/bin", "bin", ".npm-global/bin", ".bun/bin", ".volta/bin", ".cargo/bin",
                  "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")
WINDOWS_CLI_DIRS = (r"%APPDATA%\npm", r"%LOCALAPPDATA%\Microsoft\WinGet\Links",
                    r"%LOCALAPPDATA%\Programs\bin", r"~\.local\bin", r"~\bin")
# No console window flashing up from the Windows tray on every refresh.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def cli_search_path(home: str, env: Mapping[str, str], windows: bool) -> str:
    """The PATH to find and run command-line tools with: install folders, then env PATH."""
    if windows:
        dirs = [os.path.expandvars(d).replace("~", home, 1) for d in WINDOWS_CLI_DIRS]
    else:
        dirs = [d if d.startswith("/") else os.path.join(home, d) for d in POSIX_CLI_DIRS]
    dirs += (env.get("PATH") or "").split(os.pathsep)
    seen: List[str] = []
    for d in dirs:
        if d and "%" not in d and d not in seen:
            seen.append(d)
    return os.pathsep.join(seen)


@dataclass(frozen=True)
class Cli:
    """An installed command-line tool. argv: the command that starts it, e.g. ("/x/codex",),
    or (python, "fake_codex.py") in tests. env: the environment it runs in."""
    argv: Tuple[str, ...]
    env: Mapping[str, str]

    def run(self, args: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
        """Run to completion; raises subprocess.TimeoutExpired or OSError."""
        return subprocess.run(list(self.argv) + list(args), env=dict(self.env), timeout=timeout,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", creationflags=NO_WINDOW)

    def start(self, args: Sequence[str]) -> subprocess.Popen:
        """Start with line-buffered text pipes on stdin and stdout, for a JSON-RPC session."""
        return subprocess.Popen(list(self.argv) + list(args), env=dict(self.env),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, creationflags=NO_WINDOW)


class CliLocator:
    """Finds installed tools on cli_search_path and runs them with that PATH."""

    def __init__(self, home: str, env: Mapping[str, str], windows: bool):
        self._path = cli_search_path(home, env, windows)
        self._env = dict(env, PATH=self._path)

    def find(self, name: str) -> Optional[Cli]:
        found = shutil.which(name, path=self._path)
        return Cli((found,), self._env) if found else None
