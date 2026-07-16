import os
import datetime
import bcrypt
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from flask_swagger_ui import get_swaggerui_blueprint

import emails


app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app, origins="*", supports_credentials=False)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    return response

@app.before_request
def handle_preflight():
    if request.method == 'OPTIONS':
        response = app.make_response('')
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        response.status_code = 200
        return response

JWT_SECRET = os.environ.get("JWT_SECRET", "super-secret-key")
DATABASE_URL = os.environ.get("DATABASE_URL")


# -----------------------------
# Database Connection
# -----------------------------
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


# -----------------------------
# Database Setup (create tables if they don't exist)
# -----------------------------
def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'graduate',
            verification_status TEXT DEFAULT 'pending',
            headline TEXT,
            school TEXT,
            major TEXT,
            location TEXT,
            avatar_url TEXT,
            skills TEXT[],
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT,
            salary_range TEXT,
            job_type TEXT,
            description TEXT,
            url TEXT,
            tags TEXT[],
            created_at TIMESTAMP DEFAULT NOW(),
            is_active BOOLEAN DEFAULT TRUE,
            posted_by UUID REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS scholarships (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title TEXT NOT NULL,
            provider TEXT NOT NULL,
            amount TEXT,
            deadline DATE,
            description TEXT,
            url TEXT,
            tags TEXT[],
            created_at TIMESTAMP DEFAULT NOW(),
            is_active BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS verification_requests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id),
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS applications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id),
            job_id UUID REFERENCES jobs(id),
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, job_id)
        );

        CREATE TABLE IF NOT EXISTS endorsements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            recipient_id UUID REFERENCES users(id) NOT NULL,
            author_id UUID REFERENCES users(id) NOT NULL,
            relationship TEXT DEFAULT 'Professor',
            text TEXT NOT NULL,
            visible BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS posts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            author_id UUID REFERENCES users(id) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_matches_sent BOOLEAN DEFAULT FALSE;")
    conn.commit()
    cur.close()
    conn.close()


# Init DB on startup
try:
    init_db()
    print("Database initialized successfully")
except Exception as e:
    print(f"Database init error: {e}")


# -----------------------------
# Job Matching
# -----------------------------
def find_top_job_matches(major, skills, limit=5):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, company, location, salary_range, job_type, description, url, tags
        FROM jobs
        WHERE is_active = TRUE
    """)
    jobs = cur.fetchall()
    cur.close()
    conn.close()

    major_words = [w.lower() for w in (major or "").split() if len(w) > 2]
    skill_set = {s.lower() for s in (skills or [])}

    scored = []
    for job in jobs:
        tags = {t.lower() for t in (job['tags'] or [])}
        haystack = " ".join([
            job['title'] or "", job['description'] or "", " ".join(job['tags'] or [])
        ]).lower()

        score = 0
        score += 2 * len(skill_set & tags)
        score += sum(1 for s in skill_set if s in haystack)
        score += 3 * sum(1 for w in major_words if w in haystack)

        if score > 0:
            scored.append((score, job))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [job for _, job in scored[:limit]]


def maybe_send_job_matches(user_id, name, email, major, skills):
    if not major or not skills:
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT profile_matches_sent FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    if not row or row['profile_matches_sent']:
        cur.close()
        conn.close()
        return

    matches = find_top_job_matches(major, skills)
    if matches:
        try:
            emails.send_job_matches_email(name, email, matches)
        except Exception as e:
            print(f"Job matches email error: {e}")

    cur.execute("UPDATE users SET profile_matches_sent = TRUE WHERE id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()


# -----------------------------
# Auth Helper
# -----------------------------
def get_current_user():
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) != 2:
        return None
    token = parts[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["user_id"]
    except Exception:
        return None


# -----------------------------
# Root
# -----------------------------
@app.get("/")
def home():
    return "Graduate API running."


# -----------------------------
# Health Check
# -----------------------------
@app.get("/api/status")
def status():
    try:
        conn = get_conn()
        conn.close()
        return jsonify({"status": "ok", "database": "connected"})
    except Exception as e:
        return jsonify({"status": "error", "database": str(e)}), 500


# -----------------------------
# SIGNUP
# -----------------------------
@app.post("/api/signup")
def signup():
    data = request.json
    name = data.get("name")
    email = data.get("email")
    password = data.get("password")

    if not name or not email or not password:
        return jsonify({"error": "missing fields"}), 400

    role = data.get("role", "graduate")
    valid_roles = ["student", "graduate", "employer", "professor", "recruiter"]
    if role not in valid_roles:
        role = "graduate"

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO users (name, email, password_hash, role, verification_status)
            VALUES (%s, %s, %s, %s, 'pending')
            RETURNING id;
        """, (name, email, password_hash, role))

        result = cur.fetchone()
        user_id = result['id']
        conn.commit()

        try:
            emails.send_welcome_email(name, email)
        except Exception as e:
            print(f"Welcome email error: {e}")

        return jsonify({"status": "account created", "user_id": str(user_id)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# LOGIN
# -----------------------------
@app.post("/api/login")
def login():
    data = request.json
    email = data.get("email")
    password = data.get("password")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, password_hash, name, role FROM users WHERE email = %s", (email,))
    user = cur.fetchone()

    cur.close()
    conn.close()

    if not user:
        return jsonify({"error": "invalid credentials"}), 401

    if not bcrypt.checkpw(password.encode("utf-8"), user['password_hash'].encode("utf-8")):
        return jsonify({"error": "invalid credentials"}), 401

    token = jwt.encode({
        "user_id": str(user['id']),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, JWT_SECRET, algorithm="HS256")

    return jsonify({
        "token": token,
        "user_id": str(user['id']),
        "name": user['name'],
        "role": user['role'],
    })


# -----------------------------
# USERS
# -----------------------------
@app.get("/api/users")
def list_users():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, role, verification_status, headline, school,
        major, location, avatar_url, skills, created_at
        FROM users
        ORDER BY created_at DESC
        LIMIT 100
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    users = []
    for r in rows:
        users.append({
            "id": str(r['id']),
            "name": r['name'],
            "role": r['role'],
            "verificationStatus": r['verification_status'],
            "headline": r['headline'],
            "school": r['school'],
            "major": r['major'],
            "location": r['location'],
            "avatarUrl": r['avatar_url'],
            "skills": r['skills'] or [],
            "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
        })

    return jsonify(users)


# -----------------------------
# UPDATE USER PROFILE
# -----------------------------
@app.patch("/api/users/<user_id>")
def update_user(user_id):
    current_user = get_current_user()
    if not current_user or current_user != user_id:
        return jsonify({"error": "unauthorized"}), 401

    data = request.json
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE users SET
                name = COALESCE(%s, name),
                headline = COALESCE(%s, headline),
                school = COALESCE(%s, school),
                major = COALESCE(%s, major),
                location = COALESCE(%s, location),
                avatar_url = COALESCE(%s, avatar_url),
                skills = COALESCE(%s, skills)
            WHERE id = %s
            RETURNING id, name, email, role, headline, school, major, location, avatar_url, skills
        """, (
            data.get("name"),
            data.get("headline"),
            data.get("school"),
            data.get("major"),
            data.get("location"),
            data.get("avatarUrl"),
            data.get("skills"),
            user_id
        ))

        updated = cur.fetchone()
        conn.commit()

        try:
            maybe_send_job_matches(
                user_id, updated['name'], updated['email'], updated['major'], updated['skills']
            )
        except Exception as e:
            print(f"Job matching error: {e}")

        result = dict(updated)
        result.pop('email', None)
        return jsonify({"status": "updated", "user": result})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# ENDORSEMENTS
# -----------------------------
@app.get("/api/users/<user_id>/endorsements")
def list_endorsements(user_id):
    current_user = get_current_user()

    conn = get_conn()
    cur = conn.cursor()

    if current_user == user_id:
        cur.execute("""
            SELECT e.id, e.relationship, e.text, e.visible, e.created_at,
                   u.name AS author_name, u.avatar_url AS author_avatar_url
            FROM endorsements e
            JOIN users u ON u.id = e.author_id
            WHERE e.recipient_id = %s
            ORDER BY e.created_at DESC
        """, (user_id,))
    else:
        cur.execute("""
            SELECT e.id, e.relationship, e.text, e.visible, e.created_at,
                   u.name AS author_name, u.avatar_url AS author_avatar_url
            FROM endorsements e
            JOIN users u ON u.id = e.author_id
            WHERE e.recipient_id = %s AND e.visible = TRUE
            ORDER BY e.created_at DESC
        """, (user_id,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([{
        "id": str(r['id']),
        "fromName": r['author_name'],
        "relationship": r['relationship'],
        "text": r['text'],
        "avatarUrl": r['author_avatar_url'],
        "visible": r['visible'],
    } for r in rows])


@app.post("/api/users/<user_id>/endorsements")
def create_endorsement(user_id):
    author_id = get_current_user()
    if not author_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT role FROM users WHERE id = %s", (author_id,))
        author = cur.fetchone()
        if not author or author['role'] != 'professor':
            return jsonify({"error": "only professors can write endorsements"}), 403

        cur.execute("""
            INSERT INTO endorsements (recipient_id, author_id, relationship, text, visible)
            VALUES (%s, %s, %s, %s, TRUE)
            RETURNING id, relationship, text, visible
        """, (user_id, author_id, data.get("relationship", "Professor"), text))

        result = cur.fetchone()
        conn.commit()

        cur.execute("SELECT name, avatar_url FROM users WHERE id = %s", (author_id,))
        author_info = cur.fetchone()

        return jsonify({
            "status": "created",
            "endorsement": {
                "id": str(result['id']),
                "fromName": author_info['name'],
                "relationship": result['relationship'],
                "text": result['text'],
                "avatarUrl": author_info['avatar_url'],
                "visible": result['visible'],
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.patch("/api/endorsements/<endorsement_id>")
def update_endorsement(endorsement_id):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    if "visible" not in data:
        return jsonify({"error": "visible is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE endorsements SET visible = %s
            WHERE id = %s AND recipient_id = %s
            RETURNING id, visible
        """, (bool(data.get("visible")), endorsement_id, current_user))

        updated = cur.fetchone()
        conn.commit()

        if not updated:
            return jsonify({"error": "not found or unauthorized"}), 404

        return jsonify({"status": "updated", "id": str(updated['id']), "visible": updated['visible']})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# FEED POSTS
# -----------------------------
@app.get("/api/feed")
def list_feed_posts():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT p.id, p.content, p.created_at,
               u.name AS author_name, u.headline AS author_headline, u.avatar_url AS author_avatar_url
        FROM posts p
        JOIN users u ON u.id = p.author_id
        ORDER BY p.created_at DESC
        LIMIT 50
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([{
        "id": str(r['id']),
        "authorName": r['author_name'],
        "authorHeadline": r['author_headline'],
        "authorAvatarUrl": r['author_avatar_url'],
        "content": r['content'],
        "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
    } for r in rows])


@app.post("/api/feed")
def create_feed_post():
    author_id = get_current_user()
    if not author_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO posts (author_id, content)
            VALUES (%s, %s)
            RETURNING id, content, created_at
        """, (author_id, content))

        result = cur.fetchone()
        conn.commit()

        cur.execute("SELECT name, headline, avatar_url FROM users WHERE id = %s", (author_id,))
        author_info = cur.fetchone()

        return jsonify({
            "status": "created",
            "post": {
                "id": str(result['id']),
                "authorName": author_info['name'],
                "authorHeadline": author_info['headline'],
                "authorAvatarUrl": author_info['avatar_url'],
                "content": result['content'],
                "createdAt": result['created_at'].isoformat() if result['created_at'] else None,
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# JOBS
# -----------------------------
@app.get("/api/jobs")
def list_jobs():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, company, location, salary_range,
        job_type, description, url, tags, created_at, is_active
        FROM jobs
        WHERE is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 50
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    jobs = []
    for r in rows:
        jobs.append({
            "id": str(r['id']),
            "title": r['title'],
            "company": r['company'],
            "location": r['location'],
            "salaryRange": r['salary_range'],
            "jobType": r['job_type'],
            "description": r['description'],
            "url": r['url'],
            "tags": r['tags'] or [],
            "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
            "isActive": r['is_active']
        })

    return jsonify(jobs)


# -----------------------------
# CREATE JOB
# -----------------------------
@app.post("/api/jobs")
def create_job():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO jobs (title, company, location, salary_range, job_type, description, url, tags, posted_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            data.get("title"), data.get("company"), data.get("location"),
            data.get("salaryRange"), data.get("jobType"), data.get("description"),
            data.get("url"), data.get("tags"), user_id
        ))

        result = cur.fetchone()
        conn.commit()
        return jsonify({"status": "job created", "job_id": str(result['id'])})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# APPLY TO JOB
# -----------------------------
@app.post("/api/apply")
def apply_to_job():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json
    job_id = data.get("job_id")

    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO applications (user_id, job_id)
            VALUES (%s, %s)
            RETURNING id
        """, (user_id, job_id))

        result = cur.fetchone()
        conn.commit()
        return jsonify({"status": "applied", "application_id": str(result['id'])})

    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "already applied"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# MY APPLICATIONS
# -----------------------------
@app.get("/api/my-applications")
def my_applications():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT a.id, a.job_id, a.status, a.created_at,
               j.title, j.company, j.location, j.job_type
        FROM applications a
        JOIN jobs j ON a.job_id = j.id
        WHERE a.user_id = %s
        ORDER BY a.created_at DESC
    """, (user_id,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([{
        "id": str(r['id']),
        "jobId": str(r['job_id']),
        "status": r['status'],
        "appliedAt": r['created_at'].isoformat(),
        "job": {
            "title": r['title'],
            "company": r['company'],
            "location": r['location'],
            "jobType": r['job_type'],
        }
    } for r in rows])


# -----------------------------
# SCHOLARSHIPS
# -----------------------------
@app.get("/api/scholarships")
def list_scholarships():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, provider, amount, deadline,
        description, url, tags, created_at, is_active
        FROM scholarships
        WHERE is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 50
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    scholarships = []
    for r in rows:
        scholarships.append({
            "id": str(r['id']),
            "title": r['title'],
            "provider": r['provider'],
            "amount": r['amount'],
            "deadline": r['deadline'].isoformat() if r['deadline'] else None,
            "description": r['description'],
            "url": r['url'],
            "tags": r['tags'] or [],
            "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
            "isActive": r['is_active']
        })

    return jsonify(scholarships)


# -----------------------------
# CREATE SCHOLARSHIP
# -----------------------------
@app.post("/api/scholarships")
def create_scholarship():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json

    if not data.get("title") or not data.get("provider"):
        return jsonify({"error": "title and provider are required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO scholarships (title, provider, amount, deadline, description, url, tags, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
            RETURNING id
        """, (
            data.get("title"), data.get("provider"), data.get("amount"),
            data.get("deadline"), data.get("description"),
            data.get("url"), data.get("tags", []),
        ))

        result = cur.fetchone()
        conn.commit()
        return jsonify({"status": "scholarship created", "scholarship_id": str(result['id'])})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# DELETE SCHOLARSHIP
# -----------------------------
@app.delete("/api/scholarships/<scholarship_id>")
def delete_scholarship(scholarship_id):
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("UPDATE scholarships SET is_active = FALSE WHERE id = %s", (scholarship_id,))
        conn.commit()
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# -----------------------------
# Swagger Docs
# -----------------------------
SWAGGER_URL = "/docs"
API_URL = "/static/swagger.json"

swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={"app_name": "Graduate API"}
)

app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)