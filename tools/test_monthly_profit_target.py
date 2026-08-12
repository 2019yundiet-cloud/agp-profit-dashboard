import json
import math
import unittest
from pathlib import Path


HTML_PATH = Path(__file__).resolve().parent.parent / "index.html"


def extract_json_const(source: str, const_name: str):
    marker = f"const {const_name} ="
    marker_index = source.index(marker)
    start = min(
        index
        for index in (
            source.find("{", marker_index + len(marker)),
            source.find("[", marker_index + len(marker)),
        )
        if index >= 0
    )
    opening = source[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return json.loads(source[start : index + 1])
    raise AssertionError(f"Could not extract {const_name}")


class MonthlyProfitTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.plan = extract_json_const(cls.html, "monthlyProfitTargetPlan")
        cls.fixed_cost_forecast = extract_json_const(cls.html, "fixedCostForecastMonths")

    def test_visible_table_and_formula_are_present(self):
        self.assertIn('id="monthlyProfitTargetRows"', self.html)
        self.assertIn("월 순이익 1,500만원 달성 필요 매출", self.html)
        self.assertIn("필요 매출 = (예상 고정비 + 목표 순이익", self.html)
        self.assertIn('const planningPercent = n => (n * 100).toFixed(2) + "%";', self.html)
        self.assertIn('const auditPercent = n => (n * 100).toFixed(6) + "%";', self.html)
        self.assertIn("10월 기보 추가 원리금은 미확정이라 제외했습니다.", self.html)

    def test_planning_margin_matches_closed_july_source(self):
        self.assertEqual(self.plan["basisRange"], ["2026-07-01", "2026-07-31"])
        self.assertEqual(self.plan["basisRevenue"], 138264188)
        self.assertEqual(self.plan["basisPreFixedProfit"], 20595725.64)
        calculated = self.plan["basisPreFixedProfit"] / self.plan["basisRevenue"]
        self.assertAlmostEqual(self.plan["planningMargin"], calculated, places=14)
        self.assertAlmostEqual(calculated * 100, 14.8959220300777, places=10)

    def test_august_latest_margin_corroborates_planning_margin(self):
        self.assertEqual(self.plan["corroboratingRange"], ["2026-08-01", "2026-08-11"])
        self.assertLess(
            abs(self.plan["corroboratingMargin"] - self.plan["planningMargin"]),
            0.001,
        )

    def test_monthly_targets_use_audited_fixed_costs_and_ceiling(self):
        fixed_costs = {
            "2026-08": 18222350,
            "2026-09": 17752062,
            "2026-10": 17752062,
            "2026-11": 16804922,
            "2026-12": 16804922,
        }
        expected_targets = {
            "2026-08": 223100000,
            "2026-09": 219900000,
            "2026-10": 219900000,
            "2026-11": 213600000,
            "2026-12": 213600000,
        }
        self.assertEqual(
            [row["month"] for row in self.fixed_cost_forecast],
            list(expected_targets),
        )
        for month, fixed_cost in fixed_costs.items():
            exact = (
                fixed_cost + self.plan["targetAfterFixedProfit"]
            ) / self.plan["planningMargin"]
            rounded = math.ceil(exact / self.plan["roundingUnit"]) * self.plan["roundingUnit"]
            self.assertEqual(rounded, expected_targets[month])
            self.assertGreaterEqual(
                rounded * self.plan["planningMargin"] - fixed_cost,
                self.plan["targetAfterFixedProfit"],
            )


if __name__ == "__main__":
    unittest.main()
