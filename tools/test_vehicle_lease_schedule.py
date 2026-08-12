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
        cls.fixed_cost_change_schedule = extract_json_const(cls.html, "fixedCostChangeSchedule")
        cls.fixed_cost_forecast_months = extract_json_const(cls.html, "fixedCostForecastMonths")
        cls.month_config = extract_json_const(cls.html, "monthConfig")
        cls.daily_rows = extract_json_const(cls.html, "dailyRowsByMonth")

    def scheduled_fixed_cost_rows(self, month):
        available_months = sorted(key for key in self.fixed_costs if key <= month)
        self.assertTrue(available_months)
        rows = [list(row) for row in self.fixed_costs[available_months[-1]]]
        excluded_names = {
            name
            for rule in self.fixed_cost_change_schedule
            if month >= rule["effectiveFrom"]
            for name in rule["excludedNames"]
        }
        rows = [row for row in rows if row[0] not in excluded_names]
        lease_period = next(
            period
            for period in self.lease_schedule
            if month >= period["effectiveFrom"]
            and (period["effectiveTo"] is None or month <= period["effectiveTo"])
        )
        insurance_period = next(
            period
            for period in self.insurance_schedule
            if month >= period["effectiveFrom"]
            and (period["effectiveTo"] is None or month <= period["effectiveTo"])
        )
        rows = [row for row in rows if not row[0].startswith("차량")]
        finance_index = next(
            (index for index, row in enumerate(rows) if row[0].startswith("금융")),
            -1,
        )
        insert_at = finance_index + 1 if finance_index >= 0 else 0
        rows[insert_at:insert_at] = lease_period["rows"] + insurance_period["rows"]
        return rows

    def test_june_and_july_use_the_audited_vehicle_rows(self):
        expected_by_month = {
            "2026-06": {
                "차량 리스 · IM캐피탈 (김애경 차량)": 554500,
                "차량 리스 · KB (아빠 차량)": 883840,
                "차량 리스 · BMW (윤준호 차량)": 980993,
            },
            "2026-07": {
                "차량 리스 · IM캐피탈 (김애경 차량)": 554400,
                "차량 리스 · KB (아빠 차량, 인상)": 947140,
                "차량 리스 · BMW (윤준호 차량, 인상)": 1068993,
            },
        }
        for month, expected in expected_by_month.items():
            actual = {
                name: amount
                for name, amount, *_ in self.fixed_costs[month]
                if name.startswith("차량 리스")
            }
            self.assertEqual(actual, expected)

    def test_vehicle_schedule_changes_bmw_amount_from_september(self):
        june_period, july_period, september_period, november_period = self.lease_schedule
        self.assertEqual(june_period["effectiveFrom"], "2026-06")
        self.assertEqual(june_period["effectiveTo"], "2026-06")
        self.assertEqual(sum(amount for _, amount in june_period["rows"]), 2419333)
        self.assertIn(["차량 리스 · BMW (윤준호 차량)", 980993], june_period["rows"])
        self.assertEqual(july_period["effectiveFrom"], "2026-07")
        self.assertEqual(july_period["effectiveTo"], "2026-08")
        self.assertEqual(sum(amount for _, amount in july_period["rows"]), 2570533)
        self.assertIn(["차량 리스 · BMW (윤준호 차량, 인상)", 1068993], july_period["rows"])
        self.assertEqual(september_period["effectiveFrom"], "2026-09")
        self.assertEqual(september_period["effectiveTo"], "2026-10")
        self.assertEqual(sum(amount for _, amount in september_period["rows"]), 2100245)
        self.assertIn(["차량 리스 · BMW (윤준호 차량)", 598705], september_period["rows"])
        self.assertEqual(november_period["effectiveFrom"], "2026-11")
        self.assertIsNone(november_period["effectiveTo"])
        self.assertEqual(sum(amount for _, amount in november_period["rows"]), 1153105)
        self.assertNotIn(["차량 리스 · KB (아빠 차량, 인상)", 947140], november_period["rows"])

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

    def test_august_stops_accounting_services_and_uses_new_subscription_costs(self):
        rows = {name: amount for name, amount, *_ in self.scheduled_fixed_cost_rows("2026-08")}
        self.assertNotIn("외주 서비스 · 경리 (2026년 7월까지)", rows)
        self.assertNotIn("사스 서비스 · 경리나라", rows)
        self.assertNotIn("사스 서비스 · 커넥트파킹", rows)
        self.assertEqual(rows["사스 서비스 · Claude"], 160000)
        self.assertEqual(rows["사스 서비스 · OpenAI"], 450000)
        self.assertEqual(rows["외주 서비스 · 세무법인청년 (인상)"], 230000)
        self.assertEqual(rows["차량 리스 · BMW (윤준호 차량, 인상)"], 1068993)
        # 2026-08-01 실측 재점검: 나이스 54,890 · 네이버페이 Plus 14,800 해지 확정 제외
        self.assertNotIn("사스 서비스 · 정기과금_나이스", rows)
        self.assertNotIn("사스 서비스 · 네이버페이 Plus", rows)
        self.assertEqual(rows["사스 서비스 · 네이버플러스"], 4900)
        self.assertEqual(sum(rows.values()), 18222350)

    def test_september_uses_reduced_bmw_and_keeps_increased_tax_fee(self):
        rows = {name: amount for name, amount, *_ in self.scheduled_fixed_cost_rows("2026-09")}
        self.assertNotIn("외주 서비스 · 경리 (2026년 7월까지)", rows)
        self.assertNotIn("사스 서비스 · 경리나라", rows)
        self.assertNotIn("사스 서비스 · 커넥트파킹", rows)
        self.assertEqual(rows["사스 서비스 · Claude"], 160000)
        self.assertEqual(rows["사스 서비스 · OpenAI"], 450000)
        self.assertEqual(rows["외주 서비스 · 세무법인청년 (인상)"], 230000)
        self.assertEqual(rows["차량 리스 · BMW (윤준호 차량)"], 598705)
        self.assertNotIn("사스 서비스 · 정기과금_나이스", rows)
        self.assertNotIn("사스 서비스 · 네이버페이 Plus", rows)
        self.assertEqual(sum(rows.values()), 17752062)

    def test_november_excludes_kb_lease_and_recalculates_total(self):
        october_rows = {name: amount for name, amount, *_ in self.scheduled_fixed_cost_rows("2026-10")}
        november_rows = {name: amount for name, amount, *_ in self.scheduled_fixed_cost_rows("2026-11")}
        self.assertEqual(sum(october_rows.values()), 17752062)
        self.assertIn("차량 리스 · KB (아빠 차량, 인상)", october_rows)
        self.assertNotIn("차량 리스 · KB (아빠 차량, 인상)", november_rows)
        self.assertEqual(sum(november_rows.values()), 16804922)

    def test_future_change_schedule_is_visible(self):
        self.assertIn('id="fixedCostChangeRows"', self.html)
        visible_changes = {
            (row["effectiveFrom"], row["item"], row["change"])
            for row in self.fixed_cost_change_schedule
        }
        self.assertIn(("2026-08", "경리 외주", "중단 (월 800,000원 제외)"), visible_changes)
        self.assertIn(("2026-08", "경리나라", "사용 중단 (월 75,900원 제외)"), visible_changes)
        self.assertIn(("2026-08", "세무 외주", "계속 진행, 월 200,000원 → 230,000원 인상"), visible_changes)
        self.assertIn(("2026-08", "Claude", "월 335,766원 → 160,000원"), visible_changes)
        self.assertIn(("2026-08", "OpenAI", "월 600,000원 → 450,000원"), visible_changes)
        self.assertIn(("2026-08", "커넥트파킹 주차", "종료 (월 110,000원 제외)"), visible_changes)
        self.assertIn(("2026-08", "정기과금_나이스", "해지 확정 (월 54,890원 제외)"), visible_changes)
        self.assertIn(("2026-08", "네이버페이 Plus", "해지 확정 (월 14,800원 제외)"), visible_changes)
        self.assertIn(("2026-09", "BMW 리스료", "월 1,068,993원 → 598,705원"), visible_changes)
        self.assertIn(("2026-11", "KB 리스료", "종료 (월 947,140원 제외)"), visible_changes)

    def test_august_to_december_forecast_table_uses_audited_totals(self):
        self.assertIn('id="fixedCostForecastRows"', self.html)
        self.assertEqual(
            [row["month"] for row in self.fixed_cost_forecast_months],
            ["2026-08", "2026-09", "2026-10", "2026-11", "2026-12"],
        )
        expected_totals = {
            "2026-08": 18222350,
            "2026-09": 17752062,
            "2026-10": 17752062,
            "2026-11": 16804922,
            "2026-12": 16804922,
        }
        for month, expected_total in expected_totals.items():
            rows = self.scheduled_fixed_cost_rows(month)
            self.assertEqual(sum(row[1] for row in rows), expected_total)
        self.assertIn("10월 기보 연장·원금 추가 납입액은 아직 확정되지 않아 포함하지 않았습니다.", self.html)
        october = next(row for row in self.fixed_cost_forecast_months if row["month"] == "2026-10")
        self.assertEqual(october["status"], "기본 예상")
        self.assertIn("기보 추가금 미포함", october["change"])

    def test_monthly_fixed_cost_totals_are_recalculated(self):
        expected = {"2026-06": 18271459, "2026-07": 18952126}
        for month, total in expected.items():
            self.assertEqual(sum(row[1] for row in self.fixed_costs[month]), total)
            self.assertEqual(self.month_config[month]["fixedCost"], total)

    def test_july_visible_defaults_match_the_itemized_total(self):
        self.assertIn('id="fixedInput" type="number" value="18952126"', self.html)
        self.assertIn('id="fixedCostInfoValue">₩18,952,126', self.html)

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
        fixed_cost_rows = {name: amount for name, amount, *_ in self.fixed_costs["2026-07"]}
        approved_fixed_cost = sum(fixed_cost_rows.values())
        insurance_cost = fixed_cost_rows["차량 보험비"]
        profit_after_fixed_cost = pre_fixed_profit - approved_fixed_cost
        profit_without_insurance = pre_fixed_profit - (approved_fixed_cost - insurance_cost)

        self.assertEqual(approved_fixed_cost, 18952126)
        self.assertEqual(insurance_cost, 300000)
        self.assertAlmostEqual(profit_without_insurance - profit_after_fixed_cost, 300000, places=2)


if __name__ == "__main__":
    unittest.main()
