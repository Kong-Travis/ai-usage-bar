"""Composition root: the only place that knows which adapters each OS gets."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Mapping, Tuple

from .app import RunLog, UsageApp
from .config import (EMAIL_EVERY, MAX_IN_TITLE, MODEL_WINDOWS, PLUGIN_PATH_IN_REPO, PROBE_EVERY,
                     PROBE_MODELS, REPO, REPO_URL, SHOW_SUPPORT, UPDATE_EVERY, VERSION,
                     Endpoints)
from .antigravity import AgyReader, AgySource
from .codex import CodexReader, CodexSource
from .domain import AGY, CLAUDE, CODEX, AccountSource, Clock, SystemClock, UsageReader
from .i18n import Translator, normalize_language
from .infra import CliLocator, HttpClient, JsonCache, UrllibHttpClient
from .notify import LimitAlerts, Notifier, NullNotifier
from .scan import ShellScanner
from .sources import (POSIX_RC_FILES, WINDOWS_RC_FILES, AccountRegistry, ConfigDirFinder,
                      ConfTokenSource, CredentialsFileSource, EnvTokenSource, FallbackSource, Keychain,
                      KeychainSource, ShellFiles, ShellTokenSource, UserConfig, is_posix_script,
                      is_windows_script, windows_registry_token)
from .updater import ReleaseChecker, SelfUpdater
from .usage import IdentityService, ProfileClient, UsageProbe, UsageService
from .view import Presenter

MACOS, LINUX, WINDOWS = "macos", "linux", "windows"


def detect_os(platform: str = sys.platform) -> str:
    if platform == "darwin":
        return MACOS
    if platform.startswith("win"):
        return WINDOWS
    return LINUX


@dataclass(frozen=True)
class Host:
    os_name: str               # MACOS | LINUX | WINDOWS
    home: str
    env: Mapping[str, str]
    self_path: str             # the plugin file itself

    @classmethod
    def current(cls) -> Host:
        env = os.environ
        os_name = detect_os()
        home = env.get("USERPROFILE") if os_name == WINDOWS and env.get("USERPROFILE") else env.get("HOME")
        return cls(os_name, home or os.path.expanduser("~"), env, os.path.realpath(sys.argv[0]))

    @property
    def cache_dir(self) -> str:
        return os.path.join(self.home, ".cache", "claude-usage")

    @property
    def conf_path(self) -> str:
        return os.path.join(self.home, ".config", "claude-usage.conf")


def build_translator(host: Host, config: UserConfig) -> Translator:
    """CLAUDE_USAGE_LANG, else `language =` in the conf file, else Vietnamese."""
    return Translator(normalize_language(host.env.get("CLAUDE_USAGE_LANG")) or config.language or "")


def build_claude_sources(host: Host, config: UserConfig, clock: Clock,
                         tr: Translator) -> List[AccountSource]:
    scanner = ShellScanner(host.home)
    if host.os_name == WINDOWS:
        files = ShellFiles(host.home, WINDOWS_RC_FILES, is_windows_script)
        env_readers = [lambda: host.env.get("CLAUDE_CODE_OAUTH_TOKEN"), windows_registry_token]
    else:
        files = ShellFiles(host.home, POSIX_RC_FILES, is_posix_script)
        env_readers = [lambda: host.env.get("CLAUDE_CODE_OAUTH_TOKEN")]

    sources: List[AccountSource] = []
    if config.show_logins:
        dirs = ConfigDirFinder(host.home, host.env.get("CLAUDE_CONFIG_DIR"), scanner, files)
        cred_files = CredentialsFileSource(dirs, clock, host.os_name == MACOS, tr)
        if host.os_name == MACOS:
            sources.append(FallbackSource(KeychainSource(Keychain(), clock, tr), cred_files))
        else:
            sources.append(cred_files)
    sources += [ShellTokenSource(files, scanner), EnvTokenSource(env_readers),
                ConfTokenSource(config)]
    return sources


def build_providers(host: Host, config: UserConfig, clock: Clock, tr: Translator, http: HttpClient,
                    endpoints: Endpoints) -> Tuple[List[AccountSource], Dict[str, UsageReader]]:
    """Every account source, in menu order, and the usage reader of each provider. Codex and
    Antigravity are read through their own CLIs, so they appear only when installed."""
    sources = build_claude_sources(host, config, clock, tr)
    readers: Dict[str, UsageReader] = {
        CLAUDE: UsageProbe(http, endpoints.api, PROBE_MODELS, MODEL_WINDOWS)}
    locator = CliLocator(host.home, host.env, host.os_name == WINDOWS)
    if CODEX not in config.providers_off:
        codex = locator.find("codex")
        sources.append(CodexSource(host.home, host.env, codex))
        if codex:
            readers[CODEX] = CodexReader(codex)
    if AGY not in config.providers_off:
        agy = locator.find("agy")
        sources.append(AgySource(host.home, agy))
        if agy:
            readers[AGY] = AgyReader(agy, os.path.join(host.cache_dir, "agy.log"))
    return sources, readers


@dataclass
class Services:
    app: UsageApp
    updater: SelfUpdater
    tr: Translator


def build(host: Host, clock: Clock = None, http: HttpClient = None,
          notifier: Notifier = None) -> Services:
    """notifier: how this frontend shows desktop notifications; none by default."""
    clock = clock or SystemClock()
    http = http or UrllibHttpClient()
    endpoints = Endpoints.from_env(host.env)
    cache = JsonCache(host.cache_dir, clock)
    config = UserConfig.load(host.conf_path, host.home)
    tr = build_translator(host, config)
    releases = ReleaseChecker(http, endpoints.github_api, REPO, cache, UPDATE_EVERY)
    sources, readers = build_providers(host, config, clock, tr, http, endpoints)
    presenter = Presenter(clock, tr, VERSION, REPO_URL, config.warn, config.crit, MAX_IN_TITLE,
                          SHOW_SUPPORT, hidden=config.hidden, compact=config.compact)
    # Alerts use the same crit as the red colour, so a notification matches what the bar shows.
    alerts = LimitAlerts(notifier if notifier and config.notify else NullNotifier(), cache, tr,
                         config.crit, presenter.time_left, presenter.window_name, clock)
    app = UsageApp(
        registry=AccountRegistry(sources),
        usage=UsageService(readers, cache, PROBE_EVERY),
        identity=IdentityService(ProfileClient(http, endpoints.api), cache, EMAIL_EVERY),
        releases=releases,
        presenter=presenter,
        run_log=RunLog(host.cache_dir, clock, VERSION),
        version=VERSION,
        # No alerts for an account the user took out of the bar.
        alerts=lambda rows: alerts.check([r for r in rows if r.label not in config.hidden]),
    )
    updater = SelfUpdater(http, releases, endpoints.github_raw, REPO, PLUGIN_PATH_IN_REPO, VERSION,
                          host.self_path)
    return Services(app, updater, tr)
