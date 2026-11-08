"""Account sources. Each one finds a single kind of account; AccountRegistry merges them."""
from __future__ import annotations

import glob
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, replace
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from .config import CRIT_PCT, KEYCHAIN_SERVICE, WARN_PCT
from .domain import AGY, CODEX, Account, AccountSource, ClaudeAccount, Clock, LoginExpired, TokenAccount
from .i18n import Translator, normalize_language
from .scan import ShellScanner, expand_path, read_token_file, token_in_text

POSIX_RC_FILES = (".zshrc", ".zprofile", ".zshenv", ".bashrc", ".bash_profile", ".profile")
WINDOWS_RC_FILES = POSIX_RC_FILES + tuple(
    os.path.join(docs, shell, profile)
    for docs in ("Documents", os.path.join("OneDrive", "Documents"))
    for shell in ("PowerShell", "WindowsPowerShell")
    for profile in ("Microsoft.PowerShell_profile.ps1", "profile.ps1")
)
SCRIPT_DIRS = ("bin", os.path.join(".local", "bin"))
WINDOWS_SCRIPT_EXTS = ("", ".ps1", ".cmd", ".bat", ".sh")
CREDENTIALS_FILE = ".credentials.json"


# ---------- user config ----------

@dataclass(frozen=True)
class UserConfig:
    """~/.config/claude-usage.conf, one entry per line:
        label = /path/to/token-file     add a Claude token the scan cannot find
        keychain = off                  hide Claude browser logins (keychain, credentials files)
        codex = off                     hide Codex accounts (agy = off: hide Antigravity)
        language = en                   English menus (default: vi, Vietnamese)
        hide = c-m, c-nsc               accounts (by label) left out of the bar title
        compact = on                    show only an icon in the bar
        warn = 70                       percent at which a limit turns yellow (green below)
        crit = 90                       percent at which it turns orange and notifies
        notify = off                    no notifications
    """
    token_files: Tuple[Tuple[str, str], ...] = ()
    show_logins: bool = True
    language: Optional[str] = None
    hidden: Tuple[str, ...] = ()
    compact: bool = False
    warn: int = WARN_PCT
    crit: int = CRIT_PCT
    notify: bool = True
    providers_off: Tuple[str, ...] = ()

    @classmethod
    def load(cls, path: str, home: str) -> UserConfig:
        try:
            with open(path, encoding="utf-8-sig", errors="ignore") as f:
                lines = f.read().splitlines()
        except OSError:
            return cls()
        token_files: List[Tuple[str, str]] = []
        off: List[str] = []
        fields: dict = {}
        for line in lines:
            line = line.split("#", 1)[0].strip()
            if "=" not in line:
                continue
            key, val = (s.strip() for s in line.split("=", 1))
            if key in ("keychain", "logins"):
                fields["show_logins"] = not is_off(val)
            elif key == "language":
                fields["language"] = normalize_language(val)
            elif key == HIDE_KEY:
                fields["hidden"] = split_labels(val)
            elif key == COMPACT_KEY:
                fields["compact"] = is_on(val)
            elif key == "notify":
                fields["notify"] = not is_off(val)
            elif key in ("warn", "crit") and val.isdigit() and 0 < int(val) <= 100:
                fields[key] = int(val)
            elif key in (CODEX, AGY) and (is_on(val) or is_off(val)):
                if is_off(val):
                    off.append(key)
            elif val:
                token_files.append((key, expand_path(val, home)))
        cfg = cls(tuple(token_files), providers_off=tuple(off), **fields)
        if cfg.warn >= cfg.crit:  # a mistyped pair falls back to the defaults
            cfg = replace(cfg, warn=WARN_PCT, crit=CRIT_PCT)
        return cfg


HIDE_KEY, COMPACT_KEY = "hide", "compact"


def is_off(value: str) -> bool:
    return value.lower() in ("off", "no", "false", "0")


def is_on(value: str) -> bool:
    return value.lower() in ("on", "yes", "true", "1")


def split_labels(value: str) -> Tuple[str, ...]:
    """"c-m, c-nsc" -> ("c-m", "c-nsc"), without blanks or repeats."""
    out: List[str] = []
    for part in value.split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return tuple(out)


def set_account_hidden(path: str, home: str, label: str, hidden: bool) -> None:
    """Add label to, or remove it from, the `hide =` list in the conf file."""
    labels = [x for x in UserConfig.load(path, home).hidden if x != label]
    if hidden:
        labels.append(label)
    set_conf_value(path, HIDE_KEY, ", ".join(labels) if labels else None)


def set_conf_value(path: str, key: str, value: Optional[str]) -> None:
    """Set `key = value` in the conf file, replacing an existing line for key and keeping
    every other line (comments included). value None removes the line."""
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    new_line = "%s = %s" % (key, value)
    out, done = [], False
    for line in lines:
        if line.split("#", 1)[0].split("=", 1)[0].strip() == key and "=" in line:
            if not done and value is not None:
                out.append(new_line)
            done = True
            continue
        out.append(line)
    if not done and value is not None:
        out.append(new_line)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)


# ---------- browser logins ----------

def oauth_expired(oauth: dict, clock: Clock) -> bool:
    """True when a login's access token is past its expiresAt (ms). No expiry: never expires."""
    expires_ms = oauth.get("expiresAt") or 0
    return bool(expires_ms) and expires_ms / 1000 < clock.now()


def login_token(oauth: dict, clock: Clock, tr: Translator) -> str:
    """The access token of a login, or LoginExpired: only Claude Code can renew it, and
    probing with an expired token just returns 401."""
    if oauth_expired(oauth, clock):
        raise LoginExpired(tr("login_expired"))
    return oauth["accessToken"]


# ---------- macOS keychain ----------

class Keychain:
    """The macOS login keychain, through /usr/bin/security."""

    def __init__(self, security: str = "/usr/bin/security", run=subprocess.run):
        self._security = security
        self._run = run

    def services(self, prefix: str) -> List[str]:
        out = self._run([self._security, "dump-keychain"], capture_output=True, text=True,
                        timeout=15).stdout
        names = set(re.findall(r'"svce"<blob>="(%s[^"]*)"' % re.escape(prefix), out))
        return sorted(names, key=lambda s: (s != prefix, s))

    def password(self, service: str) -> str:
        return self._run([self._security, "find-generic-password", "-s", service, "-w"],
                         capture_output=True, text=True, timeout=10).stdout


class KeychainAccount(ClaudeAccount):
    is_login = True

    def __init__(self, service: str, keychain: Keychain, clock: Clock, tr: Translator):
        suffix = service[len(KEYCHAIN_SERVICE):].strip("-")
        super().__init__("claude (%s)" % suffix if suffix else "claude")
        self.service = service
        self._keychain = keychain
        self._clock = clock
        self._tr = tr

    @property
    def identity(self) -> str:
        return self.service

    def token(self) -> str:
        oauth = json.loads(self._keychain.password(self.service))["claudeAiOauth"]
        return login_token(oauth, self._clock, self._tr)


class KeychainSource:
    """Every Claude Code login in the keychain. Unlike the credentials-file fallback, an
    expired one is kept: the keychain is the real store, so the row shows how to renew it."""

    def __init__(self, keychain: Keychain, clock: Clock, tr: Translator):
        self._keychain = keychain
        self._clock = clock
        self._tr = tr

    def accounts(self) -> List[Account]:
        return [KeychainAccount(s, self._keychain, self._clock, self._tr)
                for s in self._keychain.services(KEYCHAIN_SERVICE)]


# ---------- credentials files (Linux, Windows, macOS fallback) ----------

def config_dir_label(directory: str) -> str:
    """~/.claude -> "claude", ~/.claude-work -> "claude (work)"."""
    name = os.path.basename(os.path.normpath(directory)).lstrip(".")
    rest = re.sub(r"^claude[-_.]?", "", name, flags=re.I)
    if not rest:
        return "claude"
    return "claude (%s)" % rest if name.lower().startswith("claude") else name


class CredentialsFileAccount(ClaudeAccount):
    """A browser login stored in <config dir>/.credentials.json."""
    is_login = True

    def __init__(self, path: str, clock: Clock, tr: Translator):
        super().__init__(config_dir_label(os.path.dirname(path)))
        self.path = path
        self._clock = clock
        self._tr = tr

    @property
    def identity(self) -> str:
        return os.path.normcase(os.path.realpath(self.path))

    def oauth(self) -> dict:
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["claudeAiOauth"]

    def expired(self) -> bool:
        return oauth_expired(self.oauth(), self._clock)

    def token(self) -> str:
        return login_token(self.oauth(), self._clock, self._tr)


class CredentialsFileSource:
    """Every config dir that holds a .credentials.json.

    skip_expired: on macOS the keychain is the real store and the file is a fallback that is
    often left behind expired, so expired files are hidden there.
    """

    def __init__(self, config_dirs: Callable[[], Iterable[str]], clock: Clock, skip_expired: bool,
                 tr: Translator):
        self._config_dirs = config_dirs
        self._clock = clock
        self._skip_expired = skip_expired
        self._tr = tr

    def accounts(self) -> List[Account]:
        result: List[Account] = []
        for d in self._config_dirs():
            path = os.path.join(d, CREDENTIALS_FILE)
            if not os.path.isfile(path):
                continue
            acc = CredentialsFileAccount(path, self._clock, self._tr)
            try:
                if self._skip_expired and acc.expired():
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                continue
            result.append(acc)
        return result


class ConfigDirFinder:
    """Candidate Claude config dirs: $CLAUDE_CONFIG_DIR, ~/.claude, ~/.claude-*, and any dir an
    alias or script assigns to CLAUDE_CONFIG_DIR."""

    def __init__(self, home: str, env_dir: Optional[str], scanner: ShellScanner,
                 shell_files: Callable[[], Iterable[Tuple[str, str]]]):
        self._home = home
        self._env_dir = env_dir
        self._scanner = scanner
        self._shell_files = shell_files

    def __call__(self) -> List[str]:
        dirs = [self._env_dir] if self._env_dir else []
        dirs.append(os.path.join(self._home, ".claude"))
        dirs += sorted(glob.glob(os.path.join(self._home, ".claude[-_.]*")))
        for path, _ in self._shell_files():
            dirs += self._scanner.config_dirs(path)
        seen, unique = set(), []
        for d in dirs:
            key = os.path.normcase(os.path.realpath(d))
            if key not in seen:
                seen.add(key)
                unique.append(d)
        return unique


# ---------- setup-tokens ----------

class ShellFiles:
    """(path, default label) of every rc/profile file and script that may set a token."""

    def __init__(self, home: str, rc_files: Sequence[str], is_script: Callable[[str, os.stat_result], bool]):
        self._home = home
        self._rc_files = rc_files
        self._is_script = is_script

    def __call__(self) -> List[Tuple[str, str]]:
        files = [(os.path.join(self._home, rc), os.path.basename(rc)) for rc in self._rc_files]
        for d in SCRIPT_DIRS:
            d = os.path.join(self._home, d)
            try:
                entries = sorted(os.listdir(d))
            except OSError:
                continue
            for e in entries:
                p = os.path.join(d, e)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and self._is_script(p, st):
                    files.append((p, e))
        return files


def is_posix_script(path: str, st: os.stat_result) -> bool:
    return bool(st.st_mode & 0o111)


def is_windows_script(path: str, st: os.stat_result) -> bool:
    return os.path.splitext(path)[1].lower() in WINDOWS_SCRIPT_EXTS


class ShellTokenSource:
    def __init__(self, files: Callable[[], Iterable[Tuple[str, str]]], scanner: ShellScanner):
        self._files = files
        self._scanner = scanner

    def accounts(self) -> List[Account]:
        return [TokenAccount(label, token)
                for path, default in self._files()
                for label, token in self._scanner.tokens(path, default)]


class EnvTokenSource:
    """CLAUDE_CODE_OAUTH_TOKEN from the environment or, on Windows, the user's registry."""

    def __init__(self, readers: Sequence[Callable[[], Optional[str]]]):
        self._readers = readers

    def accounts(self) -> List[Account]:
        result: List[Account] = []
        for read in self._readers:
            try:
                value = read()
            except OSError:
                continue
            token = token_in_text(value)
            if token:
                result.append(TokenAccount("env", token))
        return result


def windows_registry_token() -> Optional[str]:
    """HKCU\\Environment\\CLAUDE_CODE_OAUTH_TOKEN, as written by `setx`."""
    import winreg  # Windows only
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, "CLAUDE_CODE_OAUTH_TOKEN")[0])
    except FileNotFoundError:
        return None


class ConfTokenSource:
    def __init__(self, config: UserConfig):
        self._config = config

    def accounts(self) -> List[Account]:
        result: List[Account] = []
        for label, path in self._config.token_files:
            token = read_token_file(path)
            if token:
                result.append(TokenAccount(label, token))
        return result


class FallbackSource:
    """The fallback's accounts only when the primary source finds none (or fails).

    On macOS Claude Code writes .credentials.json only when it cannot write the keychain, so
    the file is read only if the keychain has no login; otherwise one login could show twice.
    """

    def __init__(self, primary: AccountSource, fallback: AccountSource):
        self._primary = primary
        self._fallback = fallback

    def accounts(self) -> List[Account]:
        try:
            found = self._primary.accounts()
        except Exception:
            found = []
        return found or self._fallback.accounts()


# ---------- registry ----------

class AccountRegistry:
    """All accounts from every source, in source order, without duplicates.
    A source that fails (no keychain, unreadable file) is skipped."""

    def __init__(self, sources: Sequence[AccountSource]):
        self._sources = sources

    def accounts(self) -> List[Account]:
        seen, result = set(), []
        for source in self._sources:
            try:
                found = source.accounts()
            except Exception:
                continue
            for acc in found:
                if acc.identity not in seen:
                    seen.add(acc.identity)
                    result.append(acc)
        return result
