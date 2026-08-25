import unittest
from pathlib import Path


HTML = Path(__file__).resolve().parents[1] / "index.html"


class LegacyProductVisibilityTest(unittest.TestCase):
    def test_broad_pure_protein_category_is_never_rendered(self):
        source = HTML.read_text(encoding="utf-8")
        self.assertIn("hasBroadPureProteinCategory", source)
        self.assertIn(r"/^(윤식단\s*)?순수단백$/", source)
        self.assertIn("if (hasBroadPureProteinCategory || reconciliationFailed)", source)

    def test_stale_or_unreconciled_monthly_product_data_is_hidden(self):
        source = HTML.read_text(encoding="utf-8")
        for guard in (
            "Number(meta.coveragePercent || 0) < 90",
            "Number(meta.factRevenueGapVsGauge || 0) !== 0",
            "Number(meta.cogsGapVsDailyProfit || 0) !== 0",
            "productThroughDay < latestDailyDay",
        ):
            self.assertIn(guard, source)
        self.assertIn("월간 제품 분류표는 세부 맛 분류와 공식 원가 대사가 끝나지 않아 숨겼습니다.", source)


if __name__ == "__main__":
    unittest.main()
