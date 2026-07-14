import os
import psycopg2
from psycopg2.extras import RealDictCursor

import emails

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "gabbybranch84@gmail.com")


def get_stats():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS n FROM users")
    total_users = cur.fetchone()['n']

    cur.execute("SELECT COUNT(*) AS n FROM users WHERE created_at >= NOW() - INTERVAL '24 hours'")
    new_signups_24h = cur.fetchone()['n']

    cur.execute("SELECT COUNT(*) AS n FROM jobs")
    total_jobs = cur.fetchone()['n']

    cur.execute("SELECT COUNT(*) AS n FROM scholarships")
    total_scholarships = cur.fetchone()['n']

    cur.execute("SELECT COUNT(*) AS n FROM applications")
    total_applications = cur.fetchone()['n']

    cur.close()
    conn.close()

    return {
        "total_users": total_users,
        "new_signups_24h": new_signups_24h,
        "total_jobs": total_jobs,
        "total_scholarships": total_scholarships,
        "total_applications": total_applications,
    }


if __name__ == "__main__":
    stats = get_stats()
    print(f"Daily analytics: {stats}")
    emails.send_daily_analytics_email(ADMIN_EMAIL, stats)
    print(f"Report sent to {ADMIN_EMAIL}")
