"""Limit alerts: when a notification is shown, and the adapters that show it."""
import srcpath  # noqa: F401  (must come first)

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from claude_usage.domain import (CLAUDE, CODEX, FIVE_HOUR, FIVE_HOUR_SECONDS, WEEK_SECONDS, WEEKLY,
                                 AccountRow, Usage, Window)
from claude_usage.i18n import Translator
from claude_usage.infra import Cli, JsonCache
from claude_usage.notify import (NOTIFY_CACHE_NAME, CommandNotifier, LimitAlerts, NullNotifier,
                                 notify_send_args, notify_send_notifier, osascript_args)
from claude_usage.view import Presenter

T0 = 1_800_000_000  # a whole hour, 2027-01-15 08:00 UTC
HOUR = 3600


class Clock:
    def __init__(self, now=T0):
        self.t = now

    def now(self):
        return self.t


class Recorder:
    def __init__(self):
        self.shown = []

    def notify(self, title, body):
        self.shown.append((title, body))


def row(pct, reset=T0 + 2 * HOUR, error=None, label="claude", provider=CLAUDE, weekly=10.0,
        short="C"):
    usage = Usage((Window(FIVE_HOUR, pct, reset, FIVE_HOUR_SECONDS),
                   Window(WEEKLY, weekly, T0 + 3 * 86400, WEEK_SECONDS)))
    return AccountRow(short, label, "", usage, error, provider)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.clock = Clock()
        self.cache = JsonCache(self.dir, self.clock)
        self.notes = Recorder()

    def alerts(self, lang="en", notifier=None, cache=None):
        return LimitAlerts(notifier or self.notes, cache or self.cache, Translator(lang), 90,
                           time_left=lambda epoch: "in %dm" % ((epoch - self.clock.t) // 60),
                           window_name=lambda w: w.kind, clock=self.clock)

    def run_check(self, *rows, **kwargs):
        before = len(self.notes.shown)
        self.alerts(**kwargs).check(rows)  # a fresh object per run, like SwiftBar's process
        return self.notes.shown[before:]


class WhenToAlert(Base):
    def test_reaching_crit_alerts_once_then_stays_quiet(self):
        self.assertEqual(self.run_check(row(89)), [])
        self.assertEqual(self.run_check(row(92)),
                         [("Claude Code", "C claude, 5h: 92% used, resets in 120m")])
        self.assertEqual(self.run_check(row(95)), [])

    def test_used_up_alerts_once_more(self):
        self.run_check(row(92))
        self.assertEqual(self.run_check(row(100)),
                         [("Claude Code", "C claude, 5h: limit reached, resets in 120m")])
        self.assertEqual(self.run_check(row(100)), [])

    def test_first_seen_used_up_is_one_alert_not_two(self):
        self.assertEqual(len(self.run_check(row(100))), 1)
        self.assertEqual(self.run_check(row(100)), [])

    def test_the_next_period_alerts_again(self):
        self.run_check(row(95, reset=T0 + HOUR))
        self.clock.t = T0 + 2 * HOUR  # that window has reset
        self.assertEqual(self.run_check(row(91, reset=T0 + 6 * HOUR)),
                         [("Claude Code", "C claude, 5h: 91% used, resets in 240m")])

    def test_a_dip_below_crit_in_the_same_period_does_not_alert_again(self):
        self.assertEqual([len(self.run_check(row(p))) for p in (92, 89, 92)], [1, 0, 0])

    def test_reset_jitter_is_the_same_period(self):
        # A reset computed from "resets in N seconds" moves a little, even across hh:30.
        self.run_check(row(92, reset=T0 + 2 * HOUR + 1799))
        for reset in (T0 + 2 * HOUR + 1801, T0 + 2 * HOUR + 1850, T0 + 2 * HOUR + 1700):
            self.assertEqual(self.run_check(row(93, reset=reset)), [], reset)

    def test_a_reset_already_passed_is_old_data(self):
        self.clock.t = T0 + 3 * HOUR
        self.assertEqual(self.run_check(row(99, reset=T0 + 2 * HOUR)), [])

    def test_rows_with_an_error_are_skipped(self):
        # First run after install: cached numbers that failed to refresh must not alert.
        self.assertEqual(self.run_check(row(99, error="HTTP 500")), [])
        self.assertEqual(self.run_check(AccountRow("C", "claude", "", None, "HTTP 401")), [])

    def test_a_limit_the_plan_does_not_have_is_skipped(self):
        self.assertEqual(self.run_check(row(None)), [])

    def test_each_account_and_window_alerts_on_its_own(self):
        shown = self.run_check(row(92, weekly=95),
                               row(92, label="codex", provider=CODEX, short="Cx"))
        self.assertEqual([t for t, _ in shown], ["Claude Code", "Claude Code", "Codex"])
        self.assertEqual(shown[1][1], "C claude, 7d: 95% used, resets in 4320m")

    def test_unknown_reset_alerts_once_and_again_after_dropping_below(self):
        self.assertEqual(len(self.run_check(row(92, reset=0))), 1)
        self.assertEqual(self.run_check(row(92, reset=0)), [])
        self.assertEqual(self.run_check(row(5, reset=0)), [])
        self.assertEqual(self.run_check(row(92, reset=0)),
                         [("Claude Code", "C claude, 5h: 92% used")])  # no reset time known


class Memory(Base):
    def test_past_periods_are_pruned_from_the_cache(self):
        self.run_check(row(92, reset=T0 + HOUR))
        self.assertEqual(len(self.cache.load(NOTIFY_CACHE_NAME)), 1)
        self.clock.t = T0 + 3 * HOUR
        self.run_check(row(10, label="other"))
        self.assertEqual(self.cache.load(NOTIFY_CACHE_NAME), {})

    def test_a_damaged_cache_file_counts_as_empty(self):
        for junk in ("[1, 2]", '{"k": 3, "j": {"reset": "x", "at": 1}}', "not json"):
            with open(self.cache.path(NOTIFY_CACHE_NAME), "w") as f:
                f.write(junk)
            self.assertEqual(len(self.run_check(row(92))), 1, junk)
            os.remove(self.cache.path(NOTIFY_CACHE_NAME))

    def test_no_alert_when_it_cannot_be_remembered(self):
        # Otherwise every one-minute run would show it again.
        blocker = os.path.join(self.dir, "file")
        open(blocker, "w").close()
        unwritable = JsonCache(os.path.join(blocker, "sub"), self.clock)
        self.assertEqual(self.run_check(row(92), cache=unwritable), [])

    def test_a_failing_notifier_does_not_break_the_menu(self):
        class Broken:
            def notify(self, title, body):
                raise RuntimeError("boom")

        self.alerts(notifier=Broken()).check([row(92)])

    def test_vietnamese_text_with_the_real_presenter(self):
        presenter = Presenter(self.clock, Translator("vi"), "1.3.5", "https://x.test", 70, 90, 3)
        alerts = LimitAlerts(self.notes, self.cache, Translator("vi"), 90, presenter.time_left,
                             presenter.window_name, self.clock)
        alerts.check([row(92, reset=T0 + HOUR + 600), row(100, label="codex", provider=CODEX,
                                                              short="Cx", reset=0)])
        self.assertEqual(self.notes.shown, [
            ("Claude Code", "C claude, 5 giờ: đã dùng 92%, reset sau 1 giờ 10 phút"),
            ("Codex", "Cx codex, 5 giờ: đã hết hạn mức"),
        ])


class Adapters(unittest.TestCase):
    TRICKY = ('"); do shell script "touch /tmp/pwned"; ("', "-n \\ end run\n$(id)")

    def test_under_swiftbar_the_plugin_notifies_through_swiftbar_with_an_encoded_url(self):
        import urllib.parse
        from claude_usage.notify import macos_notifier, swiftbar_notify_args
        args = swiftbar_notify_args("claude-usage")("Codex", "Cx & co: 92% #1 / b?")
        self.assertEqual(args[0], "-g")
        url = urllib.parse.urlsplit(args[1])
        self.assertEqual((url.scheme, url.netloc), ("swiftbar", "notify"))
        self.assertEqual(urllib.parse.parse_qs(url.query),
                         {"plugin": ["claude-usage"], "title": ["Codex"],
                          "body": ["Cx & co: 92% #1 / b?"]})
        # The plugin is addressed by its file name up to the first dot.
        n = macos_notifier({"SWIFTBAR_PLUGIN_PATH": "/x/plugins/claude-usage.1m.py"})
        self.assertEqual(n._cli.argv, ("/usr/bin/open",))
        self.assertEqual(n._args("t", "b")[1].split("&")[0], "swiftbar://notify?plugin=claude-usage")
        self.assertEqual(macos_notifier({})._cli.argv, ("/usr/bin/osascript",))

    def test_osascript_gets_the_text_as_arguments_not_as_script(self):
        title, body = self.TRICKY
        args = osascript_args(title, body)
        self.assertEqual(args[-3:], ["--", title, body])
        script = [a for flag, a in zip(args, args[1:]) if flag == "-e"]
        self.assertEqual(script, ["on run argv",
                                  "display notification (item 2 of argv) with title (item 1 of argv)",
                                  "end run"])

    def test_notify_send_ends_options_before_the_text(self):
        self.assertEqual(notify_send_args("-u", "--help"), ["--", "-u", "--help"])

    def test_command_notifier_passes_the_text_through_unchanged(self):
        out = os.path.join(tempfile.mkdtemp(), "argv.json")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        script = "import json, sys; json.dump(sys.argv[1:], open(%r, 'w'))" % out
        cli = Cli((sys.executable, "-c", script), dict(os.environ))
        CommandNotifier(cli, osascript_args).notify(*self.TRICKY)
        with open(out) as f:
            self.assertEqual(json.load(f), osascript_args(*self.TRICKY))

    def test_command_notifier_never_raises(self):
        missing = Cli((os.path.join(tempfile.gettempdir(), "no-such-notifier"),), {})
        CommandNotifier(missing, notify_send_args).notify("t", "b")
        slow = Cli((sys.executable, "-c", "import time; time.sleep(5)"), dict(os.environ))
        CommandNotifier(slow, notify_send_args, timeout=0.2).notify("t", "b")

    def test_no_notify_send_means_no_notifications(self):
        class Nothing:
            def find(self, name):
                return None

        self.assertIsInstance(notify_send_notifier(Nothing()), NullNotifier)

    @unittest.skipUnless(sys.platform == "darwin", "AppleScript is macOS only")
    def test_the_applescript_compiles(self):
        out = os.path.join(tempfile.mkdtemp(), "n.scpt")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        args = osascript_args("t", "b")
        script = [a for flag, a in zip(args, args[1:]) if flag == "-e"]
        cmd = ["/usr/bin/osacompile", "-o", out]
        for line in script:
            cmd += ["-e", line]
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
