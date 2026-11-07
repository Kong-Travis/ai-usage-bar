"""Codex: login discovery, limit parsing and the app-server conversation."""
import srcpath  # noqa: F401  (must come first)

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

from claude_usage.codex import CodexAccount, CodexReader, CodexSource, codex_build_usage
from claude_usage.domain import CODEX, FIVE_HOUR, WEEKLY, Window
from claude_usage.infra import Cli

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_codex.py")
CHATGPT = {"auth_mode": "chatgpt", "tokens": {"access_token": "secret"}}


def limit(used, minutes, reset=1000):
    return {"usedPercent": used, "windowDurationMins": minutes, "resetsAt": reset}


# A launcher that starts the real program as a child and waits for it, like an npm .cmd
# shim or a shell wrapper: killing only the launcher leaves the child holding our pipes.
FORKING_LAUNCHER = (sys.executable, "-c",
                    "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))",
                    sys.executable)


class TempHome(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)

    def login(self, name, auth=CHATGPT):
        path = os.path.join(self.home, name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "auth.json"), "w") as f:
            json.dump(auth, f)
        return path


class DiscoveryTest(TempHome):
    def accounts(self, env=None, cli=Cli(("codex",), {})):
        return CodexSource(self.home, env or {}, cli).accounts()

    def test_chatgpt_logins_are_found_with_labels(self):
        self.login(".codex")
        self.login(".codex-work")
        self.login(".codex_alt", {"tokens": {"id_token": "x"}})
        found = self.accounts()
        self.assertEqual([a.label for a in found], ["codex", "codex-work", "codex_alt"])
        self.assertTrue(all(a.provider == CODEX for a in found))

    def test_api_key_and_broken_logins_have_no_plan_limits(self):
        self.login(".codex", {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x", "tokens": {}})
        self.login(".codex-bad", {"auth_mode": "apikey"})
        os.makedirs(os.path.join(self.home, ".codex-empty"))
        with open(os.path.join(self.home, ".codex-bad", "auth.json"), "w") as f:
            f.write("{not json")
        self.assertEqual(self.accounts(), [])

    def test_no_cli_means_no_accounts(self):
        self.login(".codex")
        self.assertEqual(self.accounts(cli=None), [])

    def test_codex_home_comes_first_and_duplicates_collapse(self):
        main = self.login(".codex")
        other = self.login("elsewhere/team")
        found = self.accounts({"CODEX_HOME": other})
        self.assertEqual([a.home for a in found], [other, main])
        self.assertEqual(found[0].label, "team")
        # CODEX_HOME pointing at the default dir (even through a symlink) is one account
        link = os.path.join(self.home, "link")
        os.symlink(main, link)
        self.assertEqual([a.home for a in self.accounts({"CODEX_HOME": link})], [link])

    def test_identity_is_the_resolved_path(self):
        path = self.login(".codex")
        self.assertEqual(CodexAccount(path).identity, os.path.normcase(os.path.realpath(path)))


class ParsingTest(unittest.TestCase):
    def usage(self, snapshots, plan="plus", email="a@b.c", main=None):
        account = {"account": {"planType": plan, "email": email}}
        limits = {"rateLimits": main or snapshots.get("codex", {}), "rateLimitsByLimitId": snapshots}
        return codex_build_usage(account, limits)

    def test_five_hour_and_weekly_are_classified_shortest_first(self):
        usage = self.usage({"codex": {"primary": limit(73, 10080, 5), "secondary": limit(78, 300, 4)}})
        self.assertEqual(usage.windows, (Window(FIVE_HOUR, 78.0, 4, 18000), Window(WEEKLY, 73.0, 5, 604800)))
        self.assertEqual((usage.plan, usage.email), ("Plus", "a@b.c"))

    def test_other_durations_are_named_by_divisibility(self):
        usage = self.usage({"codex": {"primary": limit(1, 1440), "secondary": limit(2, 120)}})
        self.assertEqual([w.kind for w in usage.windows], ["2h", "1d"])
        self.assertEqual(self.usage({"codex": {"primary": limit(1, 45)}}).windows[0].kind, "45m")

    def test_extra_limit_follows_the_main_one_under_its_name(self):
        spark = {"limitName": None, "primary": limit(5, 300)}
        named = {"limitName": "Spark", "primary": limit(6, 300)}
        usage = self.usage({"codex_spark": spark, "codex": {"primary": limit(40, 300)}, "x": named})
        self.assertEqual([(w.name, w.pct) for w in usage.windows],
                         [("", 40.0), ("codex_spark", 5.0), ("Spark", 6.0)])

    def test_a_window_without_usedPercent_is_left_out_not_zero(self):
        usage = self.usage({"codex": {"primary": {"windowDurationMins": 300}, "secondary": limit(9, 10080)}})
        self.assertEqual([(w.kind, w.pct) for w in usage.windows], [(WEEKLY, 9.0)])

    def test_missing_reset_is_zero_and_pct_is_clamped(self):
        usage = self.usage({"codex": {"primary": {"usedPercent": 140, "windowDurationMins": 300},
                                      "secondary": limit(-3, 10080)}})
        self.assertEqual([(w.pct, w.reset) for w in usage.windows], [(100.0, 0), (0.0, 1000)])

    def test_falls_back_to_rateLimits_and_to_plan_of_the_snapshot(self):
        main = {"primary": limit(10, 300), "planType": "pro"}
        usage = codex_build_usage({"account": None}, {"rateLimits": main})
        self.assertEqual((usage.windows[0].pct, usage.plan, usage.email), (10.0, "Pro", ""))

    def test_no_windows_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "no Codex limits"):
            codex_build_usage({}, {"rateLimits": {"primary": None, "secondary": {}}})


class ReaderTest(TempHome):
    def read(self, mode, timeout=20, argv=(sys.executable,)):
        log = os.path.join(self.home, "seen-home")
        self.pid_file = os.path.join(self.home, "server-pid")
        env = dict(os.environ, FAKE_CODEX_MODE=mode, FAKE_CODEX_LOG=log, CODEX_HOME="/wrong",
                   FAKE_CODEX_PID=self.pid_file)
        reader = CodexReader(Cli(tuple(argv) + (FAKE,), env), timeout)
        account = CodexAccount(os.path.join(self.home, ".codex-work"))
        usage = reader.read(account)
        with open(log) as f:
            self.seen = f.read()
        return usage

    def test_reads_limits_skipping_noise_and_passes_the_account_home(self):
        usage = self.read("ok")
        self.assertEqual([(w.kind, w.pct) for w in usage.windows], [(FIVE_HOUR, 78.0), (WEEKLY, 73.0)])
        self.assertEqual((usage.plan, usage.email), ("Plus", "x@y.com"))
        self.assertEqual(self.seen, os.path.join(self.home, ".codex-work"))

    def test_extra_limit_comes_after_the_main_one(self):
        usage = self.read("other_limit")
        self.assertEqual([w.name for w in usage.windows], ["", "", "Spark"])

    def test_error_response_carries_its_message(self):
        with self.assertRaisesRegex(RuntimeError, "not signed in"):
            self.read("error")

    def test_process_ending_early_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, "ended early"):
            self.read("early_exit")

    def test_a_hung_server_times_out_and_is_killed(self):
        start = time.time()
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self.read("hang", timeout=0.5)
        self.assertLess(time.time() - start, 15)

    def test_a_hung_server_behind_a_forking_launcher_times_out_and_is_killed(self):
        # The old reader blocked on the forked server's stdout after killing only the launcher,
        # freezing the whole menu, and left the server running.
        start = time.time()
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self.read("hang", timeout=1, argv=FORKING_LAUNCHER)
        self.assertLess(time.time() - start, 10)
        if os.name != "nt":  # signal 0 only checks liveness on POSIX
            with open(self.pid_file) as f:
                pid = int(f.read())
            for _ in range(30):  # the orphan is reaped by init shortly after it dies
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.1)
            else:
                self.fail("the forked server is still running")


if __name__ == "__main__":
    unittest.main()
