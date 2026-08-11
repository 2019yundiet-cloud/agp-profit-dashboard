#!/usr/bin/env python3
"""AGP 순이익 대시보드 일별 드릴다운(dailyDetailByMonth) 생성기.

Supabase에서 월 단위 일별 상세(손익 분해·채널·제품 카테고리)를 조회해
index.html의 `const dailyDetailByMonth = ...;` 라인을 갱신한다.

원천:
  - mart_daily_profit_gauge_source  : 결제액·공헌이익·광고비 분해·배송비
  - imweb_profit_daily_summary      : 자사몰 수수료(4%)·원가·원가 미등록 마커
  - vw_naver_commerce_profit_daily  : 네이버 원가 (수수료는 결제액 6.8%로 산출)
  - fact_order                      : 채널별 주문·구매자·신규/재구매·결제액
  - stg_ezadmin_order_match         : 실제 출고 SKU·수량
  - stg_cost_master_sku             : 판매일 기준 SKU 원가

검증: 채널별 contrib == pay - fee - dfee - cogs (±2원). 불일치 시 실패 종료.

타임존 규약: 웨어하우스(fact_order.paid_datetime)는 날짜 정오(KST)로 정규화 저장되고
vw_naver_commerce_profit_daily도 UTC `::date` 절단을 쓰므로 이 스크립트도 같은 규약을 따른다.
파이프라인이 실제 시각을 저장하도록 바뀌면 뷰와 함께 KST 변환으로 일괄 수정할 것.

사용:
  DATABASE_URL=... python3 tools/build_daily_detail.py --month 2026-07 [--html index.html] [--dry-run]
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2

NAVER_FEE_RATE = 0.068
TAXONOMY_REVIEW_CATEGORY = "분류확인필요"
CATEGORY_ORDER = [
    "단백밥",
    "소스",
    "직화제육",
    "불고기",
    "쌈장제육",
    "닭가슴살",
    "함박스테이크",
    "밸런시",
    "기타",
    "부가옵션",
    TAXONOMY_REVIEW_CATEGORY,
    "미매칭 추정",
]
SOURCE_SYSTEMS = ("ga4_self_store", "naver_commerce")
BALANCY_SET_COST_SKUS = (
    "밸런시 마라 280g",
    "밸런시 시그니처 280g",
    "밸런시 커리 280g",
    "밸런시 토마토 280g",
)
DEFAULT_IMWEB_ARTIFACT_DIR = Path(
    "/Users/junho/Documents/codex/data/imweb_profit/artifacts"
)

# 출고 SKU의 실제 제품명을 우선한다. 단백밥/도시락 맛 이름은 개별 육류
# 카테고리로 분리하지 않고 세트 카테고리인 단백밥으로 유지한다.
CATEGORY_CASE_SQL = """
    case
      when nm like '%아이스팩%' or nm like '%드라이아이스%' or nm like '%공동현관%' or nm like '%배송메모%' or nm like '%1회 배송%' or nm like '%배송방법%' then '부가옵션'
      when nm like '%밸런시%' or nm like '%곡물볶음밥%' then '밸런시'
      when nm like '%소스%' or nm like '%드레싱%' then '소스'
      when nm like '%단백밥%' or nm like '%담백밥%' or nm like '%도시락%' or nm like '%단백질 50g%' or nm like '%단백질50g%' then '단백밥'
      when (nm like '%직화제육%' or nm like '%제육볶음%' or nm like '%저당 제육%' or nm like '%저당제육%') then '직화제육'
      when (nm like '%간장불고기%' or nm like '%저당불고기%' or nm like '%저당 불고기%' or nm like '%불고기%') then '불고기'
      when nm like '%쌈장제육%' then '쌈장제육'
      when (nm like '%함박스테이크%' or nm like '%저당함박%' or nm like '%저당 함박%') then '함박스테이크'
      when nm like '%순수단백%' then '분류확인필요'
      when nm like '%닭가슴살%' and nm not like '%도시락%' and nm not like '%단백밥%' then '닭가슴살'
      else '기타'
    end
"""


def fetch_all(cur, sql, params):
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def classify_sku_name(name):
    nm = (name or "").lower().replace(" ", "")
    if any(token in nm for token in ("아이스팩", "드라이아이스", "공동현관", "배송메모", "1회배송", "배송방법")):
        return "부가옵션"
    if "밸런시" in nm or "곡물볶음밥" in nm:
        return "밸런시"
    if "소스" in nm or "드레싱" in nm:
        return "소스"
    if any(token in nm for token in ("단백밥", "담백밥", "도시락", "단백질50g")):
        return "단백밥"
    if any(token in nm for token in ("직화제육", "제육볶음", "저당제육")):
        return "직화제육"
    if any(token in nm for token in ("간장불고기", "저당불고기", "불고기")):
        return "불고기"
    if "쌈장제육" in nm:
        return "쌈장제육"
    if any(token in nm for token in ("함박스테이크", "저당함박")):
        return "함박스테이크"
    if "닭가슴살" in nm:
        return "닭가슴살"
    if "순수단백" in nm:
        return TAXONOMY_REVIEW_CATEGORY
    return "기타"


def safe_buyer_key(order):
    raw = (
        order.get("customer_phone")
        or order.get("customer_name")
        or order.get("order_no")
        or order.get("ga4_transaction_id")
        or ""
    )
    return hashlib.sha256(str(raw).strip().encode("utf-8")).hexdigest()


def normalize_rounding_residual(revenue, cogs):
    """Clamp only the documented allocation rounding tolerance to zero."""
    revenue = int(revenue)
    cogs = int(cogs)
    return (
        0 if abs(revenue) <= 6 else revenue,
        0 if abs(cogs) <= 2 else cogs,
    )


def latest_dashboard_basis(text, fallback):
    match = re.search(
        r"const dailyRowsByMonth = (\{.*?\});\n\s*const ",
        text,
        re.DOTALL,
    )
    if not match:
        return fallback
    try:
        rows_by_month = json.loads(match.group(1))
    except json.JSONDecodeError:
        return fallback
    dates = [
        f"{month}-{int(row['day']):02d}"
        for month, rows in rows_by_month.items()
        for row in rows
        if row.get("day") is not None
    ]
    return max(dates, default=fallback)


def load_self_store_artifact_days(month, artifact_dir):
    """Load only packlist-verified, aggregate-safe self-store category rows."""
    artifact_dir = Path(artifact_dir)
    days = {}
    issues = {}
    for day_dir in sorted(artifact_dir.glob(f"{month}-??")):
        matching_path = day_dir / "ez_matching.json"
        profit_path = day_dir / "ga4_profit.json"
        if not matching_path.is_file() or not profit_path.is_file():
            continue
        matching = json.loads(matching_path.read_text(encoding="utf-8"))
        profit = json.loads(profit_path.read_text(encoding="utf-8"))
        summary = profit.get("summary") or {}
        report_date = str(summary.get("date") or day_dir.name)
        stats = matching.get("stats") or {}
        violations = []
        if matching.get("matching_mode") != "ezadmin_packlist_only":
            violations.append("matching_mode")
        if int(stats.get("by_imweb_items") or 0) != 0:
            violations.append("by_imweb_items")
        if int(stats.get("by_ezadmin_packlist") or 0) != int(stats.get("matched") or 0):
            violations.append("packlist_count")
        if float(summary.get("cost_coverage_rate") or 0) < 0.90:
            violations.append("cost_coverage_rate")
        if violations:
            issues[report_date] = f"artifact_contract_failed:{','.join(violations)}"
            continue

        category_rows = {}
        matched_revenue = 0
        matched_cogs = 0
        matched_orders = 0
        unmatched_cogs = 0
        for order in profit.get("orders") or []:
            items = order.get("sku_profitability") or []
            if order.get("match_status") != "완전매칭" or not items:
                unmatched_cogs += int(round(float(order.get("sku_cost") or 0)))
                continue
            matched_orders += 1
            buyer_key = safe_buyer_key(order)
            for item in items:
                category = classify_sku_name(item.get("sku"))
                row = category_rows.setdefault(
                    category,
                    {"qty": 0, "buyers": set(), "amt": 0, "cogs": 0},
                )
                qty = int(round(float(item.get("qty") or 0)))
                revenue = int(round(float(item.get("revenue_allocated") or 0)))
                cogs = int(round(float(item.get("total_cost") or 0)))
                row["qty"] += qty
                row["buyers"].add(buyer_key)
                row["amt"] += revenue
                row["cogs"] += cogs
                matched_revenue += revenue
                matched_cogs += cogs

        expected_revenue = int(round(float(summary.get("matched_revenue") or 0)))
        total_cogs = int(round(float(summary.get("total_sku_cost") or 0)))
        expected_matched_cogs = total_cogs - unmatched_cogs
        if (
            abs(matched_revenue - expected_revenue) > 2
            or abs(matched_cogs - expected_matched_cogs) > 2
        ):
            issues[report_date] = (
                "artifact_total_mismatch:"
                f"revenue={matched_revenue}/{expected_revenue},"
                f"cogs={matched_cogs}/{expected_matched_cogs}"
            )
            continue

        safe_rows = []
        fingerprint_rows = []
        for category, row in category_rows.items():
            safe_rows.append(
                {
                    "ch": "i",
                    "category": category,
                    "qty": row["qty"],
                    "buyers": len(row["buyers"]),
                    "amt": row["amt"],
                    "cogs": row["cogs"],
                }
            )
            fingerprint_rows.append(
                [category, row["qty"], len(row["buyers"]), row["amt"], row["cogs"]]
            )
        fingerprint_payload = {
            "report_date": report_date,
            "matching_mode": matching.get("matching_mode"),
            "matched_orders": matched_orders,
            "matched_revenue": matched_revenue,
            "matched_cogs": matched_cogs,
            "unmatched_cogs": unmatched_cogs,
            "rows": sorted(fingerprint_rows),
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        days[report_date] = {
            "rows": safe_rows,
            "total_revenue": int(round(float(summary.get("total_revenue") or 0))),
            "matched_revenue": expected_revenue,
            "unmatched_revenue": int(round(float(summary.get("unmatched_revenue") or 0))),
            "total_cogs": total_cogs,
            "unmatched_cogs": unmatched_cogs,
            "matched_orders": matched_orders,
            "total_orders": int(summary.get("total_orders") or 0),
            "fingerprint": fingerprint,
        }
    return days, issues


def build_month_detail(
    conn,
    month,
    artifact_dir=DEFAULT_IMWEB_ARTIFACT_DIR,
    required_self_store_category_date=None,
):
    start = f"{month}-01"
    end_sql = "(%s::date + interval '1 month')::date"
    cur = conn.cursor()

    gauge = fetch_all(cur, f"""
        select extract(day from report_date)::int d,
               round(meta_ad_spend)::bigint meta, round(google_ads_spend)::bigint google,
               round(naver_searchad_spend)::bigint nsa,
               round(imweb_delivery_fee)::bigint idf, round(naver_delivery_fee)::bigint ndf,
               round(imweb_payment_amount)::bigint ipay, round(naver_payment_amount)::bigint npay,
               round(imweb_contribution)::bigint ic, round(naver_contribution)::bigint nc
        from mart_daily_profit_gauge_source
        where report_date >= %s and report_date < {end_sql}
        order by report_date
    """, (start, start))

    imweb = {r["d"]: r for r in fetch_all(cur, f"""
        select extract(day from date_key::date)::int d, round(channel_fee)::bigint fee,
               round(total_cost)::bigint cogs,
               (sku_detail_text like '%%원가 미등록%%') cost_gap,
               coalesce((raw_row->'july_2026_reconciliation'->>'unclassified_shipment_revenue_inferred')::boolean, true) = false
                 api_reconciled_without_unclassified_revenue
        from imweb_profit_daily_summary
        where date_key::date >= %s and date_key::date < {end_sql} and source = 'ga4'
    """, (start, start))}

    naver = {r["d"]: r for r in fetch_all(cur, f"""
        select extract(day from report_date)::int d, round(cogs)::bigint cogs
        from vw_naver_commerce_profit_daily
        where report_date >= %s and report_date < {end_sql}
    """, (start, start))}

    stats = fetch_all(cur, f"""
        select extract(day from fo.paid_datetime)::int d,
               case when fo.source_system = 'naver_commerce' then 'n' else 'i' end ch,
               count(distinct fo.internal_order_id)::int orders,
               count(distinct fo.internal_customer_id)::int buyers,
               count(distinct fo.internal_customer_id) filter (where fo.is_first_order)::int first,
               count(distinct fo.internal_customer_id) filter (where fo.is_repeat_order)::int repeat
        from fact_order fo
        where fo.is_valid_purchase and fo.paid_datetime >= %s and fo.paid_datetime < {end_sql}
        group by 1, 2
    """, (start, start))
    stat_map = {(r["d"], r["ch"]): r for r in stats}

    cat_sql = CATEGORY_CASE_SQL.replace("%", "%%")  # psycopg2 paramstyle에서 LIKE % 이스케이프
    products = fetch_all(cur, f"""
        with costed_matching as (
          select extract(day from fo.paid_datetime)::int d,
                 case when fo.source_system = 'naver_commerce' then 'n' else 'i' end ch,
                 fo.source_system, fo.source_order_id, fo.internal_customer_id,
                 coalesce(fo.net_payment_amount, fo.payment_amount, 0)::numeric order_revenue,
                 sem.matched_sku_name nm, sem.matched_qty::numeric qty,
                 case
                   when sem.source_system = 'ga4_self_store'
                    and sem.matched_sku_name = any(%s)
                   then coalesce(cm.cogs, 0) / 4
                   else coalesce(cm.cogs, 0)
                 end::numeric unit_cost
          from stg_ezadmin_order_match sem
          join fact_order fo
            on fo.source_system = sem.source_system
           and fo.source_order_id = sem.source_order_id
          left join lateral (
            select cost.cogs
            from stg_cost_master_sku cost
            where cost.normalized_sku_name = regexp_replace(
                      lower(sem.matched_sku_name), '[^a-z0-9가-힣]+', '', 'g'
                  )
              and coalesce(cost.effective_start_date, date '1900-01-01') <= fo.paid_datetime::date
            order by coalesce(cost.effective_start_date, date '1900-01-01') desc
            limit 1
          ) cm on true
          where fo.is_valid_purchase
            and sem.source_system = any(%s)
            and sem.match_status = 'matched'
            and coalesce(sem.matched_sku_name, '') <> ''
            and sem.matched_qty > 0
            and sem.report_date >= %s and sem.report_date < {end_sql}
            and fo.paid_datetime >= %s and fo.paid_datetime < {end_sql}
        ), order_lines as (
          select d, ch, source_system, source_order_id, internal_customer_id,
                 order_revenue, nm, sum(qty)::numeric qty, max(unit_cost)::numeric unit_cost
          from costed_matching
          group by 1, 2, 3, 4, 5, 6, 7
        ), weighted as (
          select *, qty * unit_cost as line_cogs,
                 sum(qty * unit_cost) over (partition by source_system, source_order_id) as order_cogs
          from order_lines
        ), items as (
          select d, ch, internal_customer_id, nm, qty, line_cogs cogs,
                 case when order_cogs > 0 then order_revenue * line_cogs / order_cogs else 0 end amt
          from weighted
        )
        select d, ch, {cat_sql} category,
               sum(qty)::int qty, count(distinct internal_customer_id)::int buyers,
               round(sum(amt))::bigint amt, round(sum(cogs))::bigint cogs
        from items group by 1, 2, 3
    """, (list(BALANCY_SET_COST_SKUS), list(SOURCE_SYSTEMS), start, start, start, start))

    # 자사몰 fact_order_item 배분 규약 감지 (품목표 원천 아님 — 경고 노트 전용).
    # 웨어하우스 전체 리빌드는 수량비례(source_order_item_id null), 자사몰 일별수익
    # 파이프라인은 아이템 피드 실금액(orderno:idx)으로 같은 테이블을 날짜 단위로
    # 덮어써서, 마지막 기록자에 따라 품목 단가가 날짜별로 플립된다(소스 40g ~3,500원 ↔ ~500원).
    item_allocation = {r["d"]: r for r in fetch_all(cur, f"""
        select extract(day from fo.paid_datetime)::int d,
               count(*) filter (where foi.source_order_item_id is null)::int qty_share_rows,
               count(*) filter (where foi.source_order_item_id is not null)::int feed_rows
        from fact_order_item foi
        join fact_order fo on fo.internal_order_id = foi.internal_order_id
        where fo.source_system = 'ga4_self_store'
          and fo.paid_datetime >= %s and fo.paid_datetime < {end_sql}
        group by 1
    """, (start, start))}

    artifact_days, artifact_issues = load_self_store_artifact_days(month, artifact_dir)
    out = {}
    errors = []
    used_artifact_dates = set()
    for g in gauge:
        d = g["d"]
        report_date = f"{month}-{d:02d}"
        iw, nv = imweb.get(d), naver.get(d)
        if not iw or nv is None:
            errors.append(f"{month}-{d:02d}: 자사몰/네이버 요약 행 누락 (imweb={bool(iw)}, naver={nv is not None})")
            continue
        n_fee = round(g["npay"] * NAVER_FEE_RATE)

        day_products = [p for p in products if p["d"] == d]
        artifact_note = ""
        artifact = artifact_days.get(report_date)
        if artifact:
            if (
                artifact["total_revenue"] == g["ipay"]
                and artifact["total_cogs"] == iw["cogs"]
            ):
                day_products = [p for p in day_products if p["ch"] != "i"]
                day_products.extend({"d": d, **row} for row in artifact["rows"])
                used_artifact_dates.add(report_date)
                artifact_note = (
                    "자사몰 상세는 최종 이지어드민 출고 패킹리스트 산출물로 분류했습니다 "
                    f"(매출 반영률 {artifact['matched_revenue'] / max(artifact['total_revenue'], 1) * 100:.1f}%, "
                    f"스냅샷 {artifact['fingerprint'][:12]})."
                )
            else:
                artifact_note = (
                    "자사몰 상세 산출물과 공식 일별 손익의 스냅샷이 달라 "
                    "DB 출고 상세만 사용했습니다."
                )
        elif report_date in artifact_issues:
            artifact_note = "자사몰 상세 산출물 계약 검증이 실패해 DB 출고 상세만 사용했습니다."

        cat_map = {}
        for p in day_products:
            c = cat_map.setdefault(p["category"], {"qty": 0, "buyers": 0, "amt": 0, "cogs": 0, "iAmt": 0, "nAmt": 0})
            c["qty"] += p["qty"]
            c["buyers"] += p["buyers"]
            c["amt"] += p["amt"]
            c["cogs"] += p["cogs"]
            c["iAmt" if p["ch"] == "i" else "nAmt"] += p["amt"]

        # GA4 손익은 90% 이상 매칭을 허용하고 미매칭 주문에는 보수적인 추정원가를
        # 반영한다. 출고표에 없는 주문을 임의 SKU로 만들지 않고, 공식 채널 손익과
        # 출고 SKU 합계의 양수 잔액만 별도 행으로 보존한다.
        imweb_products = [p for p in day_products if p["ch"] == "i"]
        imweb_residual_revenue, imweb_residual_cogs = normalize_rounding_residual(
            g["ipay"] - sum(p["amt"] for p in imweb_products),
            iw["cogs"] - sum(p["cogs"] for p in imweb_products),
        )
        residual_notes = []
        if imweb_residual_revenue or imweb_residual_cogs:
            api_reconciled = bool(iw.get("api_reconciled_without_unclassified_revenue"))
            if (imweb_residual_revenue < 0 or imweb_residual_cogs < 0) and not api_reconciled:
                errors.append(
                    f"{month}-{d:02d} imweb: 미매칭 잔액이 음수 "
                    f"(매출 {imweb_residual_revenue}, 원가 {imweb_residual_cogs})"
                )
            else:
                residual_category = "아임웹 API 추가·보정" if api_reconciled else "미매칭 추정"
                residual = cat_map.setdefault(
                    residual_category,
                    {"qty": 0, "buyers": 0, "amt": 0, "cogs": 0, "iAmt": 0, "nAmt": 0},
                )
                residual["amt"] += imweb_residual_revenue
                residual["cogs"] += imweb_residual_cogs
                residual["iAmt"] += imweb_residual_revenue
                if api_reconciled:
                    residual_notes.append(
                        f"아임웹 API에서 확인된 추가 주문과 현재 결제금액 보정을 별도 행으로 반영했습니다 "
                        f"(자사몰 매출 보정 {imweb_residual_revenue:,}원, 확인 원가 {imweb_residual_cogs:,}원). "
                        "협찬 가능성이 있는 미분류 출고에서는 매출을 추정하지 않았습니다."
                    )
                else:
                    residual_notes.append(
                        f"출고 SKU가 확인되지 않은 주문 잔액을 미매칭 추정으로 분리했습니다 "
                        f"(자사몰 매출 {imweb_residual_revenue:,}원, 추정원가 {imweb_residual_cogs:,}원)."
                    )

        # 네이버도 90% 이상 매칭을 허용한다. 출고표에 없는 주문을 임의 SKU로
        # 만들지 않고 공식 채널 합계와 출고 SKU 합계의 양수 잔액만 분리한다.
        naver_products = [p for p in day_products if p["ch"] == "n"]
        naver_residual_revenue, naver_residual_cogs = normalize_rounding_residual(
            g["npay"] - sum(p["amt"] for p in naver_products),
            nv["cogs"] - sum(p["cogs"] for p in naver_products),
        )
        if naver_residual_revenue or naver_residual_cogs:
            if naver_residual_revenue < 0 or naver_residual_cogs < 0:
                errors.append(
                    f"{month}-{d:02d} naver: 미매칭 잔액이 음수 "
                    f"(매출 {naver_residual_revenue}, 원가 {naver_residual_cogs})"
                )
            else:
                residual = cat_map.setdefault(
                    "미매칭 추정",
                    {"qty": 0, "buyers": 0, "amt": 0, "cogs": 0, "iAmt": 0, "nAmt": 0},
                )
                residual["amt"] += naver_residual_revenue
                residual["cogs"] += naver_residual_cogs
                residual["nAmt"] += naver_residual_revenue
                residual_notes.append(
                    f"출고 SKU가 확인되지 않은 주문 잔액을 미매칭 추정으로 분리했습니다 "
                    f"(네이버 매출 {naver_residual_revenue:,}원, 추정원가 {naver_residual_cogs:,}원)."
                )
        cats = sorted(cat_map.items(), key=lambda kv: CATEGORY_ORDER.index(kv[0]) if kv[0] in CATEGORY_ORDER else 99)

        # 데이터 품질 노트
        notes = []
        if iw.get("cost_gap"):
            notes.append("일부 SKU가 원가 미등록 상태로 계산돼 품목 원가가 과소 표시될 수 있습니다.")
        notes.extend(residual_notes)
        if artifact_note:
            notes.append(artifact_note)
        alloc = item_allocation.get(d)
        if alloc and alloc["feed_rows"] > 0:
            if alloc["qty_share_rows"] > 0:
                notes.append(
                    "자사몰 fact_order_item 품목 금액이 이날은 수량비례 배분과 아이템 피드 실금액 두 규약이 "
                    "혼재돼 품목 단가 분석에 사용할 수 없습니다. 이 품목표는 출고 SKU 원가비례 배분이라 영향 없습니다."
                )
            else:
                notes.append(
                    "자사몰 fact_order_item 품목 금액이 이날은 아이템 피드 실금액 규약으로 적재돼, 수량비례 배분 "
                    "규약인 날짜와 품목 단가를 비교할 수 없습니다(예: 소스 40g ~500원 ↔ ~3,500원 플립). "
                    "이 품목표는 출고 SKU 원가비례 배분이라 영향 없습니다."
                )

        detail = {
            "imweb": {"pay": g["ipay"], "fee": iw["fee"], "dfee": g["idf"], "cogs": iw["cogs"], "contrib": g["ic"],
                      **{k: stat_map.get((d, "i"), {}).get(k, 0) for k in ("orders", "buyers", "first", "repeat")}},
            "naver": {"pay": g["npay"], "fee": n_fee, "dfee": g["ndf"], "cogs": nv["cogs"], "contrib": g["nc"],
                      **{k: stat_map.get((d, "n"), {}).get(k, 0) for k in ("orders", "buyers", "first", "repeat")}},
            "ads": {"meta": g["meta"], "google": g["google"], "naver": g["nsa"]},
            "products": [[name, c["qty"], c["buyers"], c["amt"], c["cogs"], c["iAmt"], c["nAmt"]] for name, c in cats],
            "notes": notes,
        }

        for ch_name in ("imweb", "naver"):
            ch = detail[ch_name]
            calc = ch["pay"] - ch["fee"] - ch["dfee"] - ch["cogs"]
            if abs(calc - ch["contrib"]) > 2:
                errors.append(f"{month}-{d:02d} {ch_name}: 검증 실패 계산 {calc} != 공헌이익 {ch['contrib']}")
        for product_ch, official_ch, label in (("i", detail["imweb"], "imweb"), ("n", detail["naver"], "naver")):
            channel_products = [p for p in day_products if p["ch"] == product_ch]
            product_revenue = sum(p["amt"] for p in channel_products)
            product_cogs = sum(p["cogs"] for p in channel_products)
            if product_ch == "i" and (imweb_residual_revenue or imweb_residual_cogs):
                product_revenue += imweb_residual_revenue
                product_cogs += imweb_residual_cogs
            if product_ch == "n" and (naver_residual_revenue or naver_residual_cogs):
                product_revenue += naver_residual_revenue
                product_cogs += naver_residual_cogs
            if abs(product_revenue - official_ch["pay"]) > 6:
                errors.append(
                    f"{month}-{d:02d} {label}: 출고 SKU 배부매출 {product_revenue} != 결제액 {official_ch['pay']}"
                )
            if abs(product_cogs - official_ch["cogs"]) > 2:
                errors.append(
                    f"{month}-{d:02d} {label}: 출고 SKU 원가 {product_cogs} != 공식 채널원가 {official_ch['cogs']}"
                )
        out[str(d)] = detail

    if required_self_store_category_date:
        if required_self_store_category_date not in used_artifact_dates:
            errors.append(
                f"{required_self_store_category_date}: 검증된 자사몰 카테고리 산출물을 사용하지 못했습니다"
            )
        else:
            required_day = out.get(str(int(required_self_store_category_date[-2:]))) or {}
            imweb_pay = int((required_day.get("imweb") or {}).get("pay") or 0)
            unmatched_self_revenue = sum(
                int(row[5] or 0)
                for row in required_day.get("products") or []
                if row[0] == "미매칭 추정"
            )
            taxonomy_review_revenue = sum(
                int(row[5] or 0)
                for row in required_day.get("products") or []
                if row[0] == TAXONOMY_REVIEW_CATEGORY
            )
            if unmatched_self_revenue > max(6, round(imweb_pay * 0.10)):
                errors.append(
                    f"{required_self_store_category_date}: 자사몰 미분류 매출이 10%를 초과합니다 "
                    f"({unmatched_self_revenue}/{imweb_pay})"
                )
            if taxonomy_review_revenue > 0:
                errors.append(
                    f"{required_self_store_category_date}: 제품 세부 분류 확인이 필요한 자사몰 매출이 있습니다 "
                    f"({taxonomy_review_revenue})"
                )
    if errors:
        for e in errors:
            print(f"[VERIFY-FAIL] {e}", file=sys.stderr)
        raise SystemExit(1)
    snapshot_payload = {
        "month": month,
        "detail": out,
        "used_self_store_artifact_dates": sorted(used_artifact_dates),
    }
    snapshot_id = hashlib.sha256(
        json.dumps(
            snapshot_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return out, snapshot_id


def update_html(html_path, month, month_detail, snapshot_id, dry_run):
    text = html_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^(\s*)const dailyDetailByMonth = (.*);$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"index.html에서 `const dailyDetailByMonth = ...;` 라인을 찾지 못했습니다: {html_path}")
    existing = json.loads(match.group(2))
    existing[month] = month_detail
    replacement = f"{match.group(1)}const dailyDetailByMonth = {json.dumps(existing, ensure_ascii=False, separators=(',', ':'))};"
    updated = text[:match.start()] + replacement + text[match.end():]
    target_basis_date = f"{month}-{max(int(day) for day in month_detail):02d}"
    basis_date = latest_dashboard_basis(updated, target_basis_date)
    generated_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    updated, generated_count = re.subn(
        r'(<meta name="data-generated-at" content=")[^"]*(">)',
        rf"\g<1>{generated_at}\2",
        updated,
        count=1,
    )
    updated, basis_count = re.subn(
        r'(<meta name="data-basis-date" content=")[^"]*(">)',
        rf"\g<1>{basis_date}\2",
        updated,
        count=1,
    )
    snapshot_meta = f'  <meta name="source-snapshot-id" content="{snapshot_id}">'
    if 'meta name="source-snapshot-id"' in updated:
        updated = re.sub(
            r'  <meta name="source-snapshot-id" content="[^"]*">',
            snapshot_meta,
            updated,
            count=1,
        )
    else:
        updated, snapshot_count = re.subn(
            r'(?m)^(\s*)(<meta name="data-basis-date")',
            lambda match: (
                f'{match.group(1)}<meta name="source-snapshot-id" content="{snapshot_id}">\n'
                f"{match.group(1)}{match.group(2)}"
            ),
            updated,
            count=1,
        )
        if not snapshot_count:
            raise SystemExit("대시보드 원천 스냅샷 메타 삽입 실패")
    if not generated_count or not basis_count:
        raise SystemExit("대시보드 생성시각/기준일 메타 갱신 실패")
    if dry_run:
        print(
            f"[dry-run] {month}: {len(month_detail)}일 상세 준비됨, HTML 미수정 "
            f"(basis={basis_date}, snapshot={snapshot_id})"
        )
        return
    html_path.write_text(updated, encoding="utf-8")
    print(
        f"updated {html_path} — {month}: {len(month_detail)}일 상세 "
        f"(basis={basis_date}, snapshot={snapshot_id})"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--html", default=str(Path(__file__).resolve().parent.parent / "index.html"))
    parser.add_argument(
        "--imweb-artifact-dir",
        default=str(DEFAULT_IMWEB_ARTIFACT_DIR),
    )
    parser.add_argument(
        "--require-self-store-category-date",
        help="이 날짜의 자사몰 상세가 검증된 packlist 산출물로 분류되지 않으면 실패",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"\d{4}-\d{2}", args.month):
        raise SystemExit("--month 형식은 YYYY-MM 입니다")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL 환경변수가 필요합니다")

    conn = psycopg2.connect(database_url)
    try:
        month_detail, snapshot_id = build_month_detail(
            conn,
            args.month,
            artifact_dir=args.imweb_artifact_dir,
            required_self_store_category_date=args.require_self_store_category_date,
        )
    finally:
        conn.close()
    if not month_detail:
        raise SystemExit(f"{args.month}에 게이지 행이 없습니다")
    update_html(Path(args.html), args.month, month_detail, snapshot_id, args.dry_run)


if __name__ == "__main__":
    main()
