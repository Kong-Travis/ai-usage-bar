"""Use cases: build the menu, refresh every account. Frontends depend on this, not on I/O."""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, List, Optional, Sequence, TypeVar

from .domain import AccountRow, Clock
from .sources import AccountRegistry
from .updater import ReleaseChecker, is_newer
from .usage import IdentityService, UsageService
from .view import Menu, Presenter, short_name


T = TypeVar("T")
R = TypeVar("R")
MAX_PARALLEL_PROBES = 16


def map_parallel(fn: Callable[[T], R], items: Iterable[T]) -> List[R]:
    """fn over items on a thread pool, results in input order. Each account's probe is one
    blocking HTTP call, so ten accounts take about as long as one."""
    items = list(items)
    if len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_PROBES, len(items))) as pool:
        return list(pool.map(fn, items))


class RunLog:
    """Keeps the last run's errors on disk so a blank menu can be diagnosed."""

    def __init__(self, directory: str, clock: Clock, version: str):
        self.path = os.path.join(directory, "last-run.log")
        self._clock = clock
        self._version = version

    def write(self, rows: Sequence[AccountRow]) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._clock.now()))
                f.write("%s v%s python=%s\n" % (stamp, self._version, sys.executable))
                for r in rows:
                    f.write("%s %s: %s\n" % (r.short, r.label, r.error or "ok"))
        except OSError:
            pass


class UsageApp:
    def __init__(self, registry: AccountRegistry, usage: UsageService, identity: IdentityService,
                 releases: ReleaseChecker, presenter: Presenter, run_log: RunLog, version: str,
                 mapper: Callable[[Callable, Iterable], List] = map_parallel):
        self._map = mapper
        self._registry = registry
        self._usage = usage
        self._identity = identity
        self._releases = releases
        self._presenter = presenter
        self._run_log = run_log
        self._version = version

    def rows(self, force: bool = False) -> List[AccountRow]:
        accounts = self._registry.accounts()

        def read(acc):
            usage, error = self._usage.usage(acc, force=force)
            # A provider that reports the email with the usage needs no profile lookup.
            detail = usage.email if usage and usage.email else self._identity.detail(acc)
            return usage, error, detail

        taken: set = set()
        return [AccountRow(short_name(acc.label, taken, acc.provider), acc.label, detail, usage,
                           error, acc.provider)
                for acc, (usage, error, detail) in zip(accounts, self._map(read, accounts))]

    def menu(self, force: bool = False) -> Menu:
        tag = self._releases.latest()
        rows = self.rows(force=force)
        if rows:
            self._run_log.write(rows)
        newer = is_newer(tag, self._version)
        return self._presenter.present(rows, tag if newer else None,
                                       up_to_date=tag is not None and not newer)

    def refresh(self) -> None:
        """Probe every account now, ignoring the cache."""
        self._map(lambda acc: self._usage.usage(acc, force=True), self._registry.accounts())

    def newer_release(self, fresh: bool = False) -> Optional[str]:
        """fresh: ask GitHub now (Check for updates) instead of trusting the hourly cache."""
        tag = self._releases.latest(fresh=fresh)
        return tag if is_newer(tag, self._version) else None
