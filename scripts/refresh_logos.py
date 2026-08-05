"""
One-off backfill: re-derives logo_url for every distinct company already in the jobs
table, now that a Logo.dev API key is wired in (see ingest_jobs.py's logo_url_for -
Logo.dev's curated database has real logos for many companies whose own site favicon
is too small to pass the client-side quality floor, e.g. TikTok, SpaceX, Allstate).
Does NOT re-fetch job postings from The Muse - just recomputes and updates logo_url.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/refresh_logos.py
"""
import os

import psycopg2
import psycopg2.extras

from ingest_jobs import load_companies, logo_url_for


def main():
    companies_by_name = load_companies()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT DISTINCT company FROM jobs WHERE source = 'themuse' ORDER BY company")
    companies = [r["company"] for r in cur.fetchall()]
    print(f"Refreshing logos for {len(companies)} companies...")

    changed = 0
    have_logo = 0
    for i, company in enumerate(companies, 1):
        new_url = logo_url_for(company, companies_by_name)
        cur.execute(
            "SELECT logo_url FROM jobs WHERE company = %s AND source = 'themuse' LIMIT 1",
            (company,),
        )
        old_url = cur.fetchone()["logo_url"]
        if new_url != old_url:
            cur.execute(
                "UPDATE jobs SET logo_url = %s WHERE company = %s AND source = 'themuse'",
                (new_url, company),
            )
            changed += 1
        if new_url:
            have_logo += 1
        print(f"  [{i}/{len(companies)}] {company}: {new_url or '(none)'}")

    conn.commit()
    print(f"\nDone. {have_logo}/{len(companies)} companies have a logo, {changed} changed.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
