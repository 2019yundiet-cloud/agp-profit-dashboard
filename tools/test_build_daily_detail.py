import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_daily_detail import (
    BALANCY_SET_COST_SKUS,
    CATEGORY_CASE_SQL,
    CATEGORY_ORDER,
    DEFAULT_IMWEB_ARTIFACT_DIR,
    SOURCE_SYSTEMS,
    resolve_end_exclusive,
)
from build_daily_rows import update_metadata


class DailyDetailContractTests(unittest.TestCase):
    def test_through_date_limits_detail_query_to_next_day_exclusive(self):
        self.assertEqual(resolve_end_exclusive("2026-08", "2026-08-12"), "2026-08-13")

    def test_shipping_match_is_the_product_detail_source(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("from stg_ezadmin_order_match sem", source)
        self.assertNotIn("from fact_order fo join fact_order_item", source)

    def test_dry_ice_is_an_add_on_option(self):
        self.assertIn("드라이아이스", CATEGORY_CASE_SQL)
        self.assertIn("부가옵션", CATEGORY_CASE_SQL)

    def test_pure_protein_flavors_are_separate_categories(self):
        self.assertNotIn("순수단백", CATEGORY_ORDER)
        for category in ("직화제육", "불고기", "쌈장제육"):
            self.assertIn(category, CATEGORY_ORDER)
            self.assertIn(f"then '{category}'", CATEGORY_CASE_SQL)
        self.assertLess(CATEGORY_CASE_SQL.index("then '단백밥'"), CATEGORY_CASE_SQL.index("then '직화제육'"))

    def test_channel_and_balancy_cost_contracts_match_monthly_builder(self):
        self.assertEqual(SOURCE_SYSTEMS, ("ga4_self_store", "naver_commerce"))
        self.assertIn("밸런시 마라 280g", BALANCY_SET_COST_SKUS)
        self.assertIn("밸런시 시그니처 280g", BALANCY_SET_COST_SKUS)

    def test_default_self_store_artifact_path_uses_current_source_of_truth(self):
        self.assertEqual(
            DEFAULT_IMWEB_ARTIFACT_DIR,
            Path("/Users/junho/Documents/데이터관리/data/imweb_profit/artifacts"),
        )

    def test_order_and_buyer_stats_use_only_supported_channel_sources(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("fo.source_system = any(%s)", source)

    def test_unmatched_naver_revenue_is_visible_residual_not_fabricated_sku(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("naver_residual_revenue", source)
        self.assertIn("naver_residual_cogs", source)
        self.assertIn('residual["nAmt"] += naver_residual_revenue', source)
        self.assertIn("네이버 매출", source)
        self.assertNotIn("naver_fallback_sku", source)

    def test_matched_basis_excludes_unmatched_revenue_from_contribution_check(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("excludedUnmatchedRevenue", source)
        self.assertIn("미매칭 손익 제외", source)
        self.assertIn("사용자 승인 잠정 매칭 기준", source)

    def test_dashboard_does_not_render_excluded_revenue_as_product_margin(self):
        html = Path(__file__).resolve().parent.parent.joinpath("index.html").read_text(encoding="utf-8")
        self.assertIn('excludedFromProfit = cat === "미매칭 손익 제외"', html)
        self.assertIn('excludedFromProfit ? "손익 제외"', html)

    def test_visible_generated_at_is_bound_to_the_meta_timestamp(self):
        html = Path(__file__).resolve().parent.parent.joinpath("index.html").read_text(encoding="utf-8")
        self.assertIn('meta[name="data-generated-at"]', html)
        self.assertIn("function renderGeneratedAt()", html)
        self.assertIn("renderGeneratedAt();", html)

    def test_daily_rows_refreshes_generated_and_basis_metadata(self):
        source = (
            '<meta name="data-generated-at" content="old">'
            '<meta name="data-basis-date" content="2026-07-23">'
        )
        updated = update_metadata(source, "2026-07-26", "2026-07-27T10:45:00+09:00")
        self.assertIn('content="2026-07-27T10:45:00+09:00"', updated)
        self.assertIn('content="2026-07-26"', updated)


if __name__ == "__main__":
    unittest.main()
