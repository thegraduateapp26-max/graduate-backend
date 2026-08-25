"""
One-off cleanup: removes jobs whose stored url is on a known-bad apply-link domain (see
BLOCKED_APPLY_DOMAINS in ingest_jobs.py) - discovered 2026-08-06 when click.appcast.io was
found to redirect every Walmart posting to an unrelated generic job board, and adzuna.com's
ad-landing links 404 outright. Future ingestion already skips these via ingest_jobs.py's
_consider(); this clears out the ones that got in before that check existed.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/prune_bad_apply_links.py
"""
import os

import psycopg2
import psycopg2.extras

from ingest_jobs import is_bad_apply_url


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, title, company, url FROM jobs WHERE url IS NOT NULL")
    rows = cur.fetchall()

    bad_ids = [row["id"] for row in rows if is_bad_apply_url(row["url"])]
    for row in rows:
        if row["id"] in bad_ids:
            print(f"  removing: {row['company']} - {row['title']} ({row['url'][:70]})")

    if not bad_ids:
        print("Nothing to remove.")
        cur.close()
        conn.close()
        return

    cur.execute("DELETE FROM applications WHERE job_id = ANY(%s::uuid[])", (bad_ids,))
    cur.execute("DELETE FROM skill_gaps WHERE job_id = ANY(%s::uuid[])", (bad_ids,))
    cur.execute("DELETE FROM scout_matches WHERE job_id = ANY(%s::uuid[])", (bad_ids,))
    cur.execute("DELETE FROM jobs WHERE id = ANY(%s::uuid[])", (bad_ids,))
    conn.commit()
    print(f"\nRemoved {len(bad_ids)} jobs with unreliable apply links.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
