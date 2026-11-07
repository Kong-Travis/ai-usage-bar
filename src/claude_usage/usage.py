"""Claude usage and profile data from the Anthropic API, and the provider-neutral caching
of every provider's usage."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .domain import (FIVE_HOUR, FIVE_HOUR_SECONDS, WEEK_SECONDS, WEEKLY, Account, ClaudeAccount,
                     Usage, UsageReader, Window)
from .infra import HttpClient, HttpResponse, JsonCache

OAUTH_BETA = "oauth-2025-04-20"
RATE_PREFIX = "anthropic-ratelimit-unified-"
PROBE_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."


def parse_rate_headers(h: Mapping[str, str], model_windows: Mapping[str, str]) -> Usage:
    """Usage from the anthropic-ratelimit-unified-* headers (names in lower case): the
    5-hour window, the weekly one, then each per-model weekly one."""
    p = RATE_PREFIX
    models: List[Window] = []
    for name in h:
        m = re.match(re.escape(p) + r"7d_(\w+)-utilization$", name)
        if m:
            suffix = m.group(1)
            models.append(Window(WEEKLY, float(h[name]) * 100,
                                 int(h.get(p + "7d_" + suffix + "-reset") or 0), WEEK_SECONDS,
                                 model_windows.get(suffix, suffix)))
    # A verified Ultra account has no 5-hour limit, so the API sends no 5h-* headers for it.
    five = h.get(p + "5h-utilization")
    return Usage((
        Window(FIVE_HOUR, None if five is None else float(five) * 100,
               int(h.get(p + "5h-reset") or 0), FIVE_HOUR_SECONDS),
        Window(WEEKLY, float(h.get(p + "7d-utilization") or 0) * 100,
               int(h.get(p + "7d-reset") or 0), WEEK_SECONDS),
    ) + tuple(models))


def has_usage(h: Mapping[str, str]) -> bool:
    """Whether a response reports any usage window, e.g. only 7d-* on a plan without a 5-hour
    limit."""
    return any(name.startswith(RATE_PREFIX) and name.endswith("-utilization") for name in h)


class UsageProbe:
    """The Claude UsageReader: reads usage from the rate-limit headers of a 1-token request.

    /api/oauth/usage is not used: setup-tokens lack the scope for it and it returns 429 for
    long stretches when polled. A Fable request also returns the per-model weekly windows
    (e.g. 7d_oi), which a Haiku one omits, so models are tried in order.
    """

    def __init__(self, http: HttpClient, api: str, models: Sequence[str],
                 model_windows: Mapping[str, str]):
        self._http = http
        self._api = api
        self._models = models
        self._model_windows = model_windows

    def read(self, account: Account) -> Usage:
        assert isinstance(account, ClaudeAccount)
        return self.probe(account.token())

    def probe(self, token: str) -> Usage:
        last = None
        for model in self._models:
            body = json.dumps({
                "model": model,
                "max_tokens": 1,
                "system": PROBE_SYSTEM,
                "messages": [{"role": "user", "content": "hi"}],
            }).encode()
            # A rate-limited account (429) still returns the usage headers.
            r = self._http.request(self._api + "/v1/messages", method="POST", body=body, timeout=20,
                                   headers={
                                       "Authorization": "Bearer " + token,
                                       "anthropic-beta": OAUTH_BETA,
                                       "anthropic-version": "2023-06-01",
                                       "content-type": "application/json",
                                   })
            if has_usage(r.headers):
                return parse_rate_headers(r.headers, self._model_windows)
            last = r
            if r.status == 401:
                break  # the token is rejected; another model will not change that
        raise RuntimeError(probe_failure(last))


def probe_failure(r: Optional[HttpResponse]) -> str:
    """Why a probe got no usage: the API's own error message when it sent one."""
    message = ""
    if r is not None and r.body:
        try:
            message = str(r.json()["error"]["message"])
        except (ValueError, KeyError, TypeError):
            pass
    if r is None or not message:
        return "no rate-limit headers"
    return "HTTP %d: %s" % (r.status, message)


class ProfileClient:
    def __init__(self, http: HttpClient, api: str):
        self._http = http
        self._api = api

    def email(self, token: str) -> str:
        r = self._http.request(self._api + "/api/oauth/profile", timeout=15, headers={
            "Authorization": "Bearer " + token,
            "anthropic-beta": OAUTH_BETA,
        })
        if r.status != 200:
            raise RuntimeError("profile HTTP %d" % r.status)
        return r.json()["account"]["email"]


def describe_error(e: Exception) -> str:
    return "%s: %s" % (type(e).__name__, str(e)[:160])


def cached_usage(d: object) -> Optional[Usage]:
    """A cache entry as Usage; None when missing or in a shape this version cannot read (one
    written by another version), so a bad entry costs a probe, not the whole menu."""
    if not d:
        return None
    try:
        return Usage.from_dict(d)  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


class UsageService:
    """Usage of an account, read by its provider's reader at most every max_age seconds. On
    failure it falls back to the last good result and reports the error alongside."""

    def __init__(self, readers: Mapping[str, UsageReader], cache: JsonCache, max_age: float):
        self._readers = readers
        self._cache = cache
        self._max_age = max_age

    def usage(self, account: Account, force: bool = False) -> Tuple[Optional[Usage], Optional[str]]:
        key = account.cache_key
        cached = None if force else cached_usage(self._cache.fresh(key, self._max_age))
        if cached:
            return cached, None
        try:
            usage = self._readers[account.provider].read(account)
        except Exception as e:
            return cached_usage(self._cache.load(key)), describe_error(e)
        self._cache.save(key, usage.to_dict())
        return usage, None


class IdentityService:
    """The text shown after a Claude account's label: its email for a login, "token" for a
    setup-token. Other providers report their email with their usage."""

    def __init__(self, profile: ProfileClient, cache: JsonCache, max_age: float):
        self._profile = profile
        self._cache = cache
        self._max_age = max_age

    def detail(self, account: Account) -> str:
        if not isinstance(account, ClaudeAccount):
            return ""
        if not account.is_login:
            return "token"
        name = account.cache_key + "-email"
        cached: Optional[Dict[str, str]] = self._cache.load(name)
        if cached and self._cache.age(name) < self._max_age:
            return cached.get("email", "")
        try:
            email = self._profile.email(account.token())
        except Exception:
            return (cached or {}).get("email", "")
        self._cache.save(name, {"email": email})
        return email
