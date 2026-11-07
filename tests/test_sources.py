"""Account sources and the registry that merges them."""
import srcpath  # noqa: F401  (must come first)

import inspect
import json
import os
import shutil
import tempfile
import types
import unittest

from claude_usage.domain import LoginExpired, TokenAccount
from claude_usage.i18n import Translator
from claude_usage.scan import ShellScanner
from claude_usage.sources import (AccountRegistry, ConfigDirFinder, ConfTokenSource,
                                  CredentialsFileAccount, CredentialsFileSource, EnvTokenSource,
                                  Keychain, KeychainAccount, KeychainSource, ShellFiles,
                                  ShellTokenSource, UserConfig, config_dir_label,
                                  is_posix_script, is_windows_script)


def keychain_account(service, keychain, clock=None):
    return KeychainAccount(service, keychain, clock or FakeClock(), Translator())


def tok(name):
    return "sk-ant-oat01-" + name


def _takes_translator(cls):
    return "tr" in inspect.signature(cls.__init__).parameters


def make_account(path, clock):
    """CredentialsFileAccount, with a translator when the class asks for one."""
    if _takes_translator(CredentialsFileAccount):
        from claude_usage.i18n import Translator
        return CredentialsFileAccount(path, clock, Translator())
    return CredentialsFileAccount(path, clock)


def make_source(dirs, clock, skip_expired):
    if _takes_translator(CredentialsFileSource):
        from claude_usage.i18n import Translator
        return CredentialsFileSource(dirs, clock, skip_expired, Translator())
    return CredentialsFileSource(dirs, clock, skip_expired)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t


class TempHome(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="cub-src-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def write(self, rel, text, mode=None):
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(p, mode)
        return p

    def path(self, *parts):
        return os.path.join(self.home, *parts)


class UserConfigTest(TempHome):
    def load(self, text):
        return UserConfig.load(self.write(".config/claude-usage.conf", text), self.home)

    def test_token_files_with_comments_and_tilde(self):
        cfg = self.load("# my tokens\n"
                        "work = ~/tokens/work.txt   # the work one\n"
                        "\n"
                        "ci=$HOME/ci.txt\n"
                        "not a setting\n"
                        "empty =\n")
        self.assertEqual(cfg.token_files, (("work", self.home + "/tokens/work.txt"),
                                           ("ci", self.home + "/ci.txt")))
        self.assertTrue(cfg.show_logins)

    def test_keychain_off_hides_logins(self):
        self.assertFalse(self.load("keychain = off\n").show_logins)

    def test_logins_off_hides_logins(self):
        for value in ("off", "OFF", "no", "false", "0"):
            with self.subTest(value=value):
                self.assertFalse(self.load("logins = %s\n" % value).show_logins)

    def test_keychain_on_keeps_logins_and_adds_no_token_file(self):
        cfg = self.load("keychain = on\n")
        self.assertTrue(cfg.show_logins)
        self.assertEqual(cfg.token_files, ())

    def test_thresholds_notify_and_provider_switches(self):
        cfg = self.load("warn = 60\ncrit = 85\nnotify = off\ncodex = off\nagy = on\n")
        self.assertEqual((cfg.warn, cfg.crit, cfg.notify), (60, 85, False))
        self.assertEqual(cfg.providers_off, ("codex",))
        self.assertEqual(cfg.token_files, ())

    def test_bad_thresholds_fall_back_to_the_defaults(self):
        for text in ("warn = 95\ncrit = 90\n", "warn = abc\n", "crit = 0\n", "crit = 150\n"):
            with self.subTest(text=text):
                cfg = self.load(text)
                self.assertEqual((cfg.warn, cfg.crit), (UserConfig().warn, UserConfig().crit))

    def test_a_provider_name_with_a_path_is_still_a_claude_token_file(self):
        cfg = self.load("codex = ~/t/codex.txt\n")
        self.assertEqual(cfg.token_files, (("codex", self.home + "/t/codex.txt"),))
        self.assertEqual(cfg.providers_off, ())

    def test_missing_file_gives_defaults(self):
        cfg = UserConfig.load(self.path("absent.conf"), self.home)
        self.assertEqual(cfg, UserConfig())


class FakeRun:
    """Stands in for subprocess.run and records the commands."""

    def __init__(self, outputs):
        self.outputs = outputs  # argv[1] -> stdout
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return types.SimpleNamespace(stdout=self.outputs[argv[1]])


DUMP = '\n'.join([
    '    "svce"<blob>="Claude Code-credentials-zzz"',
    '    "svce"<blob>="Other app"',
    '    "svce"<blob>="Claude Code-credentials-abc"',
    '    "svce"<blob>="Claude Code-credentials"',
    '    "svce"<blob>="Claude Code-credentials-abc"',
])


class KeychainTest(unittest.TestCase):
    def test_services_default_first_then_sorted_without_duplicates(self):
        run = FakeRun({"dump-keychain": DUMP})
        got = Keychain("/sec", run=run).services("Claude Code-credentials")
        self.assertEqual(got, ["Claude Code-credentials", "Claude Code-credentials-abc",
                               "Claude Code-credentials-zzz"])
        self.assertEqual(run.calls, [["/sec", "dump-keychain"]])

    def test_services_when_the_default_is_missing(self):
        run = FakeRun({"dump-keychain": '"svce"<blob>="Claude Code-credentials-b"\n'})
        self.assertEqual(Keychain(run=run).services("Claude Code-credentials"),
                         ["Claude Code-credentials-b"])

    def test_password_asks_for_the_service(self):
        run = FakeRun({"find-generic-password": "secret"})
        self.assertEqual(Keychain("/sec", run=run).password("svc"), "secret")
        self.assertEqual(run.calls, [["/sec", "find-generic-password", "-s", "svc", "-w"]])

    def test_labels(self):
        kc = Keychain(run=FakeRun({}))
        self.assertEqual(keychain_account("Claude Code-credentials", kc).label, "claude")
        self.assertEqual(keychain_account("Claude Code-credentials-a1b2", kc).label, "claude (a1b2)")

    def test_account_is_a_login_identified_by_its_service(self):
        acc = keychain_account("Claude Code-credentials-x", Keychain(run=FakeRun({})))
        self.assertTrue(acc.is_login)
        self.assertEqual(acc.identity, "Claude Code-credentials-x")

    def test_token_is_read_from_the_keychain_json_with_one_lookup(self):
        run = FakeRun({"find-generic-password": creds(2_000_000)})
        acc = keychain_account("Claude Code-credentials", Keychain(run=run))
        self.assertEqual(acc.token(), "ACCESS")
        self.assertEqual(len(run.calls), 1)

    def test_no_expiry_in_the_keychain_never_expires(self):
        for blob in (creds(), creds(0)):
            with self.subTest(blob=blob):
                kc = Keychain(run=FakeRun({"find-generic-password": blob}))
                self.assertEqual(keychain_account("Claude Code-credentials", kc).token(), "ACCESS")

    def test_expired_keychain_login_raises_login_expired_with_the_renew_hint(self):
        kc = Keychain(run=FakeRun({"find-generic-password": creds(500_000)}))
        acc = keychain_account("Claude Code-credentials", kc, FakeClock(1000.0))
        with self.assertRaises(LoginExpired) as cm:
            acc.token()
        self.assertEqual(str(cm.exception), Translator()("login_expired"))

    def test_a_locked_keychain_raises_on_token(self):
        acc = keychain_account("Claude Code-credentials", Keychain(run=FakeRun({"find-generic-password": ""})))
        with self.assertRaises(ValueError):
            acc.token()

    def test_source_lists_one_account_per_service_in_order(self):
        src = KeychainSource(Keychain(run=FakeRun({"dump-keychain": DUMP})), FakeClock(), Translator())
        self.assertEqual([a.label for a in src.accounts()],
                         ["claude", "claude (abc)", "claude (zzz)"])

    def test_source_keeps_an_expired_login_so_its_row_can_say_how_to_renew(self):
        run = FakeRun({"dump-keychain": DUMP, "find-generic-password": creds(500_000)})
        accounts = KeychainSource(Keychain(run=run), FakeClock(1000.0), Translator()).accounts()
        self.assertEqual(len(accounts), 3)
        with self.assertRaises(LoginExpired):
            accounts[0].token()


class ConfigDirLabel(unittest.TestCase):
    def test_labels(self):
        join = os.path.join
        cases = [
            (join("h", ".claude"), "claude"),
            (join("h", ".claude-work"), "claude (work)"),
            (join("h", ".claude_work"), "claude (work)"),
            (join("h", ".claude.work"), "claude (work)"),
            (join("h", ".claude-work") + os.sep, "claude (work)"),
            (join("h", "claude"), "claude"),
            (join("h", ".work"), "work"),
        ]
        for directory, want in cases:
            with self.subTest(directory=directory):
                self.assertEqual(config_dir_label(directory), want)


def creds(expires_ms=None, token="ACCESS"):
    oauth = {"accessToken": token}
    if expires_ms is not None:
        oauth["expiresAt"] = expires_ms
    return json.dumps({"claudeAiOauth": oauth})


class CredentialsFileTest(TempHome):
    def account(self, text, d=".claude-work", now=1000.0):
        p = self.write(os.path.join(d, ".credentials.json"), text)
        return make_account(p, FakeClock(now))

    def test_valid_login(self):
        acc = self.account(creds(2_000_000))
        self.assertTrue(acc.is_login)
        self.assertEqual(acc.label, "claude (work)")
        self.assertEqual(acc.token(), "ACCESS")
        self.assertFalse(acc.expired())

    def test_expired_login_raises_login_expired(self):
        acc = self.account(creds(500_000))
        self.assertTrue(acc.expired())
        with self.assertRaises(LoginExpired):
            acc.token()

    def test_no_expiry_never_expires(self):
        for text in (creds(), creds(0)):
            with self.subTest(text=text):
                self.assertFalse(self.account(text).expired())

    def test_identity_is_the_resolved_path(self):
        acc = self.account(creds(2_000_000))
        self.assertEqual(acc.identity, os.path.normcase(os.path.realpath(acc.path)))
        self.assertEqual(acc.cache_key, self.account(creds(2_000_000)).cache_key)


class CredentialsFileSourceTest(TempHome):
    def setUp(self):
        super().setUp()
        self.dirs = []
        for name, text in (("good", creds(2_000_000)),
                           ("expired", creds(500_000)),
                           ("corrupt", "{not json"),
                           ("nokey", "{}"),
                           ("badtype", creds("soon"))):
            self.write(os.path.join(name, ".credentials.json"), text)
            self.dirs.append(self.path(name))
        os.makedirs(self.path("empty"))
        self.dirs.append(self.path("empty"))
        self.dirs.append(self.path("missing"))

    def labels(self, skip_expired):
        src = make_source(lambda: self.dirs, FakeClock(1000.0), skip_expired)
        return [os.path.basename(os.path.dirname(a.path)) for a in src.accounts()]

    def test_skip_expired_hides_expired_and_corrupt_files(self):
        self.assertEqual(self.labels(True), ["good"])

    def test_keeping_expired_shows_the_expired_login(self):
        got = self.labels(False)
        self.assertIn("good", got)
        self.assertIn("expired", got)
        self.assertNotIn("empty", got)
        self.assertNotIn("missing", got)


class ConfigDirFinderTest(TempHome):
    def finder(self, env_dir=None, shell_files=()):
        return ConfigDirFinder(self.home, env_dir, ShellScanner(self.home), lambda: list(shell_files))

    def test_order_env_default_glob_then_aliases(self):
        for d in (".claude", ".claude-work", ".claude_x", ".claudefoo", ".config"):
            os.makedirs(self.path(d))
        rc = self.write(".zshrc", "alias a='CLAUDE_CONFIG_DIR=~/.claude-alias claude'\n")
        got = self.finder(self.path("custom"), [(rc, ".zshrc")])()
        self.assertEqual(got, [self.path("custom"), self.path(".claude"), self.path(".claude-work"),
                               self.path(".claude_x"), self.home + "/.claude-alias"])

    def test_dedupe_by_resolved_path(self):
        os.makedirs(self.path(".claude-work"))
        rc = self.write(".zshrc",
                        "alias a='CLAUDE_CONFIG_DIR=~/.claude-work claude'\n"
                        "alias b='CLAUDE_CONFIG_DIR=%s/./.claude-work claude'\n"
                        "alias c='CLAUDE_CONFIG_DIR=~/.claude claude'\n" % self.home)
        got = self.finder(self.path(".claude-work"), [(rc, "rc")])()
        self.assertEqual(got, [self.path(".claude-work"), self.path(".claude")])

    def test_without_anything_only_the_default_is_a_candidate(self):
        self.assertEqual(self.finder()(), [self.path(".claude")])


class ShellFilesTest(TempHome):
    RC = (".zshrc", os.path.join("Documents", "profile.ps1"))

    def test_rc_files_come_first_and_keep_their_basename_as_label(self):
        got = ShellFiles(self.home, self.RC, is_windows_script)()
        self.assertEqual(got[:2], [(self.path(".zshrc"), ".zshrc"),
                                   (self.path("Documents", "profile.ps1"), "profile.ps1")])

    def test_windows_predicate_goes_by_extension(self):
        names = ["a.ps1", "b.CMD", "c.bat", "d.sh", "noext", "e.txt", "f.py", "g.exe"]
        for n in names:
            self.write(os.path.join("bin", n), "x")
        os.makedirs(self.path("bin", "subdir.ps1"))
        got = [label for _, label in ShellFiles(self.home, (), is_windows_script)()]
        self.assertEqual(got, ["a.ps1", "b.CMD", "c.bat", "d.sh", "noext"])

    @unittest.skipIf(os.name == "nt", "exec bits are not meaningful on Windows")
    def test_posix_predicate_goes_by_exec_bit(self):
        self.write("bin/runme", "x", 0o755)
        self.write("bin/readme.txt", "x", 0o644)
        self.write(".local/bin/tool", "x", 0o700)
        os.makedirs(self.path("bin", "dir"), exist_ok=True)
        os.chmod(self.path("bin", "dir"), 0o755)
        got = ShellFiles(self.home, (), is_posix_script)()
        self.assertEqual(got, [(self.path("bin", "runme"), "runme"),
                               (self.path(".local", "bin", "tool"), "tool")])

    def test_missing_script_dirs_are_fine(self):
        self.assertEqual(ShellFiles(self.home, (), is_posix_script)(), [])


class TokenSources(TempHome):
    def test_shell_token_source(self):
        a = self.write(".zshrc", "alias claude-w='CLAUDE_CODE_OAUTH_TOKEN=%s claude'\n" % tok("W"))
        b = self.write("bin/ci", "export CLAUDE_CODE_OAUTH_TOKEN=%s\n" % tok("CI"))
        src = ShellTokenSource(lambda: [(a, ".zshrc"), (b, "ci"), (self.path("absent"), "x")],
                               ShellScanner(self.home))
        got = [(x.label, x.token()) for x in src.accounts()]
        self.assertEqual(got, [("claude-w", tok("W")), ("ci", tok("CI"))])
        self.assertFalse(src.accounts()[0].is_login)

    def test_env_source_skips_empty_garbage_and_failing_readers(self):
        def boom():
            raise OSError("registry unavailable")

        src = EnvTokenSource([lambda: tok("A"), boom, lambda: None, lambda: "nonsense",
                              lambda: "export X=" + tok("B")])
        got = [(a.label, a.token()) for a in src.accounts()]
        self.assertEqual(got, [("env", tok("A")), ("env", tok("B"))])

    def test_conf_token_source_reads_the_files_it_can(self):
        self.write("t/a.txt", tok("A") + "\n")
        self.write("t/empty.txt", "")
        cfg = UserConfig((("work", self.path("t", "a.txt")), ("none", self.path("t", "empty.txt")),
                          ("gone", self.path("t", "missing.txt"))))
        got = [(a.label, a.token()) for a in ConfTokenSource(cfg).accounts()]
        self.assertEqual(got, [("work", tok("A"))])


class FixedSource:
    def __init__(self, *accounts):
        self._accounts = list(accounts)

    def accounts(self):
        return list(self._accounts)


class FailingSource:
    def accounts(self):
        raise RuntimeError("keychain is locked")


class RegistryTest(unittest.TestCase):
    def test_source_order_is_kept(self):
        a, b, c = (TokenAccount(n, tok(n)) for n in "abc")
        reg = AccountRegistry([FixedSource(b), FixedSource(a, c)])
        self.assertEqual([x.label for x in reg.accounts()], ["b", "a", "c"])

    def test_duplicates_by_identity_keep_the_first(self):
        first = TokenAccount("claude-w", tok("SAME"))
        second = TokenAccount("env", tok("SAME"))
        other = TokenAccount("other", tok("OTHER"))
        reg = AccountRegistry([FixedSource(first), FixedSource(second, other)])
        self.assertEqual([x.label for x in reg.accounts()], ["claude-w", "other"])

    def test_a_failing_source_is_skipped(self):
        a = TokenAccount("a", tok("A"))
        reg = AccountRegistry([FailingSource(), FixedSource(a), FailingSource()])
        self.assertEqual(reg.accounts(), [a])

    def test_no_sources(self):
        self.assertEqual(AccountRegistry([]).accounts(), [])


if __name__ == "__main__":
    unittest.main()


class ReviewRegressions(unittest.TestCase):
    """Cases found in review: Windows file encodings, cache write failures, fallback logins."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name, data: bytes):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_token_file_written_by_powershell_51_is_read(self):
        from claude_usage.scan import read_token_file
        token = "sk-ant-oat01-PS51"
        self.assertEqual(read_token_file(self.path("t1", (token + "\r\n").encode("utf-16"))), token)
        self.assertEqual(read_token_file(self.path("t2", token.encode("utf-16-le"))), token)

    def test_conf_saved_with_a_bom_keeps_its_first_line(self):
        p = self.path("c.conf", b"\xef\xbb\xbfkeychain = off\nlanguage = en\n")
        conf = UserConfig.load(p, self.dir)
        self.assertEqual((conf.show_logins, conf.language, conf.token_files), (False, "en", ()))

    def test_a_cache_that_cannot_be_written_does_not_raise(self):
        from claude_usage.infra import JsonCache
        blocker = self.path("not-a-dir", b"")
        cache = JsonCache(os.path.join(blocker, "sub"), FakeClockForReview())
        self.assertFalse(cache.save("x", {"a": 1}))
        ok = JsonCache(self.dir, FakeClockForReview())
        self.assertTrue(ok.save("x", {"a": 1}))
        self.assertEqual(ok.load("x"), {"a": 1})
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])

    def test_fallback_source_is_used_only_when_the_primary_finds_nothing(self):
        from claude_usage.sources import FallbackSource

        class Fixed:
            def __init__(self, accounts=None, error=None):
                self._accounts, self._error = accounts or [], error

            def accounts(self):
                if self._error:
                    raise self._error
                return list(self._accounts)

        a, b = TokenAccount("a", "sk-ant-oat01-A"), TokenAccount("b", "sk-ant-oat01-B")
        self.assertEqual(FallbackSource(Fixed([a]), Fixed([b])).accounts(), [a])
        self.assertEqual(FallbackSource(Fixed([]), Fixed([b])).accounts(), [b])
        self.assertEqual(FallbackSource(Fixed(error=OSError("locked")), Fixed([b])).accounts(), [b])


class FakeClockForReview:
    def now(self):
        return 1000.0
