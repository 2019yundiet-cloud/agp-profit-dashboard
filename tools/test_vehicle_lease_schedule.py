import json
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


class VehicleLeaseScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.fixed_costs = extract_json_const(cls.html, "fixedCostsByMonth")
        cls.lease_schedule = extract_json_const(cls.html, "vehicleLeaseSchedule")
        cls.insurance_schedule = extract_json_const(cls.html, "vehicleInsuranceSchedule")
        cls.month_config = extract_json_const(cls.html, "monthConfig")
        cls.daily_rows = extract_json_const(cls.html, "dailyRowsByMonth")

    def test_june_and_july_use_the_owner_approved_vehicle_rows(self):
        expected = {
            "차량 리스 · IM캐피탈 (김애경 차량)": 554500,
            "차량 리스 · KB (아빠 차량)": 883840,
            "차량 리스 · BMW (윤준호 차량)": 980993,
        }
        for month in ("2026-06", "2026-07"):
            actual = {
                name: amount
                for name, amount, *_ in self.fixed_costs[month]
                if name.startswith("차량 리스")
            }
            self.assertEqual(actual, expected)

    def test_vehicle_schedule_changes_bmw_amount_from_september(self):
        june_period, september_period = self.lease_schedule
        self.assertEqual(june_period["effectiveFrom"], "2026-06")
        self.assertEqual(june_period["effectiveTo"], "2026-08")
        self.assertEqual(sum(amount for _, amount in june_period["rows"]), 2419333)
        self.assertIn(["차량 리스 · BMW (윤준호 차량)", 980993], june_period["rows"])
        self.assertEqual(september_period["effectiveFrom"], "2026-09")
        self.assertIsNone(september_period["effectiveTo"])
        self.assertEqual(sum(amount for _, amount in september_period["rows"]), 2037045)
        self.assertIn(["차량 리스 · BMW (윤준호 차량)", 598705], september_period["rows"])

    def test_vehicle_insurance_is_separate_and_effective_from_june(self):
        self.assertEqual(
            self.insurance_schedule,
            [
                {
                    "effectiveFrom": "2026-06",
                    "effectiveTo": None,
                    "rows": [["차량 보험비", 300000]],
                }
            ],
        )
        for month in ("2026-06", "2026-07"):
            rows = {name: amount for name, amount, *_ in self.fixed_costs[month]}
            self.assertEqual(rows["차량 보험비"], 300000)

    def test_monthly_fixed_cost_totals_are_recalculated(self):
        expected = {"2026-06": 18271459, "2026-07": 19461474}
        for month, total in expected.items():
            self.assertEqual(sum(row[1] for row in self.fixed_costs[month]), total)
            self.assertEqual(self.month_config[month]["fixedCost"], total)

    def test_july_visible_defaults_match_the_itemized_total(self):
        self.assertIn('id="fixedInput" type="number" value="19461474"', self.html)
        self.assertIn('id="fixedCostInfoValue">₩19,461,474', self.html)

    def test_july_net_profit_decreases_by_the_insurance_cost(self):
        pre_fixed_profit = sum(
            row["imweb"]
            + row["naver"]
            - row["meta"]
            - row["googleAd"]
            - row["naverAd"]
            - row["revenue"] * 0.12
            for row in self.daily_rows["2026-07"]
        )
        self.assertAlmostEqual(pre_fixed_profit, 12526521.40, places=2)
        self.assertEqual(round(pre_fixed_profit - 19461474), -6934953)


if __name__ == "__main__":
    unittest.main()
