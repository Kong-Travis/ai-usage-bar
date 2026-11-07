"""Pace of a limit: is it on track to run out before it resets? Pure maths, no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .domain import Window

# No projection until this much of the window has passed: the larger of 15 minutes and 10% of
# the window (30 minutes of a 5-hour window, about 17 hours of a week). Earlier, one burst of
# work is most of the elapsed time, so the straight-line rate swings wildly between probes and
# would warn about nothing; a week also needs most of a day before nights and idle hours count.
PACE_MIN_ELAPSED_SECONDS = 15 * 60
PACE_MIN_ELAPSED_SHARE = 0.10


@dataclass(frozen=True)
class Pace:
    """exhausted: the limit is used up (pct >= 100).
    run_out_at: epoch when the current rate reaches 100%, None when it lasts until the reset
    (or the limit is already exhausted).
    projected: % used at the reset if the rate holds, None when exhausted."""
    exhausted: bool
    run_out_at: Optional[float] = None
    projected: Optional[float] = None


def project_pace(w: Window, now: float) -> Optional[Pace]:
    """The pace of window w at time now, or None when the data cannot tell: no percentage,
    unknown reset or length, a window that has already rolled over, or too little elapsed."""
    if w.pct is None or not w.reset or not w.seconds or w.reset <= now:
        return None
    if w.pct >= 100:
        return Pace(exhausted=True)
    elapsed = w.seconds - (w.reset - now)
    if w.pct <= 0 or elapsed < max(PACE_MIN_ELAPSED_SECONDS, PACE_MIN_ELAPSED_SHARE * w.seconds):
        return None  # also covers a reset further away than the window is long (bad data)
    rate = w.pct / elapsed  # % per second
    run_out_at = now + (100 - w.pct) / rate
    return Pace(False, run_out_at if run_out_at < w.reset else None, rate * w.seconds)
