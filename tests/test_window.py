from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import support  # noqa: F401
from support import utc

from mailbox_watcher.window import ReportWindow, report_window


class ReportWindowTests(unittest.TestCase):
    def test_six_hour_window_after_boundary(self):
        window = report_window(utc(2026, 11, 21, 12, 5), 6)
        self.assertEqual(window.start, utc(2026, 11, 21, 6))
        self.assertEqual(window.end, utc(2026, 11, 21, 12))

    def test_exactly_on_boundary_reports_previous_interval(self):
        window = report_window(utc(2026, 11, 21, 12, 0), 6)
        self.assertEqual((window.start, window.end), (utc(2026, 11, 21, 6), utc(2026, 11, 21, 12)))

    def test_just_before_boundary(self):
        window = report_window(utc(2026, 11, 21, 11, 59, 59), 6)
        self.assertEqual((window.start, window.end), (utc(2026, 11, 21, 0), utc(2026, 11, 21, 6)))

    def test_midnight_window_crosses_day(self):
        window = report_window(utc(2026, 11, 21, 0, 5), 6)
        self.assertEqual((window.start, window.end), (utc(2026, 11, 20, 18), utc(2026, 11, 21, 0)))

    def test_year_boundary(self):
        window = report_window(utc(2027, 1, 1, 0, 5), 24)
        self.assertEqual((window.start, window.end), (utc(2026, 12, 31), utc(2027, 1, 1)))

    def test_non_utc_input_is_normalized(self):
        vienna = timezone(timedelta(hours=1))
        window = report_window(datetime(2026, 11, 21, 13, 5, tzinfo=vienna), 6)
        self.assertEqual((window.start, window.end), (utc(2026, 11, 21, 6), utc(2026, 11, 21, 12)))

    def test_consecutive_runs_tile_without_gaps_or_overlaps(self):
        for interval in (1, 2, 3, 4, 6, 8, 12, 24):
            with self.subTest(interval=interval):
                previous = None
                moment = utc(2026, 11, 1, 0, 5)
                for _ in range(100):
                    window = report_window(moment, interval)
                    self.assertEqual(window.end - window.start, timedelta(hours=interval))
                    if previous is not None:
                        self.assertEqual(window.start, previous.end)
                    previous = window
                    moment += timedelta(hours=interval)

    def test_delayed_run_within_interval_reports_same_window(self):
        on_time = report_window(utc(2026, 11, 21, 12, 5), 6)
        late = report_window(utc(2026, 11, 21, 17, 59), 6)
        self.assertEqual(on_time, late)

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            report_window(datetime(2026, 11, 21, 12), 6)

    def test_invalid_interval_rejected(self):
        for interval in (0, 5, 7, 48):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    report_window(utc(2026, 11, 21, 12), interval)

    def test_contains_is_half_open(self):
        window = ReportWindow(utc(2026, 11, 21, 6), utc(2026, 11, 21, 12))
        self.assertTrue(window.contains(utc(2026, 11, 21, 6)))
        self.assertTrue(window.contains(utc(2026, 11, 21, 11, 59, 59)))
        self.assertFalse(window.contains(utc(2026, 11, 21, 12)))
        self.assertFalse(window.contains(utc(2026, 11, 21, 5, 59, 59)))

    def test_contains_rejects_naive(self):
        window = ReportWindow(utc(2026, 11, 21, 6), utc(2026, 11, 21, 12))
        with self.assertRaises(ValueError):
            window.contains(datetime(2026, 11, 21, 7))


if __name__ == "__main__":
    unittest.main()
