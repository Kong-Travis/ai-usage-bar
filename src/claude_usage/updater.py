"""Release check and in-place self-update of the single-file plugin."""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

from .infra import HttpClient, JsonCache

UPDATE_CACHE = "update"
SHEBANG = "#!/usr/bin/python3"


def parse_version(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def is_newer(tag: Optional[str], version: str) -> bool:
    return bool(tag) and parse_version(tag or "") > parse_version(version)


class ReleaseChecker:
    """The newest release tag, asking GitHub at most every max_age seconds."""

    def __init__(self, http: HttpClient, github_api: str, repo: str, cache: JsonCache,
                 max_age: float):
        self._http = http
        self._url = "%s/repos/%s/releases/latest" % (github_api, repo)
        self._cache = cache
        self._max_age = max_age

    def latest(self, fresh: bool = False) -> Optional[str]:
        """fresh: ask GitHub even within max_age; the cached tag stays the fallback."""
        cached = self._cache.load(UPDATE_CACHE)
        if not fresh and cached and self._cache.age(UPDATE_CACHE) < self._max_age:
            return cached.get("tag")
        try:
            r = self._http.request(self._url, timeout=10,
                                   headers={"Accept": "application/vnd.github+json"})
            if r.status != 200:
                raise RuntimeError("GitHub HTTP %d" % r.status)
            tag = r.json()["tag_name"]
        except Exception:
            return (cached or {}).get("tag")
        self._cache.save(UPDATE_CACHE, {"tag": tag})
        return tag


class SelfUpdater:
    """Replaces the plugin file with the one from the latest release, after sanity checks.

    Releases from v1.2.x run the same checks on the file they download, so the file at
    plugin_path_in_repo must stay a single self-contained script.
    """

    def __init__(self, http: HttpClient, checker: ReleaseChecker, github_raw: str, repo: str,
                 plugin_path_in_repo: str, version: str, target: str):
        self._http = http
        self._checker = checker
        self._raw = github_raw
        self._repo = repo
        self._path_in_repo = plugin_path_in_repo
        self._version = version
        self._target = target

    def update(self) -> bool:
        """True when the file was replaced. Asks GitHub afresh: the user asked to update, and
        a release published since the last check must not be missed."""
        tag = self._checker.latest(fresh=True)
        if not tag or not is_newer(tag, self._version):
            return False
        url = "%s/%s/%s/%s" % (self._raw, self._repo, tag, self._path_in_repo)
        r = self._http.request(url, timeout=30)
        if r.status != 200:
            raise RuntimeError("download HTTP %d" % r.status)
        code = r.body.decode("utf-8")
        want = 'VERSION = "%s"' % ".".join(str(n) for n in parse_version(tag))
        if not code.startswith(SHEBANG) or want not in code:
            raise RuntimeError("downloaded plugin failed sanity check")
        compile(code, "plugin", "exec")
        tmp = self._target + ".new"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(code)
        os.chmod(tmp, 0o755)
        os.replace(tmp, self._target)
        return True
