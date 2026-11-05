"""Release check and self-update."""
import srcpath  # noqa: F401  (must come first)

import json
import os
import shutil
import tempfile
import time
import unittest

from claude_usage.infra import HttpResponse, JsonCache
from claude_usage.updater import ReleaseChecker, SelfUpdater, is_newer, parse_version

RAW = "https://raw.example"
REPO = "o/r"
PATH_IN_REPO = "plugin/p.py"


class FakeClock:
    def __init__(self):
        self.t = time.time()

    def now(self):
        return self.t


class FakeHttp:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, url, method="GET", headers=None, body=None, timeout=15):
        self.requests.append({"url": url, "headers": headers or {}})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def release(tag):
    return HttpResponse(200, {}, json.dumps({"tag_name": tag}).encode())


class Versions(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version("1.2"), (1, 2))
        self.assertEqual(parse_version("v2.0.0-beta.4"), (2, 0, 0))
        self.assertEqual(parse_version("v1.10.0"), (1, 10, 0))
        self.assertEqual(parse_version("latest"), ())

    def test_is_newer(self):
        self.assertTrue(is_newer("v1.2.3", "1.2.2"))
        self.assertTrue(is_newer("v1.10.0", "1.9.9"))
        self.assertTrue(is_newer("v2.0.0", "1.99.99"))
        self.assertFalse(is_newer("v1.2.2", "1.2.2"))
        self.assertFalse(is_newer("v1.2.1", "1.2.2"))
        self.assertFalse(is_newer(None, "1.2.2"))
        self.assertFalse(is_newer("", "1.2.2"))
        self.assertFalse(is_newer("nightly", "1.2.2"))


class TempCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cub-upd-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.clock = FakeClock()
        self.cache = JsonCache(os.path.join(self.dir, "cache"), self.clock)


class ReleaseCheckerTest(TempCase):
    def checker(self, *responses):
        self.http = FakeHttp(*responses)
        return ReleaseChecker(self.http, "https://api.example", REPO, self.cache, 3600)

    def test_asks_github_then_serves_from_cache(self):
        c = self.checker(release("v1.3.0"))
        self.assertEqual(c.latest(), "v1.3.0")
        self.assertEqual(c.latest(), "v1.3.0")
        self.assertEqual(len(self.http.requests), 1)
        r = self.http.requests[0]
        self.assertEqual(r["url"], "https://api.example/repos/o/r/releases/latest")
        self.assertIn("github", r["headers"]["Accept"])

    def test_asks_again_after_max_age(self):
        c = self.checker(release("v1.3.0"), release("v1.4.0"))
        c.latest()
        self.clock.t += 4000
        self.assertEqual(c.latest(), "v1.4.0")

    def test_fresh_asks_github_within_max_age_and_falls_back_when_it_fails(self):
        c = self.checker(release("v1.3.0"), release("v1.4.0"), OSError("offline"))
        c.latest()
        self.assertEqual(c.latest(fresh=True), "v1.4.0")
        self.assertEqual(c.latest(fresh=True), "v1.4.0")
        self.assertEqual(len(self.http.requests), 3)

    def test_http_error_falls_back_to_the_cached_tag(self):
        c = self.checker(release("v1.3.0"), HttpResponse(403))
        c.latest()
        self.clock.t += 4000
        self.assertEqual(c.latest(), "v1.3.0")

    def test_network_error_falls_back_to_the_cached_tag(self):
        c = self.checker(release("v1.3.0"), OSError("offline"))
        c.latest()
        self.clock.t += 4000
        self.assertEqual(c.latest(), "v1.3.0")

    def test_malformed_body_falls_back(self):
        c = self.checker(release("v1.3.0"), HttpResponse(200, {}, b"not json"))
        c.latest()
        self.clock.t += 4000
        self.assertEqual(c.latest(), "v1.3.0")

    def test_error_without_cache_gives_none(self):
        self.assertIsNone(self.checker(OSError("offline")).latest())


class FakeChecker:
    def __init__(self, tag):
        self.tag = tag

    def latest(self, fresh=False):
        return self.tag


GOOD = '#!/usr/bin/python3\nVERSION = "1.3.0"\nprint("new")\n'


class SelfUpdaterTest(TempCase):
    def setUp(self):
        super().setUp()
        self.target = os.path.join(self.dir, "plugin.py")
        with open(self.target, "w") as f:
            f.write("old")

    def updater(self, tag, *responses, version="1.2.2"):
        self.http = FakeHttp(*responses)
        return SelfUpdater(self.http, FakeChecker(tag), RAW, REPO, PATH_IN_REPO, version, self.target)

    def read_target(self):
        with open(self.target) as f:
            return f.read()

    def assert_untouched(self):
        self.assertEqual(self.read_target(), "old")
        self.assertEqual(os.listdir(self.dir).count("plugin.py.new"), 0)

    def test_success_replaces_the_target(self):
        u = self.updater("v1.3.0", HttpResponse(200, {}, GOOD.encode()))
        self.assertTrue(u.update())
        self.assertEqual(self.read_target(), GOOD)
        self.assertEqual(self.http.requests[0]["url"], "https://raw.example/o/r/v1.3.0/plugin/p.py")
        self.assertNotIn("plugin.py.new", os.listdir(self.dir))

    @unittest.skipIf(os.name == "nt", "exec bits are not meaningful on Windows")
    def test_success_keeps_the_exec_bit(self):
        os.chmod(self.target, 0o644)
        self.updater("v1.3.0", HttpResponse(200, {}, GOOD.encode())).update()
        self.assertTrue(os.stat(self.target).st_mode & 0o100)

    def test_written_with_unix_newlines(self):
        self.updater("v1.3.0", HttpResponse(200, {}, GOOD.encode())).update()
        with open(self.target, "rb") as f:
            self.assertNotIn(b"\r", f.read())

    def test_not_newer_does_nothing(self):
        for tag in ("v1.2.2", "v1.0.0", None):
            with self.subTest(tag=tag):
                u = self.updater(tag, version="1.2.2")
                self.assertFalse(u.update())
                self.assertEqual(self.http.requests, [])
                self.assert_untouched()

    def test_download_error_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            self.updater("v1.3.0", HttpResponse(404)).update()
        self.assertIn("404", str(cm.exception))
        self.assert_untouched()

    def test_bad_shebang_is_rejected(self):
        code = GOOD.replace("#!/usr/bin/python3", "#!/bin/sh")
        with self.assertRaises(RuntimeError):
            self.updater("v1.3.0", HttpResponse(200, {}, code.encode())).update()
        self.assert_untouched()

    def test_wrong_version_is_rejected(self):
        code = GOOD.replace("1.3.0", "1.2.9")
        with self.assertRaises(RuntimeError):
            self.updater("v1.3.0", HttpResponse(200, {}, code.encode())).update()
        self.assert_untouched()

    def test_syntax_error_is_rejected(self):
        code = GOOD + "def (:\n"
        with self.assertRaises(SyntaxError):
            self.updater("v1.3.0", HttpResponse(200, {}, code.encode())).update()
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
