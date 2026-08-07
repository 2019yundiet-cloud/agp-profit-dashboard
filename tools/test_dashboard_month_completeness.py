#!/usr/bin/env python3
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_daily_rows  # noqa: E402


def extract_json_const(html, name):
    marker = f"const {name} ="
    start = html.index(marker) + len(marker)
    return json.JSONDecoder().raw_decode(html[start:].lstrip())[0]


class DashboardMonthCompletenessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.rows = extract_json_const(cls.html, "dailyRowsByMonth")
        cls.details = extract_json_const(cls.html, "dailyDetailByMonth")

    def test_july_has_every_calendar_day_and_matching_detail(self):
        july_rows = self.rows["2026-07"]
        days = [int(row["day"]) for row in july_rows]
        self.assertEqual(days, list(range(1, 32)))
        self.assertEqual(sorted(map(int, self.details["2026-07"].keys())), list(range(1, 32)))
        self.assertGreater(july_rows[-1]["revenue"], 0)
        self.assertGreater(self.details["2026-07"]["31"]["imweb"]["pay"], 0)
        self.assertGreater(self.details["2026-07"]["31"]["naver"]["pay"], 0)

    def test_dashboard_exposes_month_coverage_and_latest_day_controls(self):
        for marker in (
            'id="monthCoverageValue"',
            'id="monthCoverageNote"',
            'id="monthCoverageFill"',
            'id="latestClosedDayButton"',
            "function renderMonthCoverage(monthMeta, queryDay)",
            "month.isCompleteMonth",
        ):
            self.assertIn(marker, self.html)

    def test_builder_rejects_historical_month_gap(self):
        rows = [(day,) for day in range(1, 31)]
        with self.assertRaises(SystemExit):
            build_daily_rows.validate_row_coverage(
                "2026-07",
                rows,
                now=datetime(2026, 8, 4, tzinfo=ZoneInfo("Asia/Seoul")),
            )

    def test_builder_accepts_complete_historical_month(self):
        rows = [(day,) for day in range(1, 32)]
        build_daily_rows.validate_row_coverage(
            "2026-07",
            rows,
            now=datetime(2026, 8, 4, tzinfo=ZoneInfo("Asia/Seoul")),
        )

    def test_historical_backfill_keeps_newest_page_basis(self):
        self.assertEqual(
            build_daily_rows.latest_dashboard_basis(self.html, "2026-07-31"),
            "2026-08-06",
        )


if __name__ == "__main__":
    unittest.main()
