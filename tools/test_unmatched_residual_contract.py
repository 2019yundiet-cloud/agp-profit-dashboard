import unittest
from pathlib import Path


class UnmatchedResidualContractTests(unittest.TestCase):
    def test_unmatched_profit_residual_is_visible_without_fake_sku(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn('"미매칭 추정"', source)
        self.assertIn("imweb_residual_revenue", source)
        self.assertIn("imweb_residual_cogs", source)
        self.assertIn("출고 SKU가 확인되지 않은 주문 잔액", source)


if __name__ == "__main__":
    unittest.main()
