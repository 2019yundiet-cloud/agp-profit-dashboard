import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_daily_detail import BALANCY_SET_COST_SKUS, CATEGORY_CASE_SQL, SOURCE_SYSTEMS


class DailyDetailContractTests(unittest.TestCase):
    def test_shipping_match_is_the_product_detail_source(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("from stg_ezadmin_order_match sem", source)
        self.assertNotIn("from fact_order fo join fact_order_item", source)

    def test_dry_ice_is_an_add_on_option(self):
        self.assertIn("드라이아이스", CATEGORY_CASE_SQL)
        self.assertIn("부가옵션", CATEGORY_CASE_SQL)

    def test_channel_and_balancy_cost_contracts_match_monthly_builder(self):
        self.assertEqual(SOURCE_SYSTEMS, ("ga4_self_store", "naver_commerce"))
        self.assertIn("밸런시 마라 280g", BALANCY_SET_COST_SKUS)
        self.assertIn("밸런시 시그니처 280g", BALANCY_SET_COST_SKUS)


if __name__ == "__main__":
    unittest.main()
