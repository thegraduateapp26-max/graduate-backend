import os
import psycopg2
import psycopg2.extras

DEMO_COMPANIES = {"Test Employer Co", "Nimbus Systems", "GoldStream Capital", "InnovateX", "CipherSec"}

conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()
cur.execute("SELECT id, title, company, source FROM jobs WHERE source IS NULL")
rows = cur.fetchall()

to_delete = [r for r in rows if r["company"] in DEMO_COMPANIES]
unexpected = [r for r in rows if r["company"] not in DEMO_COMPANIES]

if unexpected:
    print("Found unexpected non-demo, non-sourced rows - not deleting anything, review manually:")
    for r in unexpected:
        print(" ", dict(r))
    raise SystemExit(1)

print(f"Deleting {len(to_delete)} demo job(s):")
for r in to_delete:
    print(" ", r["title"], "@", r["company"], r["id"])

ids = [r["id"] for r in to_delete]
if ids:
    cur.execute("DELETE FROM applications WHERE job_id = ANY(%s::uuid[])", (ids,))
    print(f"Deleted {cur.rowcount} application(s) referencing demo jobs")
    cur.execute("DELETE FROM jobs WHERE id = ANY(%s::uuid[])", (ids,))
    conn.commit()
    print(f"Deleted {cur.rowcount} row(s)")
else:
    print("Nothing to delete")
