"""
Removes job listings whose source posting (The Muse) has gone dead (404) - filled, expired,
or pulled by the employer. The Muse's public API serves a broad historical feed with no
recency filter of its own, so ingestion picked up a real chunk of already-stale postings
(some literally "Summer 2025" internships, long past their season) alongside current ones.

Checks every job with source='themuse' by requesting its stored url and looking at the final
HTTP status after following redirects. Only a clean 404 counts as dead - anything else (200,
network hiccup, unexpected status) is left alone rather than risk deleting a job that's still
real. Deletes matching `applications` rows first, same as ingest_jobs.py's own refresh logic,
so no orphaned rows are left behind.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/prune_dead_jobs.py
    DATABASE_URL=postgresql://... python3 scripts/prune_dead_jobs.py --dry-run
"""
import os
import sys

import psycopg2
import psycopg2.extras
import requests

REQUEST_TIMEOUT = 10


def is_dead(url):
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        return resp.status_code == 404
    except requests.exceptions.RequestException:
        return None  # network issue, not a confirmed dead link - don't delete on a guess


def main():
    dry_run = "--dry-run" in sys.argv
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, title, company, url FROM jobs WHERE source = 'themuse' AND url IS NOT NULL")
    rows = cur.fetchall()
    print(f"Checking {len(rows)} job URLs...")

    dead_ids = []
    checked = 0
    for row in rows:
        checked += 1
        status = is_dead(row["url"])
        if status is True:
            dead_ids.append(row["id"])
            print(f"  [{checked}/{len(rows)}] DEAD: {row['title'][:50]} @ {row['company']}")
        elif checked % 50 == 0:
            print(f"  [{checked}/{len(rows)}] ...")

    print(f"\n{len(dead_ids)} dead out of {len(rows)} checked.")

    if not dead_ids:
        print("Nothing to remove.")
        cur.close()
        conn.close()
        return

    if dry_run:
        print("Dry run - not deleting. Re-run without --dry-run to actually remove these.")
        cur.close()
        conn.close()
        return

    cur.execute("DELETE FROM applications WHERE job_id = ANY(%s::uuid[])", (dead_ids,))
    cur.execute("DELETE FROM skill_gaps WHERE job_id = ANY(%s::uuid[])", (dead_ids,))
    cur.execute("DELETE FROM jobs WHERE id = ANY(%s::uuid[])", (dead_ids,))
    conn.commit()
    print(f"Removed {len(dead_ids)} dead job listings (and their applications).")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
