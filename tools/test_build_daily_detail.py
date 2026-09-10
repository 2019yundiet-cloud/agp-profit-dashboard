import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_daily_detail import (
    BALANCY_SET_COST_SKUS,
    CATEGORY_CASE_SQL,
    CATEGORY_ORDER,
    DEFAULT_IMWEB_ARTIFACT_DIR,
    IMWEB_COMMERCE_SOURCES,
    SOURCE_SYSTEMS,
    select_commerce_sources,
    resolve_channel_stats,
    resolve_end_exclusive,
)
from build_daily_rows import update_metadata, validate_naver_unmatched_profit_contract


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
        for category in ("직화제육", "불고기", "쌈장제육", "돈다리살 고추장맛"):
            self.assertIn(category, CATEGORY_ORDER)
            self.assertIn(f"then '{category}'", CATEGORY_CASE_SQL)
        self.assertLess(CATEGORY_CASE_SQL.index("then '단백밥'"), CATEGORY_CASE_SQL.index("then '직화제육'"))
        self.assertLess(
            CATEGORY_CASE_SQL.index("then '돈다리살 고추장맛'"),
            CATEGORY_CASE_SQL.index("then '직화제육'"),
        )

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

    def test_verified_self_store_artifact_overrides_only_order_count(self):
        stat_map = {
            (26, "i"): {"orders": 40, "buyers": 40, "first": 0, "repeat": 40}
        }
        self.assertEqual(
            resolve_channel_stats(stat_map, 26, "i", {"total_orders": 42}),
            {"orders": 42, "buyers": 40, "first": 0, "repeat": 40},
        )

    def test_unmatched_naver_revenue_is_visible_but_excluded_from_profit(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("naver_residual_revenue", source)
        self.assertIn("naver_residual_cogs", source)
        self.assertIn('residual["nAmt"] += naver_residual_revenue', source)
        self.assertIn("naver_excluded_revenue", source)
        self.assertIn("출고 SKU가 확인되지 않은 네이버 주문", source)
        self.assertNotIn("naver_fallback_sku", source)

    def test_daily_rows_blocks_unfixed_post_contract_unmatched_day(self):
        row = (
            27, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0,
            "api", "api", "api", "OK", 1, False,
        )
        with self.assertRaisesRegex(SystemExit, "NAVER_UNMATCHED_PROFIT_CONTRACT_MISSING"):
            validate_naver_unmatched_profit_contract("2026-08", [row])

    def test_daily_rows_accepts_fixed_post_contract_unmatched_day(self):
        row = (
            27, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0,
            "api", "api", "api", "OK", 1, True,
        )
        validate_naver_unmatched_profit_contract("2026-08", [row])

    def test_matched_basis_excludes_unmatched_revenue_from_contribution_check(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("excludedUnmatchedRevenue", source)
        self.assertIn("미매칭 손익 제외", source)
        self.assertIn("사용자 승인 잠정 매칭 기준", source)

    def test_dashboard_does_not_render_excluded_revenue_as_product_margin(self):
        html = Path(__file__).resolve().parent.parent.joinpath("index.html").read_text(encoding="utf-8")
        self.assertIn('excludedFromProfit = cat === "미매칭 손익 제외"', html)
        self.assertIn('excludedFromProfit ? "손익 제외"', html)

    def test_imweb_commerce_sources_includes_imweb(self):
        """Imweb official source must be included in IMWEB_COMMERCE_SOURCES."""
        self.assertIn("imweb", IMWEB_COMMERCE_SOURCES)
        self.assertIn("ga4_self_store", IMWEB_COMMERCE_SOURCES)
        self.assertIn("naver_commerce", IMWEB_COMMERCE_SOURCES)

    def test_legacy_source_systems_unchanged(self):
        """Legacy SOURCE_SYSTEMS tuple preserved for backward compatibility."""
        self.assertEqual(SOURCE_SYSTEMS, ("ga4_self_store", "naver_commerce"))

    def test_balancy_cost_applies_to_both_imweb_and_ga4(self):
        """Balancy /4 division must apply for both imweb and ga4_self_store."""
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("in ('ga4_self_store', 'imweb')", source)

    def test_item_allocation_includes_imweb_source(self):
        """Item allocation diagnostic must cover both imweb and ga4_self_store."""
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("in ('ga4_self_store', 'imweb')", source)

    def test_both_sources_fail_closed_check_exists(self):
        rows=[{'d':9,'source_system':s} for s in ('imweb','ga4_self_store')]
        with self.assertRaisesRegex(SystemExit,'IMWEB_CANONICAL_SOURCE_SET_MISMATCH'):
            select_commerce_sources(rows,'2026-09-09',True)

    def test_partial_imweb_day_cannot_take_over(self):
        with self.assertRaises(SystemExit):
            select_commerce_sources([{'d':9,'source_system':'imweb'}],'2026-09-09',False)

    def test_historical_physical_pairs_retain_prior_source(self):
        rows=[{'d':1,'source_system':s} for s in ('imweb','ga4_self_store')]
        rows.append({'d':9,'source_system':'imweb'})
        self.assertEqual(select_commerce_sources(rows,'2026-09-09',True),
                         {1:'ga4_self_store',9:'imweb'})

    def test_no_explicit_commerce_date_keeps_legacy_selection(self):
        self.assertEqual(select_commerce_sources([{'d':1,'source_system':'ga4_self_store'}],None,False),
                         {1:'ga4_self_store'})

    def test_subsequent_run_retains_verified_adopted_commerce_day(self):
        rows=[{'d':9,'source_system':'imweb'},{'d':10,'source_system':'imweb'}]
        self.assertEqual(select_commerce_sources(rows,'2026-09-10',{9:True,10:True},{9}),{9:'imweb',10:'imweb'})
        with self.assertRaises(SystemExit):
            select_commerce_sources(rows,'2026-09-10',{9:False,10:True},{9})

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
