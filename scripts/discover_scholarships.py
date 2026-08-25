"""
Weekly scholarship discovery. Unlike jobs (a real public API via The Muse), scholarships have
no free public feed - scripts/seed_scholarships.py is a hand-curated static list, and Google
Search grounding (the live-web option) 429s on this project's Gemini key without billing
enabled. So this asks Gemini to name real scholarship programs from its own training knowledge
instead of live search, then treats that as unverified and independently checks that each
candidate's application URL actually loads before publishing it - the URL check, not Gemini's
say-so, is what decides published vs hidden. A candidate whose URL doesn't verify is inserted
hidden (is_active = FALSE, source = 'ai_search') for manual review rather than shown or
silently dropped. Note this still can't confirm a *current-cycle* deadline/amount are accurate
even when the org and URL are real - Gemini is told to leave those blank rather than guess when
unsure, but a stale value can still slip through; spot-check anything published. See GET/PATCH
/api/scholarships/pending in app.py for the review queue.

Purely additive: never touches an existing (title, provider) row, so it can't clobber
hand-verified data from seed_scholarships.py or a previous run.

Usage:
    DATABASE_URL=... GEMINI_API_KEY=... RESEND_API_KEY=... python3 scripts/discover_scholarships.py
"""
import datetime
import json
import os
import sys

import psycopg2
import psycopg2.extras
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import emails

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "gabbybranch84@gmail.com")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = "gemini-3-flash-preview"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

TARGET_COUNT = 10  # weekly target - real, verifiably-current scholarships are far scarcer than job listings
KNOWN_TAGS = ["High School", "Undergraduate", "Graduate"]

# A real browser UA - plenty of financial-aid/foundation sites block the default python-requests
# UA outright, which would otherwise flood the pending-review queue with false negatives.
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

SCHOLARSHIP_SCHEMA = {
    "type": "object",
    "properties": {
        "scholarships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "provider": {"type": "string"},
                    "amount": {"type": "string"},
                    "deadline": {"type": "string", "description": "Strict ISO 8601 date, YYYY-MM-DD only (e.g. 2026-10-31, never 'October 31, 2026'). Empty string if rolling/unknown."},
                    "description": {"type": "string"},
                    "url": {"type": "string", "description": "direct application or official info URL"},
                    "tags": {"type": "array", "items": {"type": "string", "enum": KNOWN_TAGS}},
                },
                "required": ["title", "provider", "url"],
            },
        },
    },
    "required": ["scholarships"],
}


def _gemini_call(payload):
    resp = requests.post(f"{GEMINI_URL}?key={GEMINI_API_KEY}", json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def generate_candidates(existing_titles):
    """One call, no tools: Gemini names real scholarship programs from its own training
    knowledge (not live search) directly into the target schema. This is explicitly NOT trusted
    as verified - it's a candidate list. verify_url() below is the actual gate that decides
    whether a candidate gets published or held for manual review."""
    exclude = "\n".join(f"- {t}" for t in sorted(existing_titles)) or "(none yet)"
    prompt = (
        f"Name up to {TARGET_COUNT} real, well-established scholarship programs for US high "
        "school or college students (any field of study, any demographic focus) that you are "
        f"genuinely confident exist, that are NOT already in this list:\n{exclude}\n\n"
        "For each one give: its exact official title, provider/organization name, typical award "
        "amount, a 1-2 sentence description, tags for who's eligible, and the real official "
        "application/info URL if you know it precisely (not a guessed URL pattern). For "
        "deadline: only fill it in if you're confident of the CURRENT cycle's date - leave it "
        "blank rather than guess, since a wrong date is worse than no date. Only include "
        "programs you're confident are real and still active - return fewer than "
        f"{TARGET_COUNT} rather than inventing any to hit the count. Do not repeat any "
        "scholarship already listed above."
    )
    text = _gemini_call({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": SCHOLARSHIP_SCHEMA,
        },
    })
    return json.loads(text).get("scholarships", [])


def verify_url(url):
    if not url:
        return False
    try:
        resp = requests.get(url, timeout=15, allow_redirects=True, headers={"User-Agent": BROWSER_UA})
        return resp.status_code < 400
    except requests.exceptions.RequestException:
        return False


def normalize_deadline(raw):
    """Prompted for strict ISO (YYYY-MM-DD), but falls back to a couple of common human formats
    in case the model doesn't comply, rather than silently losing a real deadline to None."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt, length in [("%Y-%m-%d", 10), ("%B %d, %Y", None), ("%b %d, %Y", None)]:
        try:
            text = raw[:length] if length else raw
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()

    cur.execute("SELECT title, provider FROM scholarships")
    existing_rows = cur.fetchall()
    existing_keys = {(r["title"].strip().lower(), r["provider"].strip().lower()) for r in existing_rows}
    existing_titles = [r["title"] for r in existing_rows]
    print(f"{len(existing_keys)} scholarships already on the board")

    candidates = generate_candidates(existing_titles)
    print(f"Gemini returned {len(candidates)} candidates")

    published, pending = [], []
    today = datetime.date.today()
    for c in candidates:
        title = (c.get("title") or "").strip()
        provider = (c.get("provider") or "").strip()
        url = (c.get("url") or "").strip()
        if not title or not provider or not url:
            continue
        if (title.lower(), provider.lower()) in existing_keys:
            continue  # already on the board - never overwrite curated/prior-run data
        deadline = normalize_deadline(c.get("deadline"))
        if deadline and datetime.date.fromisoformat(deadline) < today:
            continue  # stale - not worth surfacing even for manual review
        tags = [t for t in (c.get("tags") or []) if t in KNOWN_TAGS]

        row = {
            "title": title,
            "provider": provider,
            "amount": (c.get("amount") or "").strip() or None,
            "deadline": deadline,
            "description": (c.get("description") or "").strip() or None,
            "url": url,
            "tags": tags,
        }
        existing_keys.add((title.lower(), provider.lower()))  # dedupe within this run's own candidates too
        if verify_url(url):
            published.append(row)
        else:
            pending.append(row)

    for row, is_active in [(r, True) for r in published] + [(r, False) for r in pending]:
        cur.execute(
            """
            INSERT INTO scholarships (title, provider, amount, deadline, description, url, tags, is_active, source)
            VALUES (%(title)s, %(provider)s, %(amount)s, %(deadline)s, %(description)s, %(url)s, %(tags)s, %(is_active)s, 'ai_search')
            """,
            {**row, "is_active": is_active},
        )
    conn.commit()
    print(f"Inserted {len(published)} published, {len(pending)} pending review")

    try:
        emails.send_scholarship_report(ADMIN_EMAIL, published, pending)
    except Exception as e:
        print(f"Scholarship report email error: {e}")


if __name__ == "__main__":
    main()
