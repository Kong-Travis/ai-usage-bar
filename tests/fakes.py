"""Test doubles: a fake Anthropic + GitHub HTTP server and a fake home directory."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join(ROOT, "plugin", "claude-usage.1m.py")
PREFIX = "anthropic-ratelimit-unified-"


def claude_usage(five, five_reset, weekly, weekly_reset, models=()):
    """A Claude Usage: 5-hour, weekly, then (name, pct, reset) per-model weekly windows."""
    import srcpath  # noqa: F401  (puts src/ on sys.path when a test module did not)
    from claude_usage.domain import (FIVE_HOUR, FIVE_HOUR_SECONDS, WEEK_SECONDS, WEEKLY, Usage,
                                     Window)
    return Usage((Window(FIVE_HOUR, five, five_reset, FIVE_HOUR_SECONDS),
                  Window(WEEKLY, weekly, weekly_reset, WEEK_SECONDS))
                 + tuple(Window(WEEKLY, pct, reset, WEEK_SECONDS, name) for name, pct, reset in models))


def tok(name: str) -> str:
    """A string that matches the plugin's setup-token pattern."""
    return "sk-ant-oat01-" + name


class Plan:
    """What the fake API answers for one token.

    windows: header suffix -> (utilization 0..1, reset offset in seconds from now),
    e.g. {"5h": (0.13, 8100), "7d": (0.08, 590400), "7d_oi": (0.01, 590400)}.
    error: when set, /v1/messages answers `status` with this API error message and no usage
    headers, the way the real API rejects an expired or revoked token.
    """

    def __init__(self, windows=None, status=200, fable=True, email=None, error=None):
        self.windows = windows or {}
        self.status = status
        self.fable = fable
        self.email = email
        self.error = error


class FakeServer:
    def __init__(self, plans: dict[str, Plan] | None = None, latest_tag: str = "v0.0.1",
                 plugin_code: str | None = None):
        self.plans = plans or {}
        self.latest_tag = latest_tag
        self.plugin_code = plugin_code  # served as the raw plugin file at latest_tag
        self.requests: list[tuple[str, str, str]] = []  # (method, path, model or "")
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _token(self):
                return self.headers.get("Authorization", "")[len("Bearer "):]

            def _send(self, status, body=b"{}", headers=None):
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                server.requests.append(("GET", self.path, ""))
                if self.path.endswith("/releases/latest"):
                    self._send(200, json.dumps({"tag_name": server.latest_tag}).encode())
                elif (server.plugin_code is not None
                      and self.path.endswith("/%s/plugin/claude-usage.1m.py" % server.latest_tag)):
                    self._send(200, server.plugin_code.encode("utf-8"))
                elif self.path == "/api/oauth/profile":
                    plan = server.plans.get(self._token())
                    if plan and plan.email:
                        self._send(200, json.dumps({"account": {"email": plan.email}}).encode())
                    else:
                        self._send(401)
                else:
                    self._send(404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                model = body.get("model", "")
                server.requests.append(("POST", self.path, model))
                plan = server.plans.get(self._token())
                if self.path != "/v1/messages" or plan is None:
                    self._send(401)
                    return
                if plan.error:
                    self._send(plan.status, json.dumps({"type": "error", "error": {
                        "type": "authentication_error", "message": plan.error}}).encode())
                    return
                if "fable" in model and not plan.fable:
                    self._send(400)
                    return
                now = int(time.time())
                headers = {}
                for suffix, (util, offset) in plan.windows.items():
                    if suffix.startswith("7d_") and "fable" not in model:
                        continue  # per-model windows only come back on a Fable request
                    headers[PREFIX + suffix + "-utilization"] = str(util)
                    headers[PREFIX + suffix + "-reset"] = str(now + offset)
                self._send(plan.status, b"{}", headers)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeHome:
    """A throwaway $HOME. Keychain lookups are turned off through the conf file."""

    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="cub-home-")

    def write(self, rel: str, text: str, mode: int | None = None) -> str:
        p = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        if mode is not None:
            os.chmod(p, mode)
        return p

    def append(self, rel: str, text: str) -> str:
        p = os.path.join(self.path, rel)
        with open(p, "a") as f:
            f.write(text)
        return p

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()


def base_env(home: str) -> dict:
    """A minimal environment for running the plugin, valid on every OS."""
    env = {"HOME": home, "USERPROFILE": home, "PATH": os.environ.get("PATH", ""),
           "PYTHONIOENCODING": "utf-8"}
    for name in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "WINDIR"):  # Windows needs these
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def run_plugin(home: FakeHome, server: FakeServer, *args: str, plugin: str = BUNDLE,
               extra_env: dict[str, str] | None = None) -> str:
    env = base_env(home.path)
    env.update({"CLAUDE_USAGE_API": server.url, "CLAUDE_USAGE_GITHUB": server.url})
    env.update(extra_env or {})
    out = subprocess.run([sys.executable, plugin] + list(args), env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    if out.returncode != 0:
        raise AssertionError("plugin failed (%d): %s" % (out.returncode, out.stderr))
    with open(plugin, encoding="utf-8") as f:
        version = re.search(r'^VERSION = "([^"]+)"', f.read(), re.M).group(1)
    return (out.stdout.replace(os.path.realpath(plugin), "<SELF>")
            .replace("v" + version, "v<VERSION>"))
