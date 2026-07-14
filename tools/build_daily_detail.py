#!/usr/bin/env python3
"""AGP 순이익 대시보드 일별 드릴다운(dailyDetailByMonth) 생성기.

Supabase에서 월 단위 일별 상세(손익 분해·채널·제품 카테고리)를 조회해
index.html의 `const dailyDetailByMonth = ...;` 라인을 갱신한다.

원천:
  - mart_daily_profit_gauge_source  : 결제액·공헌이익·광고비 분해·배송비
  - imweb_profit_daily_summary      : 자사몰 수수료(4%)·원가·원가 미등록 마커
  - vw_naver_commerce_profit_daily  : 네이버 원가 (수수료는 결제액 6.8%로 산출)
  - fact_order                      : 채널별 주문·구매자·신규/재구매
  - fact_order_item                 : 제품 카테고리별 수량·구매자·금액·원가

검증: 채널별 contrib == pay - fee - dfee - cogs (±2원). 불일치 시 실패 종료.

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

# build_category_profit_dashboard.py의 매핑과 동일한 우선순위 (부가옵션 → 밸런시 → 소스 → 함박 → 순수단백 → 닭가슴살 → 단백밥 → 기타)
CATEGORY_CASE_SQL = """
    case
      when nm like '%아이스팩%' or nm like '%공동현관%' or nm like '%배송메모%' or nm like '%1회 배송%' or nm like '%배송방법%' then '부가옵션'
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
        with items as (
          select extract(day from fo.paid_datetime)::int d,
                 case when fo.source_system = 'naver_commerce' then 'n' else 'i' end ch,
                 fo.internal_customer_id,
                 concat_ws(' ', coalesce(oi.product_name_raw, ''), coalesce(oi.option_name_raw, '')) nm,
                 oi.qty, coalesce(oi.item_net_amount, 0) amt, coalesce(oi.item_cogs_amount, 0) cogs
          from fact_order fo join fact_order_item oi on oi.internal_order_id = fo.internal_order_id
          where fo.is_valid_purchase and fo.paid_datetime >= %s and fo.paid_datetime < {end_sql}
        )
        select d, ch, {cat_sql} category,
               sum(qty)::int qty, count(distinct internal_customer_id)::int buyers,
               round(sum(amt))::bigint amt, round(sum(cogs))::bigint cogs
        from items group by 1, 2, 3
    """, (start, start))

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
        imweb_items = [p for p in products if p["d"] == d and p["ch"] == "i"]
        imweb_item_amt = sum(p["amt"] for p in imweb_items)
        imweb_item_cogs = sum(p["cogs"] for p in imweb_items)
        if g["ipay"] and (imweb_item_cogs == 0 or abs(imweb_item_amt - g["ipay"]) > g["ipay"] * 0.05):
            notes.append("자사몰 품목별 금액·수량은 이 날 아이템 피드 이슈로 참고용입니다 (채널 합계·원가·순이익은 이지어드민 매칭 기준으로 정확).")
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
