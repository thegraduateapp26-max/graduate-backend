"""
One-off backfill: replaces the stored url for every already-ingested job (currently The
Muse's own hosted page) with the real employer application link extracted from that page's
own JSON payload - see extract_apply_link()/resolve_real_apply_url() in ingest_jobs.py for
why the public API alone can't give us this and how the extraction works. Doesn't touch any
other job data, doesn't re-scrape The Muse's job feed.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/refresh_apply_links.py
"""
import os

import psycopg2
import psycopg2.extras

from ingest_jobs import resolve_real_apply_url


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, title, company, url FROM jobs WHERE source = 'themuse' AND url IS NOT NULL")
    rows = cur.fetchall()
    print(f"Resolving real apply links for {len(rows)} jobs...")

    updated = 0
    unchanged = 0
    for i, row in enumerate(rows, 1):
        real_url = resolve_real_apply_url(row["url"])
        if real_url and real_url != row["url"]:
            cur.execute("UPDATE jobs SET url = %s WHERE id = %s", (real_url, row["id"]))
            updated += 1
            print(f"  [{i}/{len(rows)}] {row['company']}: {real_url[:90]}")
        else:
            unchanged += 1
            if i % 25 == 0:
                print(f"  [{i}/{len(rows)}] ...")

    conn.commit()
    print(f"\nDone. {updated} updated to a real apply link, {unchanged} left as-is (extraction failed or unchanged).")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
