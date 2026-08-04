"""
Pulls real internship / entry-level / new-grad job listings from The Muse's public
jobs API (https://www.themuse.com/api/public/jobs, no API key required) and upserts
them into the `jobs` table, parsing each posting's HTML into structured fields
(job_summary, key_responsibilities, qualifications, about_company) and cross-
referencing the company name against public/data/companies.json for a real logo.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/ingest_jobs.py
"""
import json
import os
import random
import re
import time

import psycopg2
import psycopg2.extras
import requests
from bs4 import BeautifulSoup, NavigableString

API_URL = "https://www.themuse.com/api/public/jobs"
LEVELS = ["Internship", "Entry Level"]
# The Muse's actual category taxonomy (confirmed by sampling live results - its docs list
# a different/stale set). Querying category-by-category, instead of only paging through the
# unfiltered level feed, is what gets breadth across industries instead of an unfiltered feed
# that's dominated by whichever few employers (Walmart, CVS) post the most listings.
CATEGORIES = [
    "Software Engineering", "Data and Analytics", "Design and UX", "Science and Engineering",
    "Accounting and Finance", "Sales", "Account Management", "Advertising and Marketing",
    "Business Operations", "Management", "Project Management", "Human Resources and Recruitment",
    "Customer Service", "Administration and Office", "Healthcare", "Education",
    "Transportation and Logistics", "Food and Hospitality Services",
]
PAGES_PER_QUERY = 40  # ~20 results/page; the organized-listing filter rejects a lot, so needs real depth to reach TARGET_COUNT
MAX_PER_COMPANY = 12  # prevents one high-volume poster (e.g. Walmart, CVS) from crowding out everyone else
PRIORITY_MAX_PER_COMPANY = 8  # slightly higher cap for named brand-recognition pulls below
TARGET_COUNT = 500

# Well-known brands confirmed (by hand, via the API) to actually have current internship/entry
# -level listings on The Muse - queried by name in addition to the category sampling below,
# since a handful of big employers don't reliably surface through category/level alone.
# Some requested brands (BNY Mellon, Duolingo) have zero current listings on this source as of
# writing and are deliberately left out rather than faked.
PRIORITY_COMPANIES = [
    "PNC", "Salesforce", "Mastercard", "IBM", "Wells Fargo", "Bank of America",
    "Visa", "Charles Schwab", "Capital One", "Fidelity Investments",
]

COMPANIES_JSON = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "graduate-app-career-&-education-hub", "public", "data", "companies.json",
)

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}

CATEGORY_TO_INDUSTRY = {
    "Software Engineering": "Technology",
    "Data and Analytics": "Technology",
    "Design and UX": "Design",
    "Science and Engineering": "Science",
    "Accounting and Finance": "Finance",
    "Sales": "Sales",
    "Account Management": "Sales",
    "Advertising and Marketing": "Marketing",
    "Business Operations": "Business",
    "Management": "Business",
    "Project Management": "Business",
    "Human Resources and Recruitment": "Human Resources",
    "Customer Service": "Customer Support",
    "Administration and Office": "Business",
    "Healthcare": "Healthcare",
    "Education": "Education",
    "Transportation and Logistics": "Logistics",
    "Food and Hospitality Services": "Hospitality",
}

RESPONSIBILITY_HEADERS = re.compile(
    r"responsibilit|what you.?ll do|day.to.day|duties|your role|the role|what you.?ll be doing|impact",
    re.I,
)
# Checked before QUALIFICATION_HEADERS - "Preferred Qualifications" would otherwise match the
# generic "qualif" pattern below and get lumped in with required qualifications, which is
# exactly the "everything mixed together" mess this field split is meant to fix.
PREFERRED_HEADERS = re.compile(
    r"preferred|nice.to.have|bonus points|a plus|ideal candidate",
    re.I,
)
QUALIFICATION_HEADERS = re.compile(
    r"qualif|require|who you are|what you bring|what we.?re looking for|your background|skills? (you|needed)|education and experience",
    re.I,
)
ABOUT_HEADERS = re.compile(
    r"about (us|the company|%s)|who we are|why join|our (company|mission|team)|company overview",
    re.I,
)

SALARY_RE = re.compile(
    r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?(?:-|to|–)\s?\$?\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s?/\s?(?:hour|hr|year|yr))?"
    r"|\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?(?:/|per)\s?(?:hour|hr|year|yr)",
    re.I,
)


def load_companies():
    with open(COMPANIES_JSON) as f:
        data = json.load(f)
    return {c["name"].strip().lower(): c["domain"] for c in data}


def normalize_company_name(name):
    n = name.strip().lower()
    n = re.sub(r"[.,]", "", n)
    n = re.sub(r"\b(inc|llc|ltd|corp|corporation|co)\b", "", n).strip()
    return n


def favicon_url(domain, size=128):
    # Google's favicon service serves whatever resolution the site's own icon actually is
    # (confirmed up to 150px+ for many real companies) instead of DuckDuckGo's fixed 32x32,
    # which is what was making every logo on the site look blurry.
    return f"https://www.google.com/s2/favicons?domain={domain}&sz={size}"


_GENERIC_FAVICON_BYTES = None


def _is_real_favicon(content):
    """Google serves a byte-identical generic globe icon whenever it has nothing for a
    domain - fetching a guaranteed-fake domain once gives a reference to diff against,
    which is far more reliable than guessing from image dimensions/content-length."""
    global _GENERIC_FAVICON_BYTES
    if _GENERIC_FAVICON_BYTES is None:
        try:
            resp = requests.get(favicon_url("zz-definitely-fake-domain-99999.com"), timeout=10)
            _GENERIC_FAVICON_BYTES = resp.content
        except requests.exceptions.RequestException:
            _GENERIC_FAVICON_BYTES = b""
    return content != _GENERIC_FAVICON_BYTES


def guess_domain_candidates(name):
    n = re.sub(r"[,.]", "", name.strip())
    n = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|group|holdings|company|companies)\b", "", n, flags=re.I).strip()
    words = re.findall(r"[a-zA-Z0-9]+", n)
    if not words:
        return []
    joined = "".join(w.lower() for w in words)
    candidates = [joined + ".com", joined + ".org"]
    if len(words) > 1:
        candidates.append("-".join(w.lower() for w in words) + ".com")
    # Deliberately not guessing from just the first word (e.g. "Sandia" out of "Sandia
    # National Laboratories") even though it would catch a few more institutions - a short
    # generic first word ("Enterprise", "National"...) is too likely to collide with an
    # unrelated real site that happens to have its own real favicon, which the "is this a
    # real favicon" check can't catch since it would be a genuine (just wrong) logo.
    return candidates


_domain_verify_cache = {}


def find_logo_domain(company_name, companies_by_name):
    """Curated companies.json entries are trusted outright (a human verified those domains).
    For everything else, guesses a domain from the company name and only accepts it if
    Google actually has a real (non-generic) favicon for it - better coverage than the
    curated list alone, without just making up a logo for a domain that doesn't exist."""
    key = company_name.strip().lower()
    domain = companies_by_name.get(key) or companies_by_name.get(normalize_company_name(company_name))
    if domain:
        return domain

    if company_name in _domain_verify_cache:
        return _domain_verify_cache[company_name]

    found = None
    for candidate in guess_domain_candidates(company_name):
        try:
            resp = requests.get(favicon_url(candidate), timeout=10)
            if resp.ok and _is_real_favicon(resp.content):
                found = candidate
                break
        except requests.exceptions.RequestException:
            continue
    _domain_verify_cache[company_name] = found
    return found


def logo_url_for(company_name, companies_by_name):
    domain = find_logo_domain(company_name, companies_by_name)
    return favicon_url(domain) if domain else None


def is_us_location(name):
    if not name:
        return False
    if "remote" in name.lower():
        return True
    m = re.search(r",\s*([A-Z]{2})\b", name)
    return bool(m and m.group(1) in US_STATES)


def clean_text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def classify_header(text):
    if ABOUT_HEADERS.search(text):
        return "about"
    if RESPONSIBILITY_HEADERS.search(text):
        return "responsibilities"
    if PREFERRED_HEADERS.search(text):
        return "preferred_qualifications"
    if QUALIFICATION_HEADERS.search(text):
        return "qualifications"
    return None


BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6"}


def iter_runs(node):
    """Walks the tree in document order, yielding ('text', str), ('bold', str), ('li', str),
    or ('break', None). Linearizes content regardless of whether section headers are their own
    <p> tags or just inline <strong>/<b> runs inside one big paragraph separated by <br>, which
    is a common pattern in real job postings."""
    for child in node.children:
        if isinstance(child, NavigableString):
            s = str(child)
            if s.strip():
                yield ("text", s)
            continue
        name = getattr(child, "name", None)
        if name == "br":
            yield ("break", None)
        elif name in ("strong", "b"):
            t = clean_text(child)
            if t:
                yield ("bold", t)
        elif name in ("ul", "ol"):
            yield ("break", None)
            for li in child.find_all("li", recursive=False):
                t = clean_text(li)
                if t:
                    yield ("li", t)
            yield ("break", None)
        elif name in BLOCK_TAGS:
            yield ("break", None)
            if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                t = clean_text(child)
                if t:
                    yield ("bold", t)
            else:
                yield from iter_runs(child)
            yield ("break", None)
        elif name in ("a", "span", "em", "i", "u", "font"):
            yield from iter_runs(child)
        elif child.find(True) is not None or clean_text(child):
            yield from iter_runs(child)


def group_lines(runs):
    """Groups the flat run stream into lines at 'break' boundaries: ('li', None, text) or
    ('line', leading_bold_or_None, full_text)."""
    lines = []
    buf = []
    leading_bold = None

    def flush():
        nonlocal buf, leading_bold
        text = " ".join(b for _, b in buf).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            lines.append(("line", leading_bold, text))
        buf = []
        leading_bold = None

    for kind, val in runs:
        if kind == "break":
            flush()
        elif kind == "li":
            flush()
            lines.append(("li", None, val))
        else:
            if not buf and kind == "bold":
                leading_bold = val
            buf.append((kind, val))
    flush()
    return lines


def parse_contents(html):
    soup = BeautifulSoup(html or "", "html.parser")
    lines = group_lines(iter_runs(soup))

    sections = {"summary": [], "responsibilities": [], "qualifications": [], "preferred_qualifications": [], "about": [], "other": []}
    current = "summary"
    for kind, leading_bold, text in lines:
        if kind == "li":
            sections[current].append(text)
            continue
        header_text = None
        remaining = text
        if leading_bold and len(leading_bold) <= 60:
            header_candidate = leading_bold.rstrip(":").strip()
            if header_candidate and (text == leading_bold or text.startswith(leading_bold)):
                header_text = header_candidate
                remaining = text[len(leading_bold):].strip(" :-–—")
        if header_text:
            current = classify_header(header_text) or "other"
            if remaining:
                sections[current].append(remaining)
            continue
        sections[current].append(text)

    job_summary = " ".join(sections["summary"][:3])[:800] or None
    about_company = " ".join(sections["about"])[:1200] or None
    responsibilities = sections["responsibilities"][:10]
    qualifications = sections["qualifications"][:10]
    preferred_qualifications = sections["preferred_qualifications"][:10]

    full_text = clean_text(soup)
    return {
        "description": full_text[:4000],
        "job_summary": job_summary,
        "key_responsibilities": responsibilities,
        "qualifications": qualifications,
        "preferred_qualifications": preferred_qualifications,
        "about_company": about_company,
    }


def extract_salary(text):
    m = SALARY_RE.search(text or "")
    return m.group(0).strip() if m else None


def fetch_query(params, pages, label):
    for page in range(pages):
        data = None
        for attempt in range(3):
            try:
                resp = requests.get(API_URL, params={**params, "page": page}, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                break
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    print(f"  (giving up on {label} page {page}: {e})")
                else:
                    time.sleep(1.5 * (attempt + 1))
        if data is None:
            break
        results = data.get("results", [])
        if not results:
            break
        yield from results
        if page >= data.get("page_count", 1) - 1:
            break
        time.sleep(0.15)


def build_job_row(raw, level_name, companies_by_name):
    title = raw.get("name", "").strip()
    company = raw.get("company", {}).get("name", "").strip()
    locations = raw.get("locations") or []
    location = locations[0]["name"] if locations else "Remote"
    categories = raw.get("categories") or []
    job_function = categories[0]["name"] if categories else None
    industry = CATEGORY_TO_INDUSTRY.get(job_function) if job_function else None

    parsed = parse_contents(raw.get("contents", ""))

    # employment_type (Internship/Full-time/Part-time) is distinct from job_type, which the
    # frontend already treats as remote/hybrid/onsite - keep the two independent.
    employment_type = "Internship" if level_name == "Internship" else "Full-time"
    if re.search(r"part.?time", parsed["description"], re.I):
        employment_type = "Part-Time"

    location_lower = location.lower()
    if "remote" in location_lower:
        job_type = "remote"
    elif re.search(r"\bhybrid\b", parsed["description"], re.I):
        job_type = "hybrid"
    else:
        job_type = "onsite"

    return {
        "title": title,
        "company": company,
        "location": location,
        "salary_range": extract_salary(parsed["description"]),
        "job_type": job_type,
        "employment_type": employment_type,
        "description": parsed["description"],
        "url": raw.get("refs", {}).get("landing_page"),
        "tags": [],
        "job_function": job_function,
        "industry": industry,
        "seniority_level": level_name,
        "job_summary": parsed["job_summary"],
        "key_responsibilities": parsed["key_responsibilities"],
        "qualifications": parsed["qualifications"],
        "preferred_qualifications": parsed["preferred_qualifications"],
        "about_company": parsed["about_company"],
        "logo_url": logo_url_for(company, companies_by_name),
        "source": "themuse",
        "source_id": str(raw["id"]),
        "created_at": raw.get("publication_date"),
    }


def upsert_jobs(conn, rows):
    cur = conn.cursor()
    inserted = 0
    for r in rows:
        cur.execute(
            """
            INSERT INTO jobs (
                title, company, location, salary_range, job_type, employment_type, description, url, tags,
                is_active, job_function, industry, seniority_level, job_summary,
                key_responsibilities, qualifications, preferred_qualifications, about_company, logo_url,
                source, source_id, created_at
            ) VALUES (
                %(title)s, %(company)s, %(location)s, %(salary_range)s, %(job_type)s, %(employment_type)s,
                %(description)s, %(url)s, %(tags)s, TRUE, %(job_function)s, %(industry)s,
                %(seniority_level)s, %(job_summary)s, %(key_responsibilities)s,
                %(qualifications)s, %(preferred_qualifications)s, %(about_company)s, %(logo_url)s, %(source)s,
                %(source_id)s, COALESCE(%(created_at)s, NOW())
            )
            ON CONFLICT (source, source_id) WHERE source IS NOT NULL DO UPDATE SET
                title = EXCLUDED.title, company = EXCLUDED.company, location = EXCLUDED.location,
                salary_range = EXCLUDED.salary_range, job_type = EXCLUDED.job_type,
                employment_type = EXCLUDED.employment_type,
                description = EXCLUDED.description, url = EXCLUDED.url,
                is_active = TRUE, job_function = EXCLUDED.job_function,
                industry = EXCLUDED.industry, seniority_level = EXCLUDED.seniority_level,
                job_summary = EXCLUDED.job_summary,
                key_responsibilities = EXCLUDED.key_responsibilities,
                qualifications = EXCLUDED.qualifications, preferred_qualifications = EXCLUDED.preferred_qualifications,
                about_company = EXCLUDED.about_company,
                logo_url = EXCLUDED.logo_url
            """,
            r,
        )
        inserted += 1
    conn.commit()
    return inserted


def is_organized(row):
    """A title/location with no real content isn't worth showing users, but requiring BOTH
    responsibilities and qualifications to have parsed out successfully turned out to reject
    plenty of genuinely well-written postings just because they use header wording (or skip
    one section entirely) that the parser doesn't happen to catch - real companies vary their
    templates too much for that to be a reliable "is this organized" signal on its own. A
    structured list (either one) plus a real, substantial description is a better bar."""
    has_structure = bool(row["key_responsibilities"]) or bool(row["qualifications"])
    has_real_description = bool(row["description"]) and len(row["description"]) >= 300
    return has_structure and has_real_description


def _consider(raw, level, companies_by_name, seen_ids, company_counts, rows, max_per_company):
    if len(rows) >= TARGET_COUNT:
        return False
    if raw["id"] in seen_ids:
        return True
    company_name = raw.get("company", {}).get("name", "").strip()
    if not raw.get("name") or not company_name:
        return True
    if company_counts.get(company_name, 0) >= max_per_company:
        return True
    locations = raw.get("locations") or []
    location_name = locations[0]["name"] if locations else ""
    if not is_us_location(location_name):
        return True
    row = build_job_row(raw, level, companies_by_name)
    if not is_organized(row):
        return True
    seen_ids.add(raw["id"])
    company_counts[company_name] = company_counts.get(company_name, 0) + 1
    rows.append(row)
    return True


def main():
    companies_by_name = load_companies()
    print(f"Loaded {len(companies_by_name)} known companies for logo lookup")

    seen_ids = set()
    company_counts = {}
    rows = []

    print("-- priority brand-name companies --")
    for company in PRIORITY_COMPANIES:
        for level in LEVELS:
            count_before = len(rows)
            for raw in fetch_query({"company": company, "level": level}, 3, f"company={company}"):
                if not _consider(raw, level, companies_by_name, seen_ids, company_counts, rows, PRIORITY_MAX_PER_COMPANY):
                    break
            print(f"'{company}' / '{level}': collected {len(rows) - count_before}")

    print("-- category sampling --")
    queries = [(level, category) for level in LEVELS for category in CATEGORIES]
    random.shuffle(queries)  # avoid always exhausting the same early categories first as TARGET_COUNT is hit

    for level, category in queries:
        if len(rows) >= TARGET_COUNT:
            break
        count_before = len(rows)
        for raw in fetch_query({"level": level, "category": category}, PAGES_PER_QUERY, f"'{category}'/'{level}'"):
            if not _consider(raw, level, companies_by_name, seen_ids, company_counts, rows, MAX_PER_COMPANY):
                break
        print(f"'{category}' / '{level}': collected {len(rows) - count_before}")

    print(f"Total listings to upsert: {len(rows)} across {len(company_counts)} distinct companies")
    with_logo = sum(1 for r in rows if r["logo_url"])
    print(f"  with matched real logo: {with_logo} ({with_logo * 100 // max(len(rows),1)}%)")
    with_salary = sum(1 for r in rows if r["salary_range"])
    print(f"  with extracted salary: {with_salary}")
    with_resp = sum(1 for r in rows if r["key_responsibilities"])
    print(f"  with responsibilities: {with_resp}")
    with_qual = sum(1 for r in rows if r["qualifications"])
    print(f"  with qualifications: {with_qual}")
    with_preferred = sum(1 for r in rows if r["preferred_qualifications"])
    print(f"  with preferred qualifications: {with_preferred}")
    with_about = sum(1 for r in rows if r["about_company"])
    print(f"  with about-company: {with_about}")
    priority_hits = sorted({r["company"] for r in rows} & {c for c in PRIORITY_COMPANIES})
    print(f"  priority companies present: {priority_hits}")
    top_companies = sorted(company_counts.items(), key=lambda kv: -kv[1])[:10]
    print(f"  top companies: {top_companies}")

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    # The per-company cap only means something against a full refresh - otherwise old
    # over-represented rows from a prior run (e.g. 100+ Walmart listings) just sit alongside
    # the new capped batch instead of being replaced by it.
    cur.execute("DELETE FROM applications WHERE job_id IN (SELECT id FROM jobs WHERE source = 'themuse')")
    cur.execute("DELETE FROM jobs WHERE source = 'themuse'")
    conn.commit()
    n = upsert_jobs(conn, rows)
    print(f"Upserted {n} jobs")


if __name__ == "__main__":
    main()
