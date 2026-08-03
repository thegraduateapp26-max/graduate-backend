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
import re
import time

import psycopg2
import psycopg2.extras
import requests
from bs4 import BeautifulSoup, NavigableString

API_URL = "https://www.themuse.com/api/public/jobs"
LEVELS = ["Internship", "Entry Level"]
PAGES_PER_LEVEL = 20  # ~20 results/page -> up to ~400 candidates per level before filtering
TARGET_COUNT = 300

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
    "Engineering": "Technology",
    "Data Science": "Technology",
    "Data and Analytics": "Technology",
    "IT": "Technology",
    "UX": "Technology",
    "Product Management": "Technology",
    "Design": "Design",
    "Creative & Design": "Design",
    "Editorial": "Media & Communications",
    "Media Production": "Media & Communications",
    "Media Communications": "Media & Communications",
    "Writing & Editing": "Media & Communications",
    "Social Media & Community": "Marketing",
    "Marketing": "Marketing",
    "Marketing and PR": "Marketing",
    "Public Relations": "Marketing",
    "Sales": "Sales",
    "Account Management": "Sales",
    "Customer Service": "Customer Support",
    "Business and Strategy": "Business",
    "Project and Product Management": "Business",
    "Operations": "Operations",
    "Human Resources and Recruitment": "Human Resources",
    "Finance": "Finance",
    "Accounting": "Finance",
    "Legal Services": "Legal",
    "Healthcare": "Healthcare",
    "Healthcare & Medicine": "Healthcare",
    "Education": "Education",
    "Retail": "Retail",
    "Science and Engineering": "Science",
}

RESPONSIBILITY_HEADERS = re.compile(
    r"responsibilit|what you.?ll do|day.to.day|duties|your role|the role|what you.?ll be doing|impact",
    re.I,
)
QUALIFICATION_HEADERS = re.compile(
    r"qualif|requirement|who you are|what you bring|what we.?re looking for|your background|skills? (you|needed)|basic qualifications|minimum qualifications|preferred",
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


def logo_url_for(company_name, companies_by_name):
    key = company_name.strip().lower()
    domain = companies_by_name.get(key)
    if not domain:
        domain = companies_by_name.get(normalize_company_name(company_name))
    if domain:
        return f"https://icons.duckduckgo.com/ip3/{domain}.ico"
    return None


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

    sections = {"summary": [], "responsibilities": [], "qualifications": [], "about": [], "other": []}
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

    full_text = clean_text(soup)
    return {
        "description": full_text[:4000],
        "job_summary": job_summary,
        "key_responsibilities": responsibilities,
        "qualifications": qualifications,
        "about_company": about_company,
    }


def extract_salary(text):
    m = SALARY_RE.search(text or "")
    return m.group(0).strip() if m else None


def fetch_level(level, pages):
    for page in range(pages):
        resp = requests.get(API_URL, params={"level": level, "page": page}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
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
                key_responsibilities, qualifications, about_company, logo_url,
                source, source_id, created_at
            ) VALUES (
                %(title)s, %(company)s, %(location)s, %(salary_range)s, %(job_type)s, %(employment_type)s,
                %(description)s, %(url)s, %(tags)s, TRUE, %(job_function)s, %(industry)s,
                %(seniority_level)s, %(job_summary)s, %(key_responsibilities)s,
                %(qualifications)s, %(about_company)s, %(logo_url)s, %(source)s,
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
                qualifications = EXCLUDED.qualifications, about_company = EXCLUDED.about_company,
                logo_url = EXCLUDED.logo_url
            """,
            r,
        )
        inserted += 1
    conn.commit()
    return inserted


def main():
    companies_by_name = load_companies()
    print(f"Loaded {len(companies_by_name)} known companies for logo lookup")

    seen_ids = set()
    rows = []
    for level in LEVELS:
        count_for_level = 0
        for raw in fetch_level(level, PAGES_PER_LEVEL):
            if len(rows) >= TARGET_COUNT:
                break
            if raw["id"] in seen_ids:
                continue
            locations = raw.get("locations") or []
            location_name = locations[0]["name"] if locations else ""
            if not is_us_location(location_name):
                continue
            if not raw.get("name") or not raw.get("company", {}).get("name"):
                continue
            seen_ids.add(raw["id"])
            rows.append(build_job_row(raw, level, companies_by_name))
            count_for_level += 1
        print(f"Level '{level}': collected {count_for_level} US-based listings")
        if len(rows) >= TARGET_COUNT:
            break

    print(f"Total listings to upsert: {len(rows)}")
    with_logo = sum(1 for r in rows if r["logo_url"])
    print(f"  with matched real logo: {with_logo} ({with_logo * 100 // max(len(rows),1)}%)")
    with_salary = sum(1 for r in rows if r["salary_range"])
    print(f"  with extracted salary: {with_salary}")
    with_resp = sum(1 for r in rows if r["key_responsibilities"])
    print(f"  with responsibilities: {with_resp}")
    with_qual = sum(1 for r in rows if r["qualifications"])
    print(f"  with qualifications: {with_qual}")

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    n = upsert_jobs(conn, rows)
    print(f"Upserted {n} jobs")


if __name__ == "__main__":
    main()
