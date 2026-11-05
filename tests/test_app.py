"""Use cases: parallel probing, rows, menu, run log."""
import srcpath  # noqa: F401  (must come first)

import os
import shutil
import tempfile
import threading
import time
import unittest

from claude_usage.app import RunLog, UsageApp, map_parallel
from claude_usage.domain import AccountRow, TokenAccount, Usage

NOW = 1_700_000_000


class FakeClock:
    def now(self):
        return float(NOW)


class MapParallel(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertEqual(map_parallel(str, []), [])
        self.assertEqual(map_parallel(lambda x: x * 2, [4]), [8])

    def test_keeps_input_order_even_when_later_items_finish_first(self):
        def work(i):
            time.sleep((5 - i) * 0.04)
            return i

        self.assertEqual(map_parallel(work, range(5)), [0, 1, 2, 3, 4])

    def test_actually_runs_concurrently(self):
        def work(i):
            time.sleep(0.3)
            return i * i

        start = time.monotonic()
        got = map_parallel(work, range(5))
        elapsed = time.monotonic() - start
        self.assertEqual(got, [0, 1, 4, 9, 16])
        self.assertLess(elapsed, 1.0)  # sequential would take 1.5 s

    def test_uses_several_threads(self):
        ids = set()
        barrier = threading.Barrier(3, timeout=5)

        def work(i):
            ids.add(threading.get_ident())
            barrier.wait()  # only passes when three calls are in flight together
            return i

        self.assertEqual(map_parallel(work, [1, 2, 3]), [1, 2, 3])
        self.assertEqual(len(ids), 3)

    def test_an_exception_propagates(self):
        def work(i):
            if i == 2:
                raise ValueError("boom")
            return i

        with self.assertRaises(ValueError):
            map_parallel(work, [1, 2, 3])


class FakeRegistry:
    def __init__(self, accounts):
        self._accounts = accounts

    def accounts(self):
        return list(self._accounts)


class FakeUsageService:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []
        self._lock = threading.Lock()

    def usage(self, account, force=False):
        with self._lock:
            self.calls.append((account.label, force))
        return self.results.get(account.label, (None, "no data"))


class FakeIdentity:
    def detail(self, account):
        return "token" if not account.is_login else "login@x"


class FakeReleases:
    def __init__(self, tag):
        self.tag = tag

    def latest(self, fresh=False):
        self.fresh = fresh
        return self.tag


class FakePresenter:
    def __init__(self):
        self.calls = []

    def present(self, rows, tag, up_to_date=False):
        self.calls.append((list(rows), tag, up_to_date))
        return "MENU"


USAGE = Usage(10.0, NOW + 60, 20.0, NOW + 120)


class UsageAppTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cub-app-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def app(self, accounts=None, tag=None, results=None, version="1.2.2"):
        accounts = accounts if accounts is not None else [
            TokenAccount("claude-work", "sk-ant-oat01-W"),
            TokenAccount("claude", "sk-ant-oat01-C"),
            TokenAccount("claude-ci", "sk-ant-oat01-I"),
        ]
        self.usage = FakeUsageService(results if results is not None else {
            "claude-work": (USAGE, None), "claude": (None, "Boom: x")})
        self.presenter = FakePresenter()
        self.run_log = RunLog(os.path.join(self.dir, "cache"), FakeClock(), version)
        self.releases = FakeReleases(tag)
        return UsageApp(FakeRegistry(accounts), self.usage, FakeIdentity(), self.releases,
                        self.presenter, self.run_log, version, mapper=map_parallel)

    def test_rows_keep_account_order_with_short_names_and_details(self):
        rows = self.app().rows()
        self.assertEqual([(r.short, r.label, r.detail) for r in rows],
                         [("W", "claude-work", "token"), ("C", "claude", "token"),
                          ("C2", "claude-ci", "token")])
        self.assertEqual(rows[0].usage, USAGE)
        self.assertIsNone(rows[0].error)
        self.assertEqual(rows[1].error, "Boom: x")
        self.assertEqual(rows[2].error, "no data")

    def test_rows_do_not_force_by_default(self):
        app = self.app()
        app.rows()
        self.assertEqual({f for _, f in self.usage.calls}, {False})
        self.assertEqual(len(self.usage.calls), 3)

    def test_rows_with_force(self):
        app = self.app()
        app.rows(force=True)
        self.assertEqual({f for _, f in self.usage.calls}, {True})

    def test_refresh_forces_every_account(self):
        app = self.app()
        app.refresh()
        self.assertEqual(sorted(self.usage.calls),
                         [("claude", True), ("claude-ci", True), ("claude-work", True)])

    def test_menu_offers_a_newer_tag(self):
        app = self.app(tag="v1.3.0")
        self.assertEqual(app.menu(), "MENU")
        rows, tag, up_to_date = self.presenter.calls[0]
        self.assertEqual((tag, up_to_date), ("v1.3.0", False))
        self.assertEqual(len(rows), 3)

    def test_menu_does_not_offer_the_same_or_an_older_tag(self):
        for latest in ("v1.2.2", "v1.0.0", None):
            with self.subTest(latest=latest):
                app = self.app(tag=latest)
                app.menu()
                _, tag, up_to_date = self.presenter.calls[0]
                self.assertIsNone(tag)
                self.assertEqual(up_to_date, latest is not None)  # None: GitHub never answered

    def test_menu_force_reaches_the_usage_service(self):
        app = self.app()
        app.menu(force=True)
        self.assertEqual({f for _, f in self.usage.calls}, {True})

    def test_newer_release(self):
        self.assertEqual(self.app(tag="v2.0.0").newer_release(), "v2.0.0")
        self.assertIsNone(self.app(tag="v1.2.2").newer_release())
        self.assertIsNone(self.app(tag=None).newer_release())
        app = self.app(tag="v2.0.0")
        app.newer_release(fresh=True)
        self.assertTrue(self.releases.fresh)  # Check for updates skips the hourly cache

    def test_menu_writes_the_run_log_only_when_there_are_rows(self):
        app = self.app()
        app.menu()
        with open(self.run_log.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertIn("W claude-work: ok", lines)
        self.assertIn("C claude: Boom: x", lines)

        empty = self.app(accounts=[])
        os.remove(self.run_log.path)
        empty.menu()
        self.assertFalse(os.path.exists(self.run_log.path))
        self.assertEqual(self.presenter.calls[0][0], [])

    def test_no_accounts_means_no_probe(self):
        app = self.app(accounts=[])
        app.refresh()
        self.assertEqual(self.usage.calls, [])


class RunLogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cub-log-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_writes_header_and_one_line_per_row(self):
        log = RunLog(os.path.join(self.dir, "a", "b"), FakeClock(), "9.9.9")
        log.write([AccountRow("C", "claude", "", USAGE, None),
                   AccountRow("T", "claude-t", "token", None, "Err: bad")])
        self.assertEqual(log.path, os.path.join(self.dir, "a", "b", "last-run.log"))
        with open(log.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d v9\.9\.9 python=")
        self.assertEqual(lines[1:], ["C claude: ok", "T claude-t: Err: bad"])

    def test_the_log_is_replaced_not_appended(self):
        log = RunLog(self.dir, FakeClock(), "1.0.0")
        log.write([AccountRow("C", "claude", "", USAGE, None)])
        log.write([AccountRow("T", "t", "", USAGE, None)])
        with open(log.path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("claude", text)
        self.assertEqual(len(text.splitlines()), 2)

    def test_an_unwritable_location_is_ignored(self):
        blocker = os.path.join(self.dir, "file")
        with open(blocker, "w") as f:
            f.write("x")
        log = RunLog(os.path.join(blocker, "sub"), FakeClock(), "1.0.0")
        log.write([AccountRow("C", "claude", "", USAGE, None)])  # must not raise


if __name__ == "__main__":
    unittest.main()
