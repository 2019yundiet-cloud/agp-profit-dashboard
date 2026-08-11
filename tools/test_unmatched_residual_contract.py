import unittest
from pathlib import Path

from build_daily_detail import normalize_rounding_residual


class UnmatchedResidualContractTests(unittest.TestCase):
    def test_unmatched_profit_residual_is_visible_without_fake_sku(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn('"미매칭 추정"', source)
        self.assertIn("imweb_residual_revenue", source)
        self.assertIn("imweb_residual_cogs", source)
        self.assertIn("출고 SKU가 확인되지 않은 주문 잔액", source)

    def test_one_won_allocation_rounding_does_not_block_positive_cogs_residual(self):
        self.assertEqual(normalize_rounding_residual(-1, 440), (0, 440))

    def test_material_negative_residual_is_not_hidden(self):
        self.assertEqual(normalize_rounding_residual(-7, 440), (-7, 440))

    def test_api_reconciliation_has_named_visible_adjustment_lane(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn('"아임웹 API 추가·보정"', source)
        self.assertIn("api_reconciled_without_unclassified_revenue", source)
        self.assertIn("협찬 가능성이 있는 미분류 출고에서는 매출을 추정하지 않았습니다", source)


if __name__ == "__main__":
    unittest.main()
