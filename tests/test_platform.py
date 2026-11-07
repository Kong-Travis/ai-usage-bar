"""Composition root: OS detection and which sources each OS gets."""
import srcpath  # noqa: F401  (must come first)

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

from claude_usage import platform_setup
from claude_usage.infra import HttpResponse
from claude_usage.platform_setup import (LINUX, MACOS, WINDOWS, Host, build, build_claude_sources,
                                         detect_os)
from claude_usage.sources import (ConfTokenSource, CredentialsFileSource, EnvTokenSource, FallbackSource,
                                  KeychainSource, ShellTokenSource, UserConfig)

P = "anthropic-ratelimit-unified-"


def tok(name):
    return "sk-ant-oat01-" + name


class FakeClock:
    def __init__(self, t=None):
        self.t = time.time() if t is None else t

    def now(self):
        return self.t


def call_build_sources(host, config, clock):
    from claude_usage.i18n import Translator
    return build_claude_sources(host, config, clock, Translator())


class DetectOs(unittest.TestCase):
    def test_platforms(self):
        self.assertEqual(detect_os("darwin"), MACOS)
        self.assertEqual(detect_os("linux"), LINUX)
        self.assertEqual(detect_os("linux2"), LINUX)
        self.assertEqual(detect_os("freebsd13"), LINUX)
        self.assertEqual(detect_os("win32"), WINDOWS)

    def test_current_platform_is_one_of_the_three(self):
        self.assertIn(detect_os(), (MACOS, LINUX, WINDOWS))


class HostTest(unittest.TestCase):
    def test_paths(self):
        h = Host(LINUX, "/home/u", {}, "/p/plugin.py")
        self.assertEqual(h.cache_dir, os.path.join("/home/u", ".cache", "claude-usage"))
        self.assertEqual(h.conf_path, os.path.join("/home/u", ".config", "claude-usage.conf"))

    def test_current_uses_userprofile_on_windows_and_home_elsewhere(self):
        env = {"HOME": "/home/u", "USERPROFILE": "C:\\Users\\u"}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(platform_setup, "detect_os", return_value=WINDOWS):
                self.assertEqual(Host.current().home, "C:\\Users\\u")
            with mock.patch.object(platform_setup, "detect_os", return_value=LINUX):
                self.assertEqual(Host.current().home, "/home/u")
            with mock.patch.object(platform_setup, "detect_os", return_value=MACOS):
                h = Host.current()
                self.assertEqual((h.os_name, h.home), (MACOS, "/home/u"))


class TempHome(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="cub-plat-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def write(self, rel, text, mode=None):
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(p, mode)
        return p

    def host(self, os_name, env=None):
        return Host(os_name, self.home, env or {}, os.path.join(self.home, "plugin.py"))

    def sources(self, os_name, config=None, env=None, clock=None):
        return call_build_sources(self.host(os_name, env), config or UserConfig(),
                                  clock or FakeClock())


def kinds(sources):
    return [type(s) for s in sources]


class BuildSourcesTest(TempHome):
    def test_macos_has_the_keychain_first(self):
        # The credentials file is only a fallback for an empty keychain on macOS.
        sources = self.sources(MACOS)
        self.assertEqual(kinds(sources), [FallbackSource, ShellTokenSource, EnvTokenSource,
                                          ConfTokenSource])
        self.assertIsInstance(sources[0]._primary, KeychainSource)
        self.assertIsInstance(sources[0]._fallback, CredentialsFileSource)

    def test_linux_and_windows_have_no_keychain(self):
        for os_name in (LINUX, WINDOWS):
            with self.subTest(os=os_name):
                self.assertEqual(kinds(self.sources(os_name)),
                                 [CredentialsFileSource, ShellTokenSource, EnvTokenSource,
                                  ConfTokenSource])

    def test_hiding_logins_drops_the_login_sources(self):
        for os_name in (MACOS, LINUX, WINDOWS):
            with self.subTest(os=os_name):
                got = kinds(self.sources(os_name, UserConfig(show_logins=False)))
                self.assertEqual(got, [ShellTokenSource, EnvTokenSource, ConfTokenSource])

    def credentials_file_account_labels(self, os_name, expires_ms):
        self.write(".claude/.credentials.json",
                   json.dumps({"claudeAiOauth": {"accessToken": "A", "expiresAt": expires_ms}}))
        clock = FakeClock(1000.0)
        sources = [s._fallback if isinstance(s, FallbackSource) else s
                   for s in self.sources(os_name, clock=clock)]
        source = [s for s in sources if isinstance(s, CredentialsFileSource)][0]
        return [a.label for a in source.accounts()]

    def test_macos_hides_an_expired_credentials_file(self):
        self.assertEqual(self.credentials_file_account_labels(MACOS, 500_000), [])
        self.assertEqual(self.credentials_file_account_labels(MACOS, 2_000_000), ["claude"])

    def test_linux_and_windows_keep_an_expired_credentials_file(self):
        for os_name in (LINUX, WINDOWS):
            with self.subTest(os=os_name):
                self.assertEqual(self.credentials_file_account_labels(os_name, 500_000), ["claude"])

    def test_claude_config_dir_from_the_environment_is_searched(self):
        custom = os.path.join(self.home, "elsewhere")
        self.write("elsewhere/.credentials.json",
                   json.dumps({"claudeAiOauth": {"accessToken": "A"}}))
        env = {"CLAUDE_CONFIG_DIR": custom}
        source = [s for s in self.sources(LINUX, env=env) if isinstance(s, CredentialsFileSource)][0]
        self.assertEqual([os.path.dirname(a.path) for a in source.accounts()], [custom])

    def test_environment_token_source(self):
        env = {"CLAUDE_CODE_OAUTH_TOKEN": tok("ENV")}
        source = [s for s in self.sources(LINUX, env=env) if isinstance(s, EnvTokenSource)][0]
        self.assertEqual([(a.label, a.token()) for a in source.accounts()], [("env", tok("ENV"))])

    def test_conf_token_source_uses_the_given_config(self):
        self.write("t.txt", tok("CONF"))
        cfg = UserConfig((("work", os.path.join(self.home, "t.txt")),))
        source = [s for s in self.sources(LINUX, cfg) if isinstance(s, ConfTokenSource)][0]
        self.assertEqual([(a.label, a.token()) for a in source.accounts()], [("work", tok("CONF"))])

    def test_shell_scan_reads_posix_rc_files_on_every_os(self):
        self.write(".zshrc", "alias claude-w='CLAUDE_CODE_OAUTH_TOKEN=%s claude'\n" % tok("RC"))
        for os_name in (LINUX, WINDOWS, MACOS):
            with self.subTest(os=os_name):
                source = [s for s in self.sources(os_name) if isinstance(s, ShellTokenSource)][0]
                self.assertEqual([a.label for a in source.accounts()], ["claude-w"])

    def test_powershell_profile_and_scripts_are_scanned_only_on_windows(self):
        self.write(os.path.join("Documents", "PowerShell", "Microsoft.PowerShell_profile.ps1"),
                   'function claude-ps { $env:CLAUDE_CODE_OAUTH_TOKEN = "%s"; claude @args }\n'
                   % tok("PS"))
        self.write(os.path.join("bin", "claude-ci.cmd"), "set CLAUDE_CODE_OAUTH_TOKEN=%s\n" % tok("CMD"))

        def labels(os_name):
            source = [s for s in self.sources(os_name) if isinstance(s, ShellTokenSource)][0]
            return [a.label for a in source.accounts()]

        self.assertEqual(labels(WINDOWS), ["claude-ps", "claude-ci.cmd"])
        if os.name != "nt":  # Windows reports .cmd files as executable, so posix rules match too
            self.assertEqual(labels(LINUX), [])

    @unittest.skipIf(os.name == "nt", "exec bits are not meaningful on Windows")
    def test_posix_scripts_need_the_exec_bit(self):
        self.write("bin/claude-ci", "export CLAUDE_CODE_OAUTH_TOKEN=%s\n" % tok("X"), 0o755)
        self.write("bin/not-exec", "export CLAUDE_CODE_OAUTH_TOKEN=%s\n" % tok("Y"), 0o644)
        source = [s for s in self.sources(LINUX) if isinstance(s, ShellTokenSource)][0]
        self.assertEqual([a.label for a in source.accounts()], ["claude-ci"])


class FakeHttp:
    """Anthropic answers rate-limit headers; everything else (GitHub) is an error."""

    def __init__(self, clock):
        self.clock = clock
        self.requests = []

    def request(self, url, method="GET", headers=None, body=None, timeout=15):
        self.requests.append((method, url))
        if url.endswith("/v1/messages"):
            now = int(self.clock.now())
            return HttpResponse(200, {
                P + "5h-utilization": "0.1", P + "5h-reset": str(now + 3600),
                P + "7d-utilization": "0.2", P + "7d-reset": str(now + 86400)})
        return HttpResponse(404)


class BuildTest(TempHome):
    def test_build_wires_a_working_app(self):
        self.write(".config/claude-usage.conf", "keychain = off\nci = ~/t.txt\n")
        self.write("t.txt", tok("CI"))
        clock = FakeClock()
        http = FakeHttp(clock)
        services = build(self.host(LINUX), clock=clock, http=http)
        menu = services.app.menu()
        self.assertEqual(menu.title, "C 10/20")
        self.assertIn("C: ci - token", [i.text for i in menu.items])
        self.assertFalse(menu.update_available)
        # The result is cached under the home dir, and the run log is written next to it.
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".cache", "claude-usage", "last-run.log")))

    def fake_cli(self, name, script):
        """An executable `name` in ~/.local/bin that runs tests/<script> like the real CLI."""
        here = os.path.dirname(os.path.abspath(__file__))
        path = self.write(".local/bin/" + name, "#!/bin/sh\nexec '%s' '%s' \"$@\"\n"
                          % (sys.executable, os.path.join(here, script)))
        os.chmod(path, 0o755)

    @unittest.skipIf(os.name == "nt", "fake CLIs are shell scripts")
    def test_codex_and_agy_accounts_flow_from_discovery_to_the_menu(self):
        # The real flow with the real CLIs replaced: found on a minimal PATH (as SwiftBar runs
        # it), discovered from their home folders, read over their own protocols, then shown.
        self.write(".config/claude-usage.conf", "keychain = off\nci = ~/t.txt\n")
        self.write("t.txt", tok("CI"))
        self.write(".codex/auth.json", json.dumps({"auth_mode": "chatgpt", "tokens": {"a": "b"}}))
        os.makedirs(os.path.join(self.home, ".gemini", "antigravity-cli"))
        self.fake_cli("codex", "fake_codex.py")
        self.fake_cli("agy", "fake_agy.py")
        clock = FakeClock()
        env = {"PATH": "/usr/bin:/bin", "HOME": self.home}
        services = build(self.host(MACOS, env), clock=clock, http=FakeHttp(clock))
        menu = services.app.menu()
        texts = [i.text for i in menu.items]
        self.assertEqual(menu.title.split()[0::2], ["C", "Cx", "Ag"])
        self.assertTrue(any("x@y.com" in t for t in texts), texts)
        self.assertTrue(any("Gemini" in t for t in texts), texts)

    @unittest.skipIf(os.name == "nt", "fake CLIs are shell scripts")
    def test_a_limit_past_crit_notifies_once_unless_the_account_is_hidden(self):
        # The fake Codex reports 78% of its 5-hour limit, past crit = 75.
        self.write(".codex/auth.json", json.dumps({"auth_mode": "chatgpt", "tokens": {"a": "b"}}))
        self.fake_cli("codex", "fake_codex.py")
        clock = FakeClock()
        host = self.host(MACOS, {"PATH": "/usr/bin:/bin", "HOME": self.home})

        class Recorder:
            def __init__(self):
                self.sent = []

            def notify(self, title, body):
                self.sent.append((title, body))

        hidden = Recorder()
        self.write(".config/claude-usage.conf", "keychain = off\ncrit = 75\nhide = codex\n")
        build(host, clock=clock, http=FakeHttp(clock), notifier=hidden).app.menu()
        self.assertEqual(hidden.sent, [])

        shown = Recorder()
        self.write(".config/claude-usage.conf", "keychain = off\ncrit = 75\n")
        for _ in range(2):  # the second menu must not repeat the alert
            build(host, clock=clock, http=FakeHttp(clock), notifier=shown).app.menu()
        self.assertEqual([title for title, _ in shown.sent], ["Codex"])
        self.assertIn("78%", shown.sent[0][1])

    def test_providers_can_be_switched_off(self):
        self.write(".config/claude-usage.conf", "keychain = off\ncodex = off\nagy = off\n")
        self.write(".codex/auth.json", json.dumps({"auth_mode": "chatgpt", "tokens": {"a": "b"}}))
        os.makedirs(os.path.join(self.home, ".gemini", "antigravity-cli"))
        self.fake_cli("codex", "fake_codex.py")
        self.fake_cli("agy", "fake_agy.py")
        clock = FakeClock()
        services = build(self.host(MACOS, {"PATH": "/usr/bin:/bin", "HOME": self.home}),
                         clock=clock, http=FakeHttp(clock))
        self.assertEqual(services.app.menu().title, "AI ?")

    def test_build_without_any_account(self):
        self.write(".config/claude-usage.conf", "keychain = off\n")
        clock = FakeClock()
        services = build(self.host(LINUX), clock=clock, http=FakeHttp(clock))
        self.assertEqual(services.app.menu().title, "AI ?")

    def test_updater_targets_the_plugin_file(self):
        self.write(".config/claude-usage.conf", "keychain = off\n")
        clock = FakeClock()
        services = build(self.host(LINUX), clock=clock, http=FakeHttp(clock))
        self.assertFalse(services.updater.update())  # GitHub says 404: nothing to install


if __name__ == "__main__":
    unittest.main()
