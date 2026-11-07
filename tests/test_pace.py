"""Pace projection: when a limit runs out before it resets."""
import srcpath  # noqa: F401  (must come first)

import unittest

from claude_usage.domain import FIVE_HOUR, FIVE_HOUR_SECONDS, WEEK_SECONDS, WEEKLY, Window
from claude_usage.pace import Pace, project_pace

NOW = 1_000_000
H = 3600


def five(pct, left, seconds=FIVE_HOUR_SECONDS):
    return Window(FIVE_HOUR, pct, NOW + left if left is not None else 0, seconds)


class ProjectPace(unittest.TestCase):
    def test_runs_out_before_the_reset(self):
        # 50% in 2h: 25%/h, so the other 50% last 2h, while the reset is 3h away.
        p = project_pace(five(50.0, 3 * H), NOW)
        self.assertFalse(p.exhausted)
        self.assertAlmostEqual(p.run_out_at, NOW + 2 * H)
        self.assertAlmostEqual(p.projected, 125.0)

    def test_lasts_until_the_reset(self):
        # 30% in 2h projects to 75% at the reset.
        p = project_pace(five(30.0, 3 * H), NOW)
        self.assertEqual(p, Pace(False, None, 75.0))

    def test_reaching_100_exactly_at_the_reset_is_not_a_run_out(self):
        p = project_pace(five(40.0, 3 * H), NOW)
        self.assertIsNone(p.run_out_at)
        self.assertAlmostEqual(p.projected, 100.0)

    def test_exhausted_needs_only_a_future_reset(self):
        self.assertEqual(project_pace(five(100.0, 10), NOW), Pace(True))
        self.assertEqual(project_pace(five(130.0, 10), NOW), Pace(True))
        # Even 1 minute into the window: the elapsed floor applies to projections only.
        self.assertEqual(project_pace(five(100.0, FIVE_HOUR_SECONDS - 60), NOW), Pace(True))

    def test_elapsed_floor(self):
        # 5-hour window: 10% is 30 minutes, more than the 15-minute minimum.
        self.assertIsNone(project_pace(five(50.0, FIVE_HOUR_SECONDS - 29 * 60), NOW))
        self.assertIsNotNone(project_pace(five(50.0, FIVE_HOUR_SECONDS - 30 * 60), NOW))
        # Weekly: 10% is 16.8 hours.
        week = lambda elapsed: Window(WEEKLY, 50.0, NOW + WEEK_SECONDS - elapsed, WEEK_SECONDS)  # noqa: E731
        self.assertIsNone(project_pace(week(16 * H), NOW))
        self.assertIsNotNone(project_pace(week(17 * H), NOW))
        # A short window falls back to the 15-minute minimum.
        self.assertIsNone(project_pace(five(50.0, 50 * 60, seconds=H), NOW))
        self.assertIsNotNone(project_pace(five(50.0, 45 * 60, seconds=H), NOW))

    def test_incomplete_or_stale_data_gives_nothing(self):
        cases = {
            "no limit": five(None, 3 * H),
            "unknown reset": five(50.0, None),
            "unknown length": five(50.0, 3 * H, seconds=0),
            "window already rolled": five(100.0, 0),
            "reset further than the window is long": five(50.0, 6 * H),
            "nothing used": five(0.0, 3 * H),
        }
        for name, w in cases.items():
            with self.subTest(name):
                self.assertIsNone(project_pace(w, NOW))


if __name__ == "__main__":
    unittest.main()
