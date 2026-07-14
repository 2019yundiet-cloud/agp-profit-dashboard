#!/usr/bin/env python3
"""AGP 순이익 대시보드 일별 행(dailyRowsByMonth) 재생성기.

Supabase `mart_daily_profit_gauge`를 공식 원천으로 해당 월의 일별 행 배열과
monthConfig의 quality/emptyState 문자열을 index.html에 반영한다.

주의: 대상 월 키("YYYY-MM": [...])가 dailyRowsByMonth 안에 이미 존재해야 한다.
새 월은 에이전트가 monthConfig와 함께 월 키를 먼저 추가한 뒤 이 스크립트로 채운다.

사용:
  DATABASE_URL=... python3 tools/build_daily_rows.py --month 2026-07 [--html index.html] [--dry-run]
"""

import argparse
import json
import os
import re
from pathlib import Path

import psycopg2


def num(v):
    f = float(v)
    return int(f) if f == int(f) else round(f, 2)


def fetch_rows(database_url, month):
    conn = psycopg2.connect(database_url)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            select extract(day from report_date)::int,
                   round(total_payment_amount)::bigint, round(imweb_payment_amount)::bigint,
                   round(naver_payment_amount)::bigint,
                   imweb_contribution, naver_contribution,
                   round(meta_ad_spend)::bigint, round(google_ads_spend)::bigint,
                   round(naver_searchad_spend)::bigint,
                   round(imweb_delivery_fee)::bigint, round(naver_delivery_fee)::bigint,
                   round(total_delivery_fee)::bigint,
                   coalesce(meta_ad_spend_source, 'missing'), coalesce(google_ads_spend_source, 'missing'),
                   coalesce(naver_searchad_spend_source, 'missing'), coalesce(data_quality_bucket, 'UNKNOWN')
            from mart_daily_profit_gauge
            where report_date >= %s::date and report_date < (%s::date + interval '1 month')::date
            order by report_date
            """,
            (f"{month}-01", f"{month}-01"),
        )
        return cur.fetchall()
    finally:
        conn.close()


def render_block(month, rows):
    entries = []
    quality_counts = {}
    for (d, pay, ipay, npay, ic, nc, meta, google, nsa, idf, ndf, tdf, ms, gs, ns, q) in rows:
        quality_counts[q] = quality_counts.get(q, 0) + 1
        entry = {
            "day": d, "revenue": num(pay), "selfRevenueApi": num(ipay), "naverRevenueApi": num(npay),
            "imweb": num(ic), "naver": num(nc), "meta": num(meta), "googleAd": num(google),
            "naverAd": num(nsa), "imwebDeliveryFee": num(idf), "naverDeliveryFee": num(ndf),
            "totalDeliveryFee": num(tdf), "metaSource": ms, "googleSource": gs,
            "naverSearchSource": ns, "quality": q,
        }
        lines = ",\n".join(
            f'                              "{k}": {json.dumps(v, ensure_ascii=False)}' for k, v in entry.items()
        )
        entries.append("                    {\n" + lines + "\n                    }")
    block = f'          "{month}": [\n' + ",\n".join(entries) + "\n          ]"
    quality_str = " · ".join(f"{k} {v}일" for k, v in sorted(quality_counts.items(), key=lambda kv: -kv[1]))
    return block, quality_str


def update_html(html_path, month, block, quality_str, last_day, dry_run):
    text = html_path.read_text(encoding="utf-8")

    # dailyRowsByMonth 안의 해당 월 배열 교체 (다음 월 키 또는 객체 끝 직전까지)
    row_pattern = re.compile(
        r'          "' + month + r'": \[.*?\n          \](?=,\n          "|\n\};)', re.DOTALL
    )
    if len(row_pattern.findall(text)) != 1:
        raise SystemExit(f"dailyRowsByMonth에서 {month} 블록을 정확히 1개 찾지 못했습니다 (새 월은 월 키를 먼저 추가)")
    text = row_pattern.sub(lambda m: block, text, count=1)

    # monthConfig quality/emptyState 갱신
    text, n_q = re.subn(
        r'("' + month + r'": \{[^}]*?"quality": ")[^"]*(")',
        lambda m: m.group(1) + quality_str + m.group(2), text, count=1,
    )
    text, n_e = re.subn(
        r'("' + month + r'": \{[^}]*?"emptyState": "[^"]*?확정행은 )\d+(일까지입니다\.")',
        lambda m: m.group(1) + str(last_day) + m.group(2), text, count=1,
    )
    if not (n_q and n_e):
        raise SystemExit(f"monthConfig {month}의 quality/emptyState 갱신 실패 (quality={n_q}, emptyState={n_e})")

    if dry_run:
        print(f"[dry-run] {month}: 행 블록 준비 완료, HTML 미수정 (quality: {quality_str}, last_day: {last_day})")
        return
    html_path.write_text(text, encoding="utf-8")
    print(f"updated {html_path} — {month} rows, quality: {quality_str}, last_day: {last_day}")


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

    rows = fetch_rows(database_url, args.month)
    if not rows:
        raise SystemExit(f"{args.month}에 mart_daily_profit_gauge 행이 없습니다")
    block, quality_str = render_block(args.month, rows)
    update_html(Path(args.html), args.month, block, quality_str, max(r[0] for r in rows), args.dry_run)


if __name__ == "__main__":
    main()
