import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_daily_detail import (
    classify_sku_name,
    load_self_store_artifact_days,
    update_html,
)


class SelfStoreCategoryArtifactTests(unittest.TestCase):
    def test_category_taxonomy(self):
        self.assertEqual(classify_sku_name("윤식단 단백밥 오리지널 [L]"), "단백밥")
        self.assertEqual(classify_sku_name("데리야끼 소스 40g"), "소스")
        self.assertEqual(classify_sku_name("윤식단 순수단백 저당 제육볶음 100g"), "순수단백")
        self.assertEqual(classify_sku_name("윤식단 닭가슴살 150g"), "닭가슴살")
        self.assertEqual(classify_sku_name("윤식단 함박스테이크 150g"), "함박스테이크")
        self.assertEqual(classify_sku_name("밸런시 마라 280g"), "밸런시")
        self.assertEqual(classify_sku_name("아이스팩 추가"), "부가옵션")

    def test_packlist_artifact_is_aggregated_without_pii(self):
        with tempfile.TemporaryDirectory() as tmp:
            day_dir = Path(tmp) / "2026-07-27"
            day_dir.mkdir()
            (day_dir / "ez_matching.json").write_text(
                json.dumps(
                    {
                        "matching_mode": "ezadmin_packlist_only",
                        "stats": {
                            "matched": 1,
                            "by_ezadmin_packlist": 1,
                            "by_imweb_items": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (day_dir / "ga4_profit.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "date": "2026-07-27",
                            "total_orders": 1,
                            "total_revenue": 20000,
                            "matched_revenue": 20000,
                            "unmatched_revenue": 0,
                            "total_sku_cost": 10000,
                            "cost_coverage_rate": 1.0,
                        },
                        "orders": [
                            {
                                "match_status": "완전매칭",
                                "customer_phone": "01000000000",
                                "customer_name": "테스트고객",
                                "sku_profitability": [
                                    {
                                        "sku": "윤식단 단백밥 오리지널 [L]",
                                        "qty": 2,
                                        "revenue_allocated": 15000,
                                        "total_cost": 7500,
                                    },
                                    {
                                        "sku": "데리야끼 소스 40g",
                                        "qty": 2,
                                        "revenue_allocated": 5000,
                                        "total_cost": 2500,
                                    },
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            days, issues = load_self_store_artifact_days("2026-07", tmp)

        self.assertEqual(issues, {})
        result = days["2026-07-27"]
        self.assertEqual(result["matched_revenue"], 20000)
        self.assertEqual(result["total_cogs"], 10000)
        self.assertEqual({row["category"] for row in result["rows"]}, {"단백밥", "소스"})
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("01000000000", serialized)
        self.assertNotIn("테스트고객", serialized)

    def test_non_packlist_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            day_dir = Path(tmp) / "2026-07-27"
            day_dir.mkdir()
            (day_dir / "ez_matching.json").write_text(
                json.dumps(
                    {
                        "matching_mode": "legacy",
                        "stats": {
                            "matched": 1,
                            "by_ezadmin_packlist": 0,
                            "by_imweb_items": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (day_dir / "ga4_profit.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "date": "2026-07-27",
                            "cost_coverage_rate": 1.0,
                        },
                        "orders": [],
                    }
                ),
                encoding="utf-8",
            )
            days, issues = load_self_store_artifact_days("2026-07", tmp)

        self.assertEqual(days, {})
        self.assertIn("2026-07-27", issues)

    def test_dashboard_metadata_tracks_basis_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "index.html"
            html_path.write_text(
                "\n".join(
                    [
                        '<meta name="data-generated-at" content="old">',
                        '<meta name="data-basis-date" content="old">',
                        "const dailyDetailByMonth = {};",
                    ]
                ),
                encoding="utf-8",
            )
            update_html(
                html_path,
                "2026-07",
                {"27": {"products": [], "notes": []}},
                "a" * 64,
                dry_run=False,
            )
            rendered = html_path.read_text(encoding="utf-8")

        self.assertIn('name="data-basis-date" content="2026-07-27"', rendered)
        self.assertIn('name="source-snapshot-id" content="' + ("a" * 64) + '"', rendered)
        self.assertNotIn('name="data-generated-at" content="old"', rendered)

    def test_naver_unmatched_residual_is_not_assigned_to_a_fake_category(self):
        source = Path(__file__).with_name("build_daily_detail.py").read_text(encoding="utf-8")
        self.assertIn("naver_residual_revenue", source)
        self.assertIn("네이버 매출", source)
        self.assertIn("residual_notes", source)


if __name__ == "__main__":
    unittest.main()
