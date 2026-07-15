import sys
import unittest
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_monthly_product_profit import allocate_order, build_rows, category_for_sku, unit_cost_for_source


class MonthlyProductProfitTests(unittest.TestCase):
    def test_allocation_reconciles_exact_order_revenue(self):
        rows = allocate_order(
            Decimal("10000"),
            [
                ("상품A", Decimal("2"), Decimal("1000")),
                ("상품B", Decimal("1"), Decimal("3000")),
            ],
        )
        self.assertEqual(sum(row["revenue"] for row in rows), Decimal("10000"))
        self.assertEqual(sum(row["cogs"] for row in rows), Decimal("5000"))
        self.assertEqual(sum(row["profit"] for row in rows), Decimal("5000"))

    def test_category_mapping_uses_shipping_sku(self):
        self.assertEqual(category_for_sku("윤식단 단백밥 직화제육"), "단백밥")
        self.assertEqual(category_for_sku("윤식단 순수단백 저당 쌈장제육 100g"), "순수단백")
        self.assertEqual(category_for_sku("데리야끼 소스 40g"), "소스")
        self.assertEqual(category_for_sku("아이스팩 추가"), "부가옵션")

    def test_balancy_set_cost_respects_channel_quantity_contract(self):
        stored = Decimal("7410")
        self.assertEqual(unit_cost_for_source("ga4_self_store", "밸런시 마라 280g", stored), Decimal("1852.5"))
        self.assertEqual(unit_cost_for_source("naver_commerce", "밸런시 마라 280g", stored), stored)

    def test_database_effective_cost_overrides_stale_workbook_cost(self):
        data = {
            "orders": {("naver_commerce", "order-1"): Decimal("10000")},
            "matching": {
                ("naver_commerce", "order-1"): [
                    ("윤식단 단백밥 그릴드함박", Decimal("1"), Decimal("3218"))
                ]
            },
            "gauge_orders": 1,
            "gauge_revenue": Decimal("10000"),
            "official_channel_cogs": Decimal("3218"),
        }
        categories, products, meta = build_rows(
            data,
            {"윤식단 단백밥 그릴드함박": Decimal("3140")},
            product_limit=15,
        )
        self.assertEqual(products[0]["cogs"], 3218)
        self.assertEqual(categories[0]["cogs"], 3218)
        self.assertEqual(meta["cogsGapVsDailyProfit"], 0)


if __name__ == "__main__":
    unittest.main()
