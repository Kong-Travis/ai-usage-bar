"""Reading usage and profile data from the Anthropic API, with caching."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .domain import Account, ModelWindow, Usage
from .infra import HttpClient, HttpResponse, JsonCache

OAUTH_BETA = "oauth-2025-04-20"
RATE_PREFIX = "anthropic-ratelimit-unified-"
PROBE_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."


def parse_rate_headers(h: Mapping[str, str], model_windows: Mapping[str, str]) -> Usage:
    """Usage from the anthropic-ratelimit-unified-* headers (names in lower case)."""
    p = RATE_PREFIX
    models: List[ModelWindow] = []
    for name in h:
        m = re.match(re.escape(p) + r"7d_(\w+)-utilization$", name)
        if m:
            suffix = m.group(1)
            models.append(ModelWindow(
                name=model_windows.get(suffix, suffix),
                pct=float(h[name]) * 100,
                reset=int(h.get(p + "7d_" + suffix + "-reset") or 0),
            ))
    return Usage(
        five_hour=float(h[p + "5h-utilization"]) * 100,
        five_hour_reset=int(h[p + "5h-reset"]),
        weekly=float(h[p + "7d-utilization"]) * 100,
        weekly_reset=int(h[p + "7d-reset"]),
        models=tuple(models),
    )


class UsageProbe:
    """Reads usage from the rate-limit headers of a 1-token request.

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
            if r.headers.get(RATE_PREFIX + "5h-utilization") is not None:
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


class UsageService:
    """Usage of an account, probed at most every max_age seconds. On failure it falls back
    to the last good result and reports the error alongside."""

    def __init__(self, probe: UsageProbe, cache: JsonCache, max_age: float):
        self._probe = probe
        self._cache = cache
        self._max_age = max_age

    def usage(self, account: Account, force: bool = False) -> Tuple[Optional[Usage], Optional[str]]:
        key = account.cache_key
        cached = None if force else self._cache.fresh(key, self._max_age)
        if cached:
            return Usage.from_dict(cached), None
        try:
            usage = self._probe.probe(account.token())
        except Exception as e:
            last = self._cache.load(key)
            return (Usage.from_dict(last) if last else None), describe_error(e)
        self._cache.save(key, usage.to_dict())
        return usage, None


class IdentityService:
    """The text shown after an account's label: its email for a login, "token" otherwise."""

    def __init__(self, profile: ProfileClient, cache: JsonCache, max_age: float):
        self._profile = profile
        self._cache = cache
        self._max_age = max_age

    def detail(self, account: Account) -> str:
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
