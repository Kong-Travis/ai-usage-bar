"""The shipped plugin file: single, self-contained, and installable by older releases."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from fakes import BUNDLE, ROOT, FakeHome, FakeServer, base_env, run_plugin

OLD_RELEASE = "v1.2.2"

# Runs an old release's `--update` with urlopen faked: GitHub says the latest tag is v9.9.9 and
# the raw file at that tag is the new bundle (with its version set to 9.9.9).
OLD_UPDATE_DRIVER = r'''
import io, json, sys, urllib.request
old_path, new_code = sys.argv[1], open(sys.argv[2], encoding="utf-8").read()

class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

def fake_urlopen(req, timeout=None):
    url = req if isinstance(req, str) else req.full_url
    if url.endswith("/releases/latest"):
        return Resp(json.dumps({"tag_name": "v9.9.9"}).encode())
    if url.startswith("https://raw.githubusercontent.com/") and "/v9.9.9/" in url:
        return Resp(new_code.encode("utf-8"))
    raise AssertionError("unexpected URL " + url)

urllib.request.urlopen = fake_urlopen
sys.argv = [old_path, "--update"]
code = open(old_path, encoding="utf-8").read()
exec(compile(code, old_path, "exec"), {"__name__": "__main__", "__file__": old_path})
'''


class BundleShape(unittest.TestCase):
    def setUp(self):
        with open(BUNDLE, encoding="utf-8") as f:
            self.code = f.read()

    def test_conditions_old_self_update_checks(self):
        self.assertTrue(self.code.startswith("#!/usr/bin/python3"))
        self.assertRegex(self.code, r'(?m)^VERSION = "\d+\.\d+\.\d+"$')
        compile(self.code, BUNDLE, "exec")

    def test_no_relative_imports_left(self):
        self.assertNotRegex(self.code, r"(?m)^\s*from \.")

    def test_bundle_is_up_to_date_with_src(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "bundle.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_runs_alone_in_an_empty_folder(self):
        tmp = tempfile.mkdtemp()
        home = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "claude-usage.1m.py")
            shutil.copy(BUNDLE, path)
            os.makedirs(os.path.join(home, ".config"))
            with open(os.path.join(home, ".config", "claude-usage.conf"), "w") as f:
                f.write("keychain = off\n")
            env = base_env(home)
            env["CLAUDE_USAGE_GITHUB"] = "http://127.0.0.1:9"
            out = subprocess.run([sys.executable, "-I", path], cwd=tmp, env=env,
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertTrue(out.stdout.startswith("AI ? |"), out.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)


class OldReleaseCanUpdate(unittest.TestCase):
    def test_v122_self_update_installs_the_new_bundle(self):
        try:
            old = subprocess.run(["git", "show", "%s:plugin/claude-usage.1m.py" % OLD_RELEASE],
                                 cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                 check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git tag %s not available" % OLD_RELEASE)
        with open(BUNDLE, encoding="utf-8") as f:
            new = re.sub(r'(?m)^VERSION = "[^"]+"$', 'VERSION = "9.9.9"', f.read(), count=1)
        tmp = tempfile.mkdtemp()
        try:
            old_path = os.path.join(tmp, "claude-usage.1m.py")
            new_path = os.path.join(tmp, "new.txt")
            driver = os.path.join(tmp, "driver.py")
            for path, text in ((old_path, old), (new_path, new), (driver, OLD_UPDATE_DRIVER)):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            env = base_env(tmp)
            env["PYTHONUTF8"] = "1"  # v1.2.2 wrote with the locale encoding; it only ever ran on macOS
            out = subprocess.run([sys.executable, driver, old_path, new_path], env=env,
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            with open(old_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), new)
            # The updated file must then run on its own.
            os.makedirs(os.path.join(tmp, ".config"), exist_ok=True)
            with open(os.path.join(tmp, ".config", "claude-usage.conf"), "w") as f:
                f.write("keychain = off\n")
            env["CLAUDE_USAGE_GITHUB"] = "http://127.0.0.1:9"
            run = subprocess.run([sys.executable, "-I", old_path], env=env, cwd=tmp,
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("AI ?", run.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class UpdateNow(unittest.TestCase):
    """Bug fixed after v1.3.3: `--update` trusted the 6-hour release cache, so right after a
    release it found nothing newer and silently left the old plugin in place."""

    def test_update_asks_github_even_when_the_release_cache_is_fresh(self):
        with open(BUNDLE, encoding="utf-8") as f:
            new = re.sub(r'(?m)^VERSION = "[^"]+"$', 'VERSION = "9.9.9"', f.read(), count=1)
        with FakeHome() as home:
            installed = home.write("swiftbar/claude-usage.1m.py", "")
            shutil.copyfile(BUNDLE, installed)
            home.write(".cache/claude-usage/update.json", '{"tag": "v0.0.1"}')  # checked just now
            with FakeServer(latest_tag="v9.9.9", plugin_code=new) as server:
                run_plugin(home, server, "--update", plugin=installed,
                           extra_env={"CLAUDE_USAGE_GITHUB_RAW": server.url})
            with open(installed, encoding="utf-8") as f:
                self.assertEqual(f.read(), new)
            with open(os.path.join(home.path, ".cache", "claude-usage", "update.json")) as f:
                self.assertIn("v9.9.9", f.read())  # the menu stops offering the update


if __name__ == "__main__":
    unittest.main()
