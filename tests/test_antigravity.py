"""Antigravity (agy) discovery and limits, driven through a fake agy executable."""
import srcpath  # noqa: F401  (must come first)

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from claude_usage.antigravity import AgyAccount, AgyReader, AgySource, _agy_epoch
from claude_usage.domain import AGY, FIVE_HOUR, WEEKLY, WEEK_SECONDS
from claude_usage.infra import Cli

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_agy.py")



class AgyTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.args_file = os.path.join(self.dir, "args.json")
        self.log_path = os.path.join(self.dir, "logs", "agy.log")

    def read(self, mode, timeout=30, argv=(sys.executable,)):
        env = dict(os.environ, FAKE_AGY_MODE=mode, FAKE_AGY_ARGS=self.args_file)
        cli = Cli(tuple(argv) + (FAKE,), env)
        return AgyReader(cli, self.log_path, timeout).read(AgyAccount())


class TestAgySource(AgyTestCase):
    def test_account_needs_cli_and_state_folder(self):
        cli = Cli((sys.executable, FAKE), {})
        self.assertEqual([], AgySource(self.dir, cli).accounts())
        os.makedirs(os.path.join(self.dir, ".gemini", "antigravity-cli"))
        self.assertEqual([], AgySource(self.dir, None).accounts())
        (account,) = AgySource(self.dir, cli).accounts()
        self.assertEqual((AGY, "agy", "agy"), (account.provider, account.label, account.identity))


class TestAgyReader(AgyTestCase):
    def test_runs_usage_command_with_own_log_file(self):
        self.read("ok")
        with open(self.args_file, encoding="utf-8") as f:
            args = json.loads(f.read())
        self.assertEqual(["-p", "/usage", "--output-format", "json", "--log-file", self.log_path],
                         args)
        with open(self.log_path, encoding="utf-8") as f:  # parent dir was created
            self.assertTrue(f.read())

    def test_groups_in_order_with_short_names(self):
        usage = self.read("ok")
        self.assertEqual(("", ""), (usage.plan, usage.email))
        self.assertEqual([("Gemini", 0.0), ("Claude + GPT", 75.0)],
                         [(w.name, w.pct) for w in usage.windows])
        first = usage.windows[0]
        self.assertEqual((WEEKLY, WEEK_SECONDS, 1794635092), (first.kind, first.seconds, first.reset))

    def test_five_hour_first_clamping_and_unknown_windows(self):
        windows = self.read("five_hour").windows
        self.assertEqual([("Gemini", FIVE_HOUR, 50.0), ("Gemini", WEEKLY, 25.0),
                          ("Claude + GPT", "1d", 0.0), ("Claude + GPT", "monthly", 100.0)],
                         [(w.name, w.kind, w.pct) for w in windows])
        self.assertEqual((18000, 86400, 0), (windows[0].seconds, windows[2].seconds, windows[3].seconds))
        self.assertEqual(1794020400, windows[0].reset)  # 7-digit fraction and +07:00 offset

    def test_bucket_without_fraction_is_left_out(self):
        windows = self.read("missing_fraction").windows
        self.assertEqual([("Gemini", FIVE_HOUR, 10.0)], [(w.name, w.kind, w.pct) for w in windows])

    def test_error_status_reports_error_text_capped(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.read("error_status")
        message = str(ctx.exception)
        self.assertTrue(message.startswith("/credits failed: x"))
        self.assertEqual(160, len(message))

    def test_garbage_output_reports_exit_code(self):
        with self.assertRaisesRegex(RuntimeError, "code 3"):
            self.read("garbage")

    def test_slow_agy_times_out(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.read("slow", timeout=0.5)



class TestAgyEpoch(unittest.TestCase):
    def test_iso_variants(self):
        base = 1794635092
        for text in ("2026-11-14T05:44:52Z", "2026-11-14T05:44:52.123456789Z",
                     "2026-11-14T05:44:52", "2026-11-14T12:44:52+07:00",
                     "2026-11-14T00:44:52-0500", "2026-11-14 05:44:52z"):
            self.assertEqual(base, _agy_epoch(text), text)

    def test_unparsable_is_zero(self):
        for text in (None, "", "soon", "2026-13-40T99:00:00Z", 5):
            self.assertEqual(0, _agy_epoch(text), repr(text))


if __name__ == "__main__":
    unittest.main()
