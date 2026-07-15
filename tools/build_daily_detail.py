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
import json
import os
import re
import sys
from pathlib import Path

import psycopg2

NAVER_FEE_RATE = 0.068
CATEGORY_ORDER = ["단백밥", "소스", "순수단백", "닭가슴살", "함박스테이크", "밸런시", "기타", "부가옵션"]
SOURCE_SYSTEMS = ("ga4_self_store", "naver_commerce")
BALANCY_SET_COST_SKUS = (
    "밸런시 마라 280g",
    "밸런시 시그니처 280g",
    "밸런시 커리 280g",
    "밸런시 토마토 280g",
)

# build_category_profit_dashboard.py의 매핑과 동일한 우선순위 (부가옵션 → 밸런시 → 소스 → 함박 → 순수단백 → 닭가슴살 → 단백밥 → 기타)
CATEGORY_CASE_SQL = """
    case
      when nm like '%아이스팩%' or nm like '%드라이아이스%' or nm like '%공동현관%' or nm like '%배송메모%' or nm like '%1회 배송%' or nm like '%배송방법%' then '부가옵션'
      when nm like '%밸런시%' or nm like '%곡물볶음밥%' then '밸런시'
      when nm like '%소스%' or nm like '%드레싱%' then '소스'
      when nm like '%함박스테이크%' and nm not like '%도시락%' and nm not like '%순수단백%' then '함박스테이크'
      when (nm like '%순수단백%' or nm like '%슬라이스 닭가슴살%' or nm like '%저당함박%' or nm like '%저당 함박%'
            or nm like '%저당불고기%' or nm like '%쌈장제육%' or nm like '%저당 제육%' or nm like '%제육볶음 10팩%'
            or nm like '%간장불고기%') and nm not like '%도시락%' and nm not like '%단백밥%' then '순수단백'
      when nm like '%닭가슴살%' and nm not like '%도시락%' and nm not like '%단백밥%' then '닭가슴살'
      when nm like '%단백밥%' or nm like '%담백밥%' or nm like '%도시락%' or nm like '%단백질 50g%' or nm like '%단백질50g%' then '단백밥'
      else '기타'
    end
"""


def fetch_all(cur, sql, params):
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_month_detail(conn, month):
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
               (sku_detail_text like '%%원가 미등록%%') cost_gap
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

    out = {}
    errors = []
    for g in gauge:
        d = g["d"]
        iw, nv = imweb.get(d), naver.get(d)
        if not iw or nv is None:
            errors.append(f"{month}-{d:02d}: 자사몰/네이버 요약 행 누락 (imweb={bool(iw)}, naver={nv is not None})")
            continue
        n_fee = round(g["npay"] * NAVER_FEE_RATE)

        cat_map = {}
        for p in products:
            if p["d"] != d:
                continue
            c = cat_map.setdefault(p["category"], {"qty": 0, "buyers": 0, "amt": 0, "cogs": 0, "iAmt": 0, "nAmt": 0})
            c["qty"] += p["qty"]
            c["buyers"] += p["buyers"]
            c["amt"] += p["amt"]
            c["cogs"] += p["cogs"]
            c["iAmt" if p["ch"] == "i" else "nAmt"] += p["amt"]
        cats = sorted(cat_map.items(), key=lambda kv: CATEGORY_ORDER.index(kv[0]) if kv[0] in CATEGORY_ORDER else 99)

        # 데이터 품질 노트
        notes = []
        if iw.get("cost_gap"):
            notes.append("일부 SKU가 원가 미등록 상태로 계산돼 품목 원가가 과소 표시될 수 있습니다.")

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
            channel_products = [p for p in products if p["d"] == d and p["ch"] == product_ch]
            product_revenue = sum(p["amt"] for p in channel_products)
            product_cogs = sum(p["cogs"] for p in channel_products)
            if abs(product_revenue - official_ch["pay"]) > 6:
                errors.append(
                    f"{month}-{d:02d} {label}: 출고 SKU 배부매출 {product_revenue} != 결제액 {official_ch['pay']}"
                )
            if abs(product_cogs - official_ch["cogs"]) > 2:
                errors.append(
                    f"{month}-{d:02d} {label}: 출고 SKU 원가 {product_cogs} != 공식 채널원가 {official_ch['cogs']}"
                )
        out[str(d)] = detail

    if errors:
        for e in errors:
            print(f"[VERIFY-FAIL] {e}", file=sys.stderr)
        raise SystemExit(1)
    return out


def update_html(html_path, month, month_detail, dry_run):
    text = html_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^(\s*)const dailyDetailByMonth = (.*);$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"index.html에서 `const dailyDetailByMonth = ...;` 라인을 찾지 못했습니다: {html_path}")
    existing = json.loads(match.group(2))
    existing[month] = month_detail
    replacement = f"{match.group(1)}const dailyDetailByMonth = {json.dumps(existing, ensure_ascii=False, separators=(',', ':'))};"
    if dry_run:
        print(f"[dry-run] {month}: {len(month_detail)}일 상세 준비됨, HTML 미수정")
        return
    html_path.write_text(text[:match.start()] + replacement + text[match.end():], encoding="utf-8")
    print(f"updated {html_path} — {month}: {len(month_detail)}일 상세")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--html", default=str(Path(__file__).resolve().parent.parent / "index.html"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"\d{4}-\d{2}", args.month):
        raise SystemExit("--month 형식은 YYYY-MM 입니다")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL 환경변수가 필요합니다")

    conn = psycopg2.connect(database_url)
    try:
        month_detail = build_month_detail(conn, args.month)
    finally:
        conn.close()
    if not month_detail:
        raise SystemExit(f"{args.month}에 게이지 행이 없습니다")
    update_html(Path(args.html), args.month, month_detail, args.dry_run)


if __name__ == "__main__":
    main()
