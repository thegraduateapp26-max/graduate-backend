"""
Removes the leftover demo/placeholder scholarships and seeds a curated list of real,
well-known national scholarship programs (name, provider, amount, deadline, description,
and real application URL). There is no free public API with The Muse's breadth/quality for
scholarships, so unlike jobs this is a hand-curated list rather than a live feed - re-run
whenever new real scholarships should be added (upserts on title+provider).

Usage:
    DATABASE_URL=postgresql://... python3 scripts/seed_scholarships.py
"""
import os

import psycopg2
import psycopg2.extras

DEMO_SCHOLARSHIPS = {
    ("Future AI Researchers Grant", "Horizon Labs"),
    ("Women in Engineering Scholarship", "Ada Lovelace Foundation"),
    ("Community Leadership Award", "City Council"),
    ("STEM Innovation Grant", "Tech Fund"),
}

# (title, provider, amount, deadline "YYYY-MM-DD", description, url, tags)
REAL_SCHOLARSHIPS = [
    (
        "QuestBridge National College Match",
        "QuestBridge",
        "Full four-year scholarship",
        "2026-09-25",
        "Matches high-achieving, low-income high school seniors with full four-year scholarships to QuestBridge's partner colleges, covering tuition, room, board, and books.",
        "https://www.questbridge.org/high-school-students/national-college-match",
        ["High School"],
    ),
    (
        "Dell Scholars Program",
        "Michael & Susan Dell Foundation",
        "$20,000",
        "2026-12-01",
        "Supports students who have overcome significant obstacles to pursue higher education, providing a scholarship plus laptop, textbook credits, and ongoing program support through college.",
        "https://www.dellscholars.org/scholarship/",
        ["High School", "Undergraduate"],
    ),
    (
        "Horatio Alger National Scholarship",
        "Horatio Alger Association",
        "$25,000",
        "2026-10-25",
        "Awarded to high school students who have faced and overcome significant adversity, demonstrated integrity and perseverance, and plan to pursue a bachelor's degree.",
        "https://scholars.horatioalger.org/scholarships/",
        ["High School"],
    ),
    (
        "Burger King Scholars Program",
        "Burger King McLamore Foundation",
        "Up to $50,000",
        "2026-12-15",
        "Merit-based scholarship for high school seniors, current college students, and Burger King employees/dependents, based on academics, work experience, and community involvement.",
        "https://www.bkmclamorefoundation.org/",
        ["High School", "Undergraduate"],
    ),
    (
        "Elks National Foundation Most Valuable Student Scholarship",
        "Elks National Foundation",
        "Up to $50,000",
        "2026-11-15",
        "One of the largest scholarship competitions of its kind, awarding college scholarships to graduating high school seniors based on academics, leadership, and financial need.",
        "https://www.elks.org/scholars/scholarships/mvs.cfm",
        ["High School"],
    ),
    (
        "Foot Locker Scholar Athletes Program",
        "Foot Locker Foundation",
        "$20,000",
        "2026-12-05",
        "Recognizes graduating high school student-athletes who have excelled in academics, athletics, and community leadership.",
        "https://footlockerscholarathletes.com/",
        ["High School"],
    ),
    (
        "Society of Women Engineers Scholarship Program",
        "Society of Women Engineers (SWE)",
        "$1,000 - $20,000",
        "2027-02-01",
        "Offers over 250 scholarships annually to women entering or advancing in accredited engineering, engineering technology, and computer science programs.",
        "https://swe.org/scholarships/",
        ["High School", "Undergraduate", "Graduate"],
    ),
    (
        "National Merit Scholarship Program",
        "National Merit Scholarship Corporation",
        "$2,500 and up",
        "2026-10-01",
        "Academic competition for recognition and scholarships, based on PSAT/NMSQT performance, awarded to graduating high school seniors.",
        "https://www.nationalmerit.org/s/1758/interior.aspx?sid=1758&gid=2&pgid=424",
        ["High School"],
    ),
    (
        "Hispanic Scholarship Fund Scholarship",
        "Hispanic Scholarship Fund (HSF)",
        "$500 - $5,000",
        "2027-02-15",
        "The nation's largest Hispanic scholarship organization, supporting Hispanic-heritage students pursuing associate, undergraduate, or graduate degrees.",
        "https://www.hsf.net/scholarship",
        ["Undergraduate", "Graduate"],
    ),
    (
        "UNCF General Scholarship Fund",
        "United Negro College Fund (UNCF)",
        "Varies by award",
        "2027-03-31",
        "Provides scholarships and internships to students at UNCF-member HBCUs and other institutions, helping close the college completion gap for Black students.",
        "https://uncf.org/scholarships",
        ["High School", "Undergraduate"],
    ),
    (
        "American Indian College Fund Scholarship",
        "American Indian College Fund",
        "Varies by award",
        "2027-05-31",
        "Provides scholarships to Native American and Alaska Native students attending tribal colleges or mainstream accredited colleges and universities.",
        "https://collegefund.org/students/scholarships/",
        ["Undergraduate", "Graduate"],
    ),
    (
        "Point Foundation LGBTQ Scholarship",
        "Point Foundation",
        "Up to $10,000",
        "2027-01-27",
        "The nation's largest scholarship-granting organization for LGBTQ students of merit, providing financial support, mentorship, and leadership development.",
        "https://pointfoundation.org/point-apply/",
        ["Undergraduate", "Graduate"],
    ),
    (
        "Google Lime Scholarship",
        "Google (in partnership with Lime Connect)",
        "$10,000",
        "2026-12-05",
        "Supports students with disabilities studying computer science, computer engineering, or a closely related technical field, recognizing leadership and academic excellence.",
        "https://www.limeconnect.com/programs/page/google-lime-scholarship",
        ["Undergraduate", "Graduate"],
    ),
    (
        "Microsoft Disability Scholarship",
        "Microsoft",
        "$5,000",
        "2027-03-15",
        "Awarded to graduating high school seniors with disabilities planning to pursue a degree in computer science, computer engineering, or a related STEM field.",
        "https://careers.microsoft.com/students/us/en/usscholarshipprogram",
        ["High School"],
    ),
    (
        "Amazon Future Engineer Scholarship",
        "Amazon",
        "$40,000",
        "2027-01-25",
        "Awards renewable four-year scholarships plus guaranteed Amazon internships to students from underrepresented and underserved communities pursuing a degree in computer science.",
        "https://www.amazonfutureengineer.com/scholarships",
        ["High School"],
    ),
    (
        "Society of Hispanic Professional Engineers (SHPE) Scholarship",
        "Society of Hispanic Professional Engineers",
        "Varies by award",
        "2027-05-01",
        "Supports Hispanic students pursuing degrees in engineering, science, technology, or math, awarded based on academic achievement, leadership, and financial need.",
        "https://shpe.org/students/scholarships/",
        ["Undergraduate", "Graduate"],
    ),
    (
        "National Society of Black Engineers (NSBE) Scholarships",
        "National Society of Black Engineers",
        "Varies by award",
        "2027-04-01",
        "Offers scholarships to Black students pursuing degrees in engineering, technology, and science, supporting academic and professional development.",
        "https://www.nsbe.org/scholarships",
        ["Undergraduate", "Graduate"],
    ),
    (
        "Scholastic Art & Writing Awards",
        "Alliance for Young Artists & Writers",
        "Up to $10,000",
        "2026-12-01",
        "The nation's longest-running recognition program for creative teens, awarding scholarships and portfolio gold medals in art and writing categories.",
        "https://www.artandwriting.org/",
        ["High School"],
    ),
    (
        "National Black Nurses Association Scholarship",
        "National Black Nurses Association (NBNA)",
        "Varies by award",
        "2027-04-15",
        "Provides scholarships to Black nursing students enrolled in accredited LPN/LVN, RN, or advanced-practice nursing programs.",
        "https://www.nbna.org/scholarships",
        ["Undergraduate", "Graduate"],
    ),
    (
        "Jack Kent Cooke Undergraduate Transfer Scholarship",
        "Jack Kent Cooke Foundation",
        "Up to $55,000/year",
        "2027-01-15",
        "Supports high-achieving community college students with financial need who are transferring to a four-year college or university to complete their bachelor's degree.",
        "https://www.jkcf.org/our-scholarships/undergraduate-transfer-scholarship/",
        ["Undergraduate"],
    ),
    (
        "AMVETS National Scholarship",
        "AMVETS",
        "Varies by award",
        "2027-04-30",
        "Supports veterans, active-duty service members, and their family members pursuing undergraduate or graduate education.",
        "https://amvets.org/scholarships/",
        ["Undergraduate", "Graduate"],
    ),
]


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()

    cur.execute("SELECT id, title, provider FROM scholarships")
    existing = cur.fetchall()
    to_delete = [r["id"] for r in existing if (r["title"], r["provider"]) in DEMO_SCHOLARSHIPS]
    if to_delete:
        cur.execute("DELETE FROM scholarships WHERE id = ANY(%s::uuid[])", (to_delete,))
        print(f"Deleted {cur.rowcount} demo scholarship(s)")

    inserted = 0
    for title, provider, amount, deadline, description, url, tags in REAL_SCHOLARSHIPS:
        cur.execute(
            "SELECT id FROM scholarships WHERE title = %s AND provider = %s",
            (title, provider),
        )
        if cur.fetchone():
            cur.execute(
                """
                UPDATE scholarships SET amount = %s, deadline = %s, description = %s, url = %s, tags = %s, is_active = TRUE
                WHERE title = %s AND provider = %s
                """,
                (amount, deadline, description, url, tags, title, provider),
            )
        else:
            cur.execute(
                """
                INSERT INTO scholarships (title, provider, amount, deadline, description, url, tags, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                """,
                (title, provider, amount, deadline, description, url, tags),
            )
            inserted += 1

    conn.commit()
    print(f"Inserted {inserted} new scholarship(s), upserted {len(REAL_SCHOLARSHIPS) - inserted} existing")

    cur.execute("SELECT count(*) AS n FROM scholarships WHERE is_active = TRUE")
    print(f"Total active scholarships: {cur.fetchone()['n']}")


if __name__ == "__main__":
    main()
