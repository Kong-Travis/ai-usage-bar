"""Reading usage and identity from the API, with caching and fallbacks."""
import srcpath  # noqa: F401  (must come first)

import json
import shutil
import tempfile
import time
import unittest

from claude_usage.config import MODEL_WINDOWS, PROBE_MODELS
from claude_usage.domain import CLAUDE, Account, ClaudeAccount, TokenAccount, Usage
from fakes import claude_usage
from claude_usage.infra import HttpResponse, JsonCache
from claude_usage.usage import (OAUTH_BETA, RATE_PREFIX, IdentityService, ProfileClient,
                                UsageProbe, UsageService, describe_error, parse_rate_headers)

P = RATE_PREFIX


class FakeClock:
    def __init__(self):
        self.t = time.time()

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def headers(five=0.13, seven=0.08, models=None, five_reset=1000, seven_reset=2000):
    h = {P + "5h-utilization": str(five), P + "5h-reset": str(five_reset),
         P + "7d-utilization": str(seven), P + "7d-reset": str(seven_reset)}
    for suffix, (util, reset) in (models or {}).items():
        h[P + "7d_%s-utilization" % suffix] = str(util)
        if reset is not None:
            h[P + "7d_%s-reset" % suffix] = str(reset)
    return h


class FakeHttp:
    """Answers from a queue of HttpResponse (or exceptions) and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, url, method="GET", headers=None, body=None, timeout=15):
        self.requests.append({"url": url, "method": method, "headers": headers or {},
                              "body": json.loads(body) if body else None, "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def five(u):
    return u.windows[0]


def weekly(u):
    return u.windows[1]


def models(u):
    return u.windows[2:]


class ParseRateHeaders(unittest.TestCase):
    def test_plain_windows(self):
        u = parse_rate_headers(headers(), MODEL_WINDOWS)
        self.assertEqual(u, claude_usage(13.0, 1000, 8.0, 2000))

    def test_per_model_window_uses_the_configured_name(self):
        u = parse_rate_headers(headers(models={"oi": (0.01, 3000)}), MODEL_WINDOWS)
        self.assertEqual(u, claude_usage(13.0, 1000, 8.0, 2000, [("Fable", 1.0, 3000)]))
        self.assertEqual(MODEL_WINDOWS["oi"], "Fable")

    def test_unknown_model_suffix_keeps_the_suffix(self):
        u = parse_rate_headers(headers(models={"xy": (0.5, 10)}), MODEL_WINDOWS)
        self.assertEqual([m.name for m in models(u)], ["xy"])
        self.assertAlmostEqual(models(u)[0].pct, 50.0)

    def test_model_without_reset_header_has_reset_zero(self):
        u = parse_rate_headers(headers(models={"oi": (0.2, None)}), MODEL_WINDOWS)
        self.assertEqual(models(u)[0].reset, 0)

    def test_custom_naming_and_percentages_order(self):
        u = parse_rate_headers(headers(five=0.1, seven=0.2, models={"a": (0.3, 1), "b": (0.4, 2)}),
                               {"a": "Alpha"})
        self.assertEqual(sorted(m.name for m in models(u)), ["Alpha", "b"])
        got = u.percentages()
        self.assertEqual(len(got), 4)
        self.assertAlmostEqual(got[0], 10.0)
        self.assertAlmostEqual(got[1], 20.0)

    def test_the_weekly_header_is_not_mistaken_for_a_model(self):
        u = parse_rate_headers(headers(), {})
        self.assertEqual(models(u), ())

    def test_no_five_hour_headers_means_no_five_hour_limit_and_survives_the_cache(self):
        h = headers(seven=0.58)
        del h[P + "5h-utilization"], h[P + "5h-reset"]
        u = parse_rate_headers(h, {})
        self.assertEqual((five(u).pct, five(u).reset), (None, 0))
        self.assertAlmostEqual(weekly(u).pct, 58.0)
        self.assertIsNone(u.percentages()[0])
        self.assertEqual(Usage.from_dict(json.loads(json.dumps(u.to_dict()))), u)


class UsageProbeTest(unittest.TestCase):
    API = "http://127.0.0.1:9"

    def probe(self, *responses):
        http = FakeHttp(*responses)
        return UsageProbe(http, self.API, PROBE_MODELS, MODEL_WINDOWS), http

    def test_first_model_with_headers_wins_and_request_is_well_formed(self):
        probe, http = self.probe(HttpResponse(200, headers(models={"oi": (0.01, 5)})))
        u = probe.probe("TOK")
        self.assertEqual([m.name for m in models(u)], ["Fable"])
        self.assertEqual(len(http.requests), 1)
        r = http.requests[0]
        self.assertEqual(r["url"], self.API + "/v1/messages")
        self.assertEqual(r["method"], "POST")
        self.assertEqual(r["headers"]["Authorization"], "Bearer TOK")
        self.assertEqual(r["headers"]["anthropic-beta"], OAUTH_BETA)
        self.assertEqual(r["body"]["model"], PROBE_MODELS[0])
        self.assertEqual(r["body"]["max_tokens"], 1)

    def test_falls_back_to_the_next_model_when_there_are_no_headers(self):
        probe, http = self.probe(HttpResponse(400), HttpResponse(200, headers()))
        u = probe.probe("TOK")
        self.assertAlmostEqual(five(u).pct, 13.0)
        self.assertEqual([r["body"]["model"] for r in http.requests], list(PROBE_MODELS))

    def test_rate_limited_response_with_headers_is_accepted(self):
        probe, http = self.probe(HttpResponse(429, headers(five=0.95)))
        self.assertAlmostEqual(five(probe.probe("TOK")).pct, 95.0)
        self.assertEqual(len(http.requests), 1)

    def test_no_headers_anywhere_raises(self):
        probe, http = self.probe(HttpResponse(500), HttpResponse(401))
        with self.assertRaises(RuntimeError) as cm:
            probe.probe("TOK")
        self.assertEqual(str(cm.exception), "no rate-limit headers")
        self.assertEqual(len(http.requests), len(PROBE_MODELS))

    def test_the_last_error_message_is_reported_when_every_model_fails(self):
        def err(msg):
            return json.dumps({"error": {"message": msg}}).encode()
        probe, _ = self.probe(HttpResponse(400, body=err("first")),
                              HttpResponse(529, body=err("Overloaded")))
        with self.assertRaises(RuntimeError) as cm:
            probe.probe("TOK")
        self.assertEqual(str(cm.exception), "HTTP 529: Overloaded")

    def test_an_unreadable_error_body_falls_back_to_the_plain_message(self):
        for body in (b"<html>bad gateway</html>", b"\xff\xfe", b'{"error": "flat"}', b"[]"):
            with self.subTest(body=body):
                probe, _ = self.probe(HttpResponse(502, body=body), HttpResponse(502, body=body))
                with self.assertRaises(RuntimeError) as cm:
                    probe.probe("TOK")
                self.assertEqual(str(cm.exception), "no rate-limit headers")

    def test_network_error_propagates(self):
        probe, _ = self.probe(OSError("down"))
        with self.assertRaises(OSError):
            probe.probe("TOK")


class ProfileClientTest(unittest.TestCase):
    def test_email(self):
        body = json.dumps({"account": {"email": "a@b.c"}}).encode()
        http = FakeHttp(HttpResponse(200, {}, body))
        self.assertEqual(ProfileClient(http, "http://x").email("TOK"), "a@b.c")
        r = http.requests[0]
        self.assertEqual(r["url"], "http://x/api/oauth/profile")
        self.assertEqual(r["method"], "GET")
        self.assertEqual(r["headers"]["Authorization"], "Bearer TOK")
        self.assertEqual(r["headers"]["anthropic-beta"], OAUTH_BETA)

    def test_non_200_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            ProfileClient(FakeHttp(HttpResponse(401)), "http://x").email("TOK")
        self.assertIn("401", str(cm.exception))


class CacheCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cub-usage-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.clock = FakeClock()
        self.cache = JsonCache(self.dir, self.clock)


class FakeProbe:
    def __init__(self, *results):
        self.results = list(results)
        self.tokens = []

    def read(self, account):
        return self.probe(account.token())

    def probe(self, token):
        self.tokens.append(token)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


USAGE_A = claude_usage(10.0, 111, 20.0, 222, [("Fable", 5.0, 333)])
USAGE_B = claude_usage(30.0, 444, 40.0, 555)


class UsageServiceTest(CacheCase):
    def setUp(self):
        super().setUp()
        self.account = TokenAccount("claude", "sk-ant-oat01-A")

    def service(self, *results):
        self.probe = FakeProbe(*results)
        return UsageService({CLAUDE: self.probe}, self.cache, 180)

    def test_first_call_probes_and_caches(self):
        svc = self.service(USAGE_A)
        self.assertEqual(svc.usage(self.account), (USAGE_A, None))
        self.assertEqual(self.probe.tokens, ["sk-ant-oat01-A"])
        self.assertEqual(Usage.from_dict(self.cache.load(self.account.cache_key)), USAGE_A)

    def test_fresh_cache_hit_does_not_probe(self):
        svc = self.service(USAGE_A)
        svc.usage(self.account)
        self.clock.advance(100)
        self.assertEqual(svc.usage(self.account), (USAGE_A, None))
        self.assertEqual(len(self.probe.tokens), 1)

    def test_force_bypasses_a_fresh_cache(self):
        svc = self.service(USAGE_A, USAGE_B)
        svc.usage(self.account)
        self.assertEqual(svc.usage(self.account, force=True), (USAGE_B, None))
        self.assertEqual(len(self.probe.tokens), 2)

    def test_stale_cache_probes_again(self):
        svc = self.service(USAGE_A, USAGE_B)
        svc.usage(self.account)
        self.clock.advance(200)
        self.assertEqual(svc.usage(self.account), (USAGE_B, None))

    def test_an_unreadable_cache_entry_is_a_miss_not_a_crash(self):
        # A cache written by another version (e.g. a newer "5h": null read by an older one) must
        # not take the whole menu down: a fresh entry re-probes, a fallback entry is dropped.
        for bad in ({"5h": "x"}, {"7d": 1}, []):
            with self.subTest(bad=bad):
                self.cache.save(self.account.cache_key, bad)
                self.assertEqual(self.service(USAGE_A).usage(self.account), (USAGE_A, None))
                self.cache.save(self.account.cache_key, bad)
                self.assertEqual(self.service(ValueError("boom")).usage(self.account),
                                 (None, "ValueError: boom"))

    def test_failure_falls_back_to_the_last_cache_with_error_text(self):
        svc = self.service(USAGE_A, ValueError("boom"))
        svc.usage(self.account)
        self.clock.advance(200)
        self.assertEqual(svc.usage(self.account), (USAGE_A, "ValueError: boom"))

    def test_failure_with_force_also_falls_back(self):
        svc = self.service(USAGE_A, RuntimeError("no rate-limit headers"))
        svc.usage(self.account)
        self.assertEqual(svc.usage(self.account, force=True),
                         (USAGE_A, "RuntimeError: no rate-limit headers"))

    def test_failure_without_cache(self):
        svc = self.service(RuntimeError("no rate-limit headers"))
        self.assertEqual(svc.usage(self.account), (None, "RuntimeError: no rate-limit headers"))

    def test_token_failure_is_reported_not_raised(self):
        class Locked(Account):
            identity = "locked"

            def token(self):
                raise PermissionError("keychain locked")

        svc = self.service()
        self.assertEqual(svc.usage(Locked("x")), (None, "PermissionError: keychain locked"))
        self.assertEqual(self.probe.tokens, [])

    def test_accounts_are_cached_separately(self):
        other = TokenAccount("b", "sk-ant-oat01-B")
        svc = self.service(USAGE_A, USAGE_B)
        self.assertEqual(svc.usage(self.account)[0], USAGE_A)
        self.assertEqual(svc.usage(other)[0], USAGE_B)

    def test_error_text_is_truncated(self):
        self.assertEqual(describe_error(ValueError("x" * 500)), "ValueError: " + "x" * 160)


class FakeLogin(ClaudeAccount):
    is_login = True
    identity = "login-1"

    def token(self):
        return "LOGIN-TOKEN"


class FakeProfile:
    def __init__(self, *results):
        self.results = list(results)
        self.tokens = []

    def email(self, token):
        self.tokens.append(token)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class IdentityServiceTest(CacheCase):
    def service(self, *results):
        self.profile = FakeProfile(*results)
        return IdentityService(self.profile, self.cache, 86400)

    def test_token_account_is_just_token(self):
        svc = self.service()
        self.assertEqual(svc.detail(TokenAccount("x", "sk-ant-oat01-T")), "token")
        self.assertEqual(self.profile.tokens, [])

    def test_login_email_is_cached_for_max_age(self):
        svc = self.service("a@b.c", "new@b.c")
        acc = FakeLogin("claude")
        self.assertEqual(svc.detail(acc), "a@b.c")
        self.assertEqual(self.profile.tokens, ["LOGIN-TOKEN"])
        self.clock.advance(86400 - 60)
        self.assertEqual(svc.detail(acc), "a@b.c")
        self.assertEqual(len(self.profile.tokens), 1)
        self.clock.advance(120)
        self.assertEqual(svc.detail(acc), "new@b.c")
        self.assertEqual(len(self.profile.tokens), 2)

    def test_failure_returns_the_stale_email(self):
        svc = self.service("a@b.c", RuntimeError("profile HTTP 500"))
        acc = FakeLogin("claude")
        svc.detail(acc)
        self.clock.advance(90000)
        self.assertEqual(svc.detail(acc), "a@b.c")

    def test_failure_without_cache_is_empty(self):
        svc = self.service(RuntimeError("profile HTTP 500"))
        self.assertEqual(svc.detail(FakeLogin("claude")), "")


if __name__ == "__main__":
    unittest.main()
