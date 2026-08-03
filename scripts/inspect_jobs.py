import os
import psycopg2
import psycopg2.extras

conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()
cur.execute("SELECT count(*) AS n FROM jobs")
print("total jobs:", cur.fetchone()["n"])
cur.execute("SELECT count(*) AS n FROM jobs WHERE source IS NOT NULL")
print("real (sourced) jobs:", cur.fetchone()["n"])
cur.execute("SELECT id, title, company, location, job_type, salary_range, posted_by, source, created_at FROM jobs ORDER BY created_at DESC LIMIT 25")
for r in cur.fetchall():
    print(dict(r))
