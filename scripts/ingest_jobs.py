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
from bs4 import BeautifulSoup

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


def split_header_paragraph(tag):
    """Handles both standalone header paragraphs (<p><strong>Header</strong></p>) and the more
    common inline form (<p><strong>Header:</strong><br>rest of the paragraph...</p>) - returns
    (header_text, remaining_text) or (None, full_text) if this isn't a header paragraph at all."""
    strong = tag.find(["strong", "b"])
    if not strong:
        return None, clean_text(tag)
    first_tag = tag.find(True)
    if first_tag is not strong:
        return None, clean_text(tag)
    header_text = clean_text(strong).rstrip(":").strip()
    if not header_text or len(header_text) > 60:
        return None, clean_text(tag)
    full = clean_text(tag)
    remaining = full[len(clean_text(strong)):].strip(" : -")
    return header_text, remaining


def parse_contents(html):
    soup = BeautifulSoup(html or "", "html.parser")
    sections = {"summary": [], "responsibilities": [], "qualifications": [], "about": [], "other": []}
    current = "summary"
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "div"], recursive=False):
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            current = classify_header(clean_text(el)) or "other"
            continue
        if el.name in ("ul", "ol"):
            items = [clean_text(li) for li in el.find_all("li") if clean_text(li)]
            if current in ("responsibilities", "qualifications"):
                sections[current].extend(items)
            else:
                sections[current].append(" ".join(items))
            continue
        if el.name in ("p", "div"):
            header_text, remaining = split_header_paragraph(el)
            if header_text:
                current = classify_header(header_text) or "other"
                if remaining:
                    sections[current].append(remaining)
                continue
            text = clean_text(el)
            if text:
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

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    n = upsert_jobs(conn, rows)
    print(f"Upserted {n} jobs")


if __name__ == "__main__":
    main()
