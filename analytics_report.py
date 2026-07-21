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


def send_graduation_reminders():
    """Email students whose expected graduation date is about a month away, once each."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, email, expected_graduation_date FROM users
        WHERE role = 'student'
          AND expected_graduation_date IS NOT NULL
          AND graduation_reminder_sent = FALSE
          AND expected_graduation_date <= CURRENT_DATE + INTERVAL '1 month'
    """)
    due = cur.fetchall()

    sent = 0
    for user in due:
        grad_date_label = user['expected_graduation_date'].strftime("%B %Y")
        try:
            emails.send_graduation_reminder_email(user['name'], user['email'], grad_date_label)
            cur.execute("UPDATE users SET graduation_reminder_sent = TRUE WHERE id = %s", (user['id'],))
            conn.commit()
            sent += 1
        except Exception as e:
            print(f"Graduation reminder error for {user['email']}: {e}")

    cur.close()
    conn.close()
    return sent


if __name__ == "__main__":
    stats = get_stats()
    print(f"Daily analytics: {stats}")
    emails.send_daily_analytics_email(ADMIN_EMAIL, stats)
    print(f"Report sent to {ADMIN_EMAIL}")

    reminders_sent = send_graduation_reminders()
    print(f"Graduation reminders sent: {reminders_sent}")
