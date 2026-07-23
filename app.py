import os
import datetime
import secrets
import bcrypt
import jwt
import pyotp
import psycopg2
from psycopg2.extras import RealDictCursor, Json

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from flask_swagger_ui import get_swaggerui_blueprint
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

import emails


app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 75 * 1024 * 1024  # 75MB - a 50MB spotlight video becomes ~67MB once base64-encoded; still caps abusive oversized bodies
# Railway sits in front of the app as a single reverse-proxy hop - without this,
# request.remote_addr (and therefore rate limiting) would see every user as the proxy's own IP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

limiter = Limiter(get_remote_address, app=app, default_limits=["300 per minute"], storage_uri="memory://")

ALLOWED_ORIGINS = {
    "https://thegraduate.io",
    "https://www.thegraduate.io",
    "http://localhost:5173",
    "http://localhost:3000",
}

CORS(app, origins=list(ALLOWED_ORIGINS), supports_credentials=False)


def _cors_origin():
    origin = request.headers.get("Origin")
    return origin if origin in ALLOWED_ORIGINS else None


@app.after_request
def add_security_headers(response):
    origin = _cors_origin()
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

@app.before_request
def handle_preflight():
    if request.method == 'OPTIONS':
        response = app.make_response('')
        origin = _cors_origin()
        if origin:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        response.status_code = 200
        return response

JWT_SECRET = os.environ["JWT_SECRET"]  # no insecure fallback - fail loudly if unset rather than sign tokens with a guessable default
DATABASE_URL = os.environ.get("DATABASE_URL")


# -----------------------------
# Database Connection
# -----------------------------
# This used to go through a ThreadedConnectionPool, added to avoid opening a fresh Postgres
# connection per request under load. In practice it introduced a worse failure mode than the
# one it fixed: the pool holds a single process-wide lock across every getconn()/putconn()
# call, including the moment a brand new physical connection is opened. When that connection
# attempt is ever slow or stuck, it holds the lock indefinitely and every other thread in the
# worker blocks behind it too - including requests that don't touch a new connection at all
# (reproduced in production twice: one stuck request took the entire worker down, requiring a
# manual restart both times, with nothing unusual visible on the Postgres side). A plain
# connection per request can't wedge other requests this way: a slow/stuck connect only ever
# blocks the one request making it. connect_timeout bounds how long that can take.
def get_conn():
    return psycopg2.connect(
        DATABASE_URL, cursor_factory=RealDictCursor, connect_timeout=5
    )


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

        CREATE TABLE IF NOT EXISTS connections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            requester_id UUID REFERENCES users(id) NOT NULL,
            recipient_id UUID REFERENCES users(id) NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(requester_id, recipient_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sender_id UUID REFERENCES users(id) NOT NULL,
            recipient_id UUID REFERENCES users(id) NOT NULL,
            text TEXT NOT NULL,
            read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
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

        CREATE TABLE IF NOT EXISTS post_likes (
            post_id UUID REFERENCES posts(id) NOT NULL,
            user_id UUID REFERENCES users(id) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (post_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS post_comments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            post_id UUID REFERENCES posts(id) NOT NULL,
            author_id UUID REFERENCES users(id) NOT NULL,
            parent_comment_id UUID REFERENCES post_comments(id),
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS spotlights (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            title TEXT,
            video_url TEXT NOT NULL,
            thumbnail_url TEXT,
            duration_seconds INTEGER,
            views INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_matches_sent BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE post_comments ADD COLUMN IF NOT EXISTS parent_comment_id UUID REFERENCES post_comments(id);")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS background_url TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS active_status TEXT DEFAULT 'online';")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS projects JSONB DEFAULT '[]'::jsonb;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_badge TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMP;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS two_factor_secret TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS two_factor_enabled BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS expected_graduation_date DATE;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS graduation_reminder_sent BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS grad_year INTEGER;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS work_history JSONB DEFAULT '[]'::jsonb;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS endorsements_hidden BOOLEAN DEFAULT FALSE;")
    # One-time badge assignments (only applied if not already set, so a later admin
    # edit via the UI isn't silently reverted by a future deploy).
    cur.execute("UPDATE users SET custom_badge = %s WHERE name = %s AND custom_badge IS NULL;", ("CEO", "Gabrielle Branch"))
    cur.execute("UPDATE users SET custom_badge = %s WHERE name = %s AND custom_badge IS NULL;", ("CEO's Mom", "Vinnie Brown"))
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


def is_admin(user_id):
    if not user_id:
        return False
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return bool(row and row['role'] == 'admin')


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
@limiter.limit("5 per minute")
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

    if role in ("student", "professor") and not email.lower().strip().endswith(".edu"):
        return jsonify({"error": "Students and professors must sign up with a .edu email address."}), 400

    expected_graduation_date = None
    grad_year = None
    if role == "student":
        grad_month = data.get("expectedGraduationMonth")
        grad_year_input = data.get("expectedGraduationYear")
        if grad_month and grad_year_input:
            try:
                expected_graduation_date = datetime.date(int(grad_year_input), int(grad_month), 1)
                grad_year = int(grad_year_input)
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid graduation month/year."}), 400

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO users (name, email, password_hash, role, verification_status, expected_graduation_date, grad_year)
            VALUES (%s, %s, %s, %s, 'pending', %s, %s)
            RETURNING id;
        """, (name, email, password_hash, role, expected_graduation_date, grad_year))

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
@limiter.limit("15 per minute")
def login():
    data = request.json
    email = data.get("email")
    password = data.get("password")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, password_hash, name, role, two_factor_enabled FROM users WHERE email = %s", (email,))
    user = cur.fetchone()

    cur.close()
    conn.close()

    if not user:
        return jsonify({"error": "invalid credentials"}), 401

    if not bcrypt.checkpw(password.encode("utf-8"), user['password_hash'].encode("utf-8")):
        return jsonify({"error": "invalid credentials"}), 401

    if user['two_factor_enabled']:
        pending_token = jwt.encode({
            "pending_2fa_user_id": str(user['id']),
            "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
        }, JWT_SECRET, algorithm="HS256")
        return jsonify({"requires2FA": True, "pendingToken": pending_token})

    token = jwt.encode({
        "user_id": str(user['id']),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, JWT_SECRET, algorithm="HS256")

    return jsonify({
        "token": token,
        "user_id": str(user['id']),
        "name": user['name'],
        "role": user['role'],
        "email": email,
    })


@app.post("/api/login/2fa")
@limiter.limit("15 per minute")
def login_2fa():
    data = request.json or {}
    pending_token = data.get("pendingToken")
    code = (data.get("code") or "").strip()

    if not pending_token or not code:
        return jsonify({"error": "pendingToken and code are required"}), 400

    try:
        payload = jwt.decode(pending_token, JWT_SECRET, algorithms=["HS256"])
        user_id = payload.get("pending_2fa_user_id")
        if not user_id:
            raise ValueError("not a valid pending 2FA token")
    except Exception:
        return jsonify({"error": "This login session has expired. Please sign in again."}), 401

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, name, email, role, two_factor_secret, two_factor_enabled FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()

    cur.close()
    conn.close()

    if not user or not user['two_factor_enabled'] or not user['two_factor_secret']:
        return jsonify({"error": "Two-factor authentication is not set up for this account."}), 400

    totp = pyotp.TOTP(user['two_factor_secret'])
    if not totp.verify(code, valid_window=1):
        return jsonify({"error": "Invalid verification code."}), 401

    token = jwt.encode({
        "user_id": str(user['id']),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, JWT_SECRET, algorithm="HS256")

    return jsonify({
        "token": token,
        "user_id": str(user['id']),
        "name": user['name'],
        "email": user['email'],
        "role": user['role'],
    })


# -----------------------------
# TWO-FACTOR AUTHENTICATION
# -----------------------------
@app.get("/api/2fa/status")
def get_2fa_status():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT two_factor_enabled FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        return jsonify({"error": "user not found"}), 404

    return jsonify({"enabled": bool(user['two_factor_enabled'])})


@app.post("/api/2fa/setup")
def setup_2fa():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            return jsonify({"error": "user not found"}), 404

        secret = pyotp.random_base32()
        cur.execute("UPDATE users SET two_factor_secret = %s WHERE id = %s", (secret, user_id))
        conn.commit()

        otpauth_url = pyotp.TOTP(secret).provisioning_uri(name=user['email'], issuer_name="Graduate")

        return jsonify({"secret": secret, "otpauthUrl": otpauth_url})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.post("/api/2fa/verify-setup")
@limiter.limit("10 per minute")
def verify_setup_2fa():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT two_factor_secret FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user or not user['two_factor_secret']:
            return jsonify({"error": "Start two-factor setup first."}), 400

        totp = pyotp.TOTP(user['two_factor_secret'])
        if not totp.verify(code, valid_window=1):
            return jsonify({"error": "Invalid verification code."}), 401

        cur.execute("UPDATE users SET two_factor_enabled = TRUE WHERE id = %s", (user_id,))
        conn.commit()

        return jsonify({"status": "enabled"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.post("/api/2fa/disable")
@limiter.limit("10 per minute")
def disable_2fa():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT two_factor_secret, two_factor_enabled FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user or not user['two_factor_enabled'] or not user['two_factor_secret']:
            return jsonify({"error": "Two-factor authentication is not enabled."}), 400

        totp = pyotp.TOTP(user['two_factor_secret'])
        if not totp.verify(code, valid_window=1):
            return jsonify({"error": "Invalid verification code."}), 401

        cur.execute("UPDATE users SET two_factor_enabled = FALSE, two_factor_secret = NULL WHERE id = %s", (user_id,))
        conn.commit()

        return jsonify({"status": "disabled"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# CHANGE PASSWORD (authenticated, knows current password)
# -----------------------------
@app.post("/api/change-password")
@limiter.limit("10 per minute")
def change_password():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    current_password = data.get("currentPassword")
    new_password = data.get("newPassword")

    if not current_password or not new_password:
        return jsonify({"error": "currentPassword and newPassword are required"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "password must be at least 6 characters"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            return jsonify({"error": "user not found"}), 404

        if not bcrypt.checkpw(current_password.encode("utf-8"), user['password_hash'].encode("utf-8")):
            return jsonify({"error": "Current password is incorrect."}), 401

        new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode()
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, user_id))
        conn.commit()

        return jsonify({"status": "updated"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# CHANGE EMAIL (authenticated, password-confirmed)
# -----------------------------
@app.post("/api/change-email")
@limiter.limit("10 per minute")
def change_email():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    new_email = (data.get("newEmail") or "").strip()
    password = data.get("password")

    if not new_email or not password:
        return jsonify({"error": "newEmail and password are required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            return jsonify({"error": "user not found"}), 404

        if not bcrypt.checkpw(password.encode("utf-8"), user['password_hash'].encode("utf-8")):
            return jsonify({"error": "Incorrect password."}), 401

        cur.execute("SELECT id FROM users WHERE email = %s AND id != %s", (new_email, user_id))
        if cur.fetchone():
            return jsonify({"error": "That email is already in use by another account."}), 409

        cur.execute("UPDATE users SET email = %s WHERE id = %s", (new_email, user_id))
        conn.commit()

        return jsonify({"status": "updated", "email": new_email})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# SELF-SERVICE STUDENT -> GRADUATE ROLE SWITCH
# (deliberately one-directional and narrow - no general role-change
# endpoint exists, to avoid any privilege-escalation surface)
# -----------------------------
@app.post("/api/graduate")
def switch_to_graduate():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            return jsonify({"error": "user not found"}), 404
        if user['role'] != 'student':
            return jsonify({"error": "Only student accounts can switch to Graduate this way."}), 400

        cur.execute("UPDATE users SET role = 'graduate' WHERE id = %s", (user_id,))
        conn.commit()

        return jsonify({"status": "updated", "role": "graduate"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# DATA EXPORT (self-only)
# -----------------------------
@app.get("/api/users/<user_id>/export")
def export_user_data(user_id):
    current_user = get_current_user()
    if not current_user or current_user != user_id:
        return jsonify({"error": "unauthorized"}), 401

    conn = get_conn()
    cur = conn.cursor()

    def rows_as_dicts(query, params):
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT id, name, email, role, headline, bio, school, major, location,
               avatar_url, background_url, custom_badge, grad_year, expected_graduation_date,
               skills, projects, work_history, verification_status, active_status,
               two_factor_enabled, created_at
        FROM users WHERE id = %s
    """, (user_id,))
    profile = cur.fetchone()
    if not profile:
        cur.close()
        conn.close()
        return jsonify({"error": "user not found"}), 404

    data = {
        "profile": dict(profile),
        "posts": rows_as_dicts("SELECT id, content, created_at FROM posts WHERE author_id = %s", (user_id,)),
        "postComments": rows_as_dicts("SELECT id, post_id, text, created_at FROM post_comments WHERE author_id = %s", (user_id,)),
        "messagesSent": rows_as_dicts("SELECT id, recipient_id, text, created_at FROM messages WHERE sender_id = %s", (user_id,)),
        "messagesReceived": rows_as_dicts("SELECT id, sender_id, text, created_at FROM messages WHERE recipient_id = %s", (user_id,)),
        "connections": rows_as_dicts("SELECT id, requester_id, recipient_id, status, created_at FROM connections WHERE requester_id = %s OR recipient_id = %s", (user_id, user_id)),
        "endorsementsReceived": rows_as_dicts("SELECT id, author_id, relationship, text, visible, created_at FROM endorsements WHERE recipient_id = %s", (user_id,)),
        "endorsementsWritten": rows_as_dicts("SELECT id, recipient_id, relationship, text, created_at FROM endorsements WHERE author_id = %s", (user_id,)),
        "jobApplications": rows_as_dicts("SELECT id, job_id, status, created_at FROM applications WHERE user_id = %s", (user_id,)),
        "jobsPosted": rows_as_dicts("SELECT id, title, company, location, created_at FROM jobs WHERE posted_by = %s", (user_id,)),
        "spotlights": rows_as_dicts("SELECT id, title, video_url, thumbnail_url, duration_seconds, views, is_active, created_at FROM spotlights WHERE user_id = %s", (user_id,)),
    }

    for record_list in data.values():
        if isinstance(record_list, list):
            for record in record_list:
                for key, value in record.items():
                    if hasattr(value, "isoformat"):
                        record[key] = value.isoformat()
    for key, value in data["profile"].items():
        if hasattr(value, "isoformat"):
            data["profile"][key] = value.isoformat()

    cur.close()
    conn.close()

    response = jsonify(data)
    response.headers["Content-Disposition"] = "attachment; filename=graduate-data-export.json"
    return response


# -----------------------------
# ACCOUNT DELETION (self-only, password-confirmed)
# -----------------------------
@app.delete("/api/users/<user_id>")
@limiter.limit("5 per minute")
def delete_account(user_id):
    current_user = get_current_user()
    if not current_user or current_user != user_id:
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    password = data.get("password")
    if not password:
        return jsonify({"error": "password is required to confirm account deletion"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            return jsonify({"error": "user not found"}), 404
        if not bcrypt.checkpw(password.encode("utf-8"), user['password_hash'].encode("utf-8")):
            return jsonify({"error": "Incorrect password."}), 401

        # No ON DELETE CASCADE anywhere in this schema, and jobs.posted_by has no cascade
        # either - clear/delete every dependent row in FK-safe order before the user row itself.
        cur.execute("UPDATE jobs SET posted_by = NULL WHERE posted_by = %s", (user_id,))
        cur.execute("DELETE FROM post_comments WHERE post_id IN (SELECT id FROM posts WHERE author_id = %s)", (user_id,))
        cur.execute("DELETE FROM post_comments WHERE author_id = %s", (user_id,))
        cur.execute("DELETE FROM post_likes WHERE post_id IN (SELECT id FROM posts WHERE author_id = %s)", (user_id,))
        cur.execute("DELETE FROM post_likes WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM posts WHERE author_id = %s", (user_id,))
        cur.execute("DELETE FROM endorsements WHERE recipient_id = %s OR author_id = %s", (user_id, user_id))
        cur.execute("DELETE FROM messages WHERE sender_id = %s OR recipient_id = %s", (user_id, user_id))
        cur.execute("DELETE FROM connections WHERE requester_id = %s OR recipient_id = %s", (user_id, user_id))
        cur.execute("DELETE FROM applications WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM verification_requests WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()

        return jsonify({"status": "deleted"})

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# PASSWORD RESET
# -----------------------------
@app.post("/api/forgot-password")
@limiter.limit("5 per minute")
def forgot_password():
    data = request.json or {}
    email = data.get("email")
    if not email:
        return jsonify({"error": "email is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT id, name FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

        # Always return the same response whether or not the email is registered,
        # so this endpoint can't be used to check which emails have accounts.
        if user:
            token = secrets.token_urlsafe(32)
            expires = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            cur.execute(
                "UPDATE users SET reset_token = %s, reset_token_expires = %s WHERE id = %s",
                (token, expires, user['id'])
            )
            conn.commit()

            try:
                reset_url = f"{emails.APP_URL}?view=reset-password&token={token}"
                emails.send_password_reset_email(user['name'], email, reset_url)
            except Exception as e:
                print(f"Password reset email error: {e}")

        return jsonify({"status": "sent"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.post("/api/reset-password")
@limiter.limit("10 per minute")
def reset_password():
    data = request.json or {}
    token = data.get("token")
    password = data.get("password")

    if not token or not password:
        return jsonify({"error": "token and password are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 characters"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT id FROM users WHERE reset_token = %s AND reset_token_expires > NOW()",
            (token,)
        )
        user = cur.fetchone()

        if not user:
            return jsonify({"error": "This reset link is invalid or has expired."}), 400

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()
        cur.execute(
            "UPDATE users SET password_hash = %s, reset_token = NULL, reset_token_expires = NULL WHERE id = %s",
            (password_hash, user['id'])
        )
        conn.commit()

        return jsonify({"status": "updated"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# USERS
# -----------------------------
@app.get("/api/users")
def list_users():
    # The Spotlight badge (hasSpotlight) is only meaningful to recruiters/employers/admins -
    # everyone else gets it as always-false, so students/graduates get no signal at all about
    # who else has one, matching the rule that only recruiters/employers/admins can see
    # anything Spotlight-related about other members.
    viewer_id = get_current_user()

    conn = get_conn()
    cur = conn.cursor()

    viewer_can_see_spotlights = False
    if viewer_id:
        cur.execute("SELECT role FROM users WHERE id = %s", (viewer_id,))
        viewer = cur.fetchone()
        viewer_can_see_spotlights = bool(viewer and viewer['role'] in SPOTLIGHT_VIEWER_ROLES)

    cur.execute("""
        SELECT u.id, u.name, u.role, u.verification_status, u.headline, u.school,
        u.major, u.location, u.avatar_url, u.background_url, u.bio, u.active_status,
        u.projects, u.custom_badge, u.skills, u.grad_year, u.expected_graduation_date, u.work_history, u.created_at,
        u.endorsements_hidden,
        EXISTS(SELECT 1 FROM spotlights s WHERE s.user_id = u.id AND s.is_active = TRUE) AS has_spotlight
        FROM users u
        ORDER BY u.created_at DESC
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
            "backgroundUrl": r['background_url'],
            "bio": r['bio'],
            "activeStatus": r['active_status'] or 'online',
            "projects": r['projects'] or [],
            "customBadge": r['custom_badge'],
            "skills": r['skills'] or [],
            "gradYear": r['grad_year'],
            "expectedGraduationDate": r['expected_graduation_date'].isoformat() if r['expected_graduation_date'] else None,
            "workHistory": r['work_history'] or [],
            "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
            "hasSpotlight": bool(r['has_spotlight']) if viewer_can_see_spotlights else False,
            "endorsementsHidden": bool(r['endorsements_hidden']),
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
                background_url = COALESCE(%s, background_url),
                bio = COALESCE(%s, bio),
                active_status = COALESCE(%s, active_status),
                projects = COALESCE(%s, projects),
                grad_year = COALESCE(%s, grad_year),
                work_history = COALESCE(%s, work_history),
                skills = COALESCE(%s, skills),
                endorsements_hidden = COALESCE(%s, endorsements_hidden)
            WHERE id = %s
            RETURNING id, name, email, role, headline, school, major, location,
                avatar_url, background_url, bio, active_status, projects, custom_badge, grad_year, work_history, skills, endorsements_hidden
        """, (
            data.get("name"),
            data.get("headline"),
            data.get("school"),
            data.get("major"),
            data.get("location"),
            data.get("avatarUrl"),
            data.get("backgroundUrl"),
            data.get("bio"),
            data.get("activeStatus"),
            Json(data.get("projects")) if data.get("projects") is not None else None,
            data.get("gradYear"),
            Json(data.get("workHistory")) if data.get("workHistory") is not None else None,
            data.get("skills"),
            data.get("endorsementsHidden"),
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
# ADMIN: VERIFICATION
# -----------------------------
@app.patch("/api/users/<user_id>/verification")
def update_user_verification(user_id):
    current_user = get_current_user()
    if not is_admin(current_user):
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    status = data.get("status")
    if status not in ("approved", "rejected", "pending"):
        return jsonify({"error": "status must be approved, rejected, or pending"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE users SET verification_status = %s
            WHERE id = %s
            RETURNING id, verification_status
        """, (status, user_id))

        updated = cur.fetchone()
        conn.commit()

        if not updated:
            return jsonify({"error": "user not found"}), 404

        return jsonify({"status": "updated", "id": str(updated['id']), "verificationStatus": updated['verification_status']})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# ADMIN: CUSTOM BADGE
# -----------------------------
@app.patch("/api/users/<user_id>/badge")
def update_user_badge(user_id):
    current_user = get_current_user()
    if not is_admin(current_user):
        return jsonify({"error": "unauthorized"}), 401

    data = request.json or {}
    badge = data.get("customBadge")
    if badge is not None:
        badge = badge.strip() or None

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE users SET custom_badge = %s
            WHERE id = %s
            RETURNING id, custom_badge
        """, (badge, user_id))

        updated = cur.fetchone()
        conn.commit()

        if not updated:
            return jsonify({"error": "user not found"}), 404

        return jsonify({"status": "updated", "id": str(updated['id']), "customBadge": updated['custom_badge']})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# NETWORK CONNECTIONS
# -----------------------------
@app.get("/api/users/<user_id>/connection-status")
def get_connection_status(user_id):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"status": "none"})
    if current_user == user_id:
        return jsonify({"status": "self"})

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, requester_id, recipient_id, status FROM connections
        WHERE (requester_id = %s AND recipient_id = %s) OR (requester_id = %s AND recipient_id = %s)
    """, (current_user, user_id, user_id, current_user))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({"status": "none"})
    if row['status'] == 'accepted':
        return jsonify({"status": "connected", "connectionId": str(row['id'])})
    if row['status'] == 'pending':
        if str(row['requester_id']) == current_user:
            return jsonify({"status": "pending_sent", "connectionId": str(row['id'])})
        return jsonify({"status": "pending_received", "connectionId": str(row['id'])})
    return jsonify({"status": "none"})


@app.post("/api/users/<user_id>/connect")
def request_connection(user_id):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "authentication required"}), 401
    if current_user == user_id:
        return jsonify({"error": "cannot connect with yourself"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        # If the other person already requested us, connecting back accepts their request
        # instead of creating a conflicting second row.
        cur.execute("""
            SELECT id FROM connections
            WHERE requester_id = %s AND recipient_id = %s AND status = 'pending'
        """, (user_id, current_user))
        reverse = cur.fetchone()

        if reverse:
            cur.execute("UPDATE connections SET status = 'accepted' WHERE id = %s RETURNING id", (reverse['id'],))
            result = cur.fetchone()
            conn.commit()
            return jsonify({"status": "connected", "connectionId": str(result['id'])})

        cur.execute("""
            INSERT INTO connections (requester_id, recipient_id, status)
            VALUES (%s, %s, 'pending')
            ON CONFLICT (requester_id, recipient_id) DO UPDATE SET status = 'pending'
            RETURNING id
        """, (current_user, user_id))
        result = cur.fetchone()
        conn.commit()
        return jsonify({"status": "pending_sent", "connectionId": str(result['id'])})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.patch("/api/connections/<connection_id>")
def respond_to_connection(connection_id):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    new_status = data.get("status")
    if new_status not in ("accepted", "declined"):
        return jsonify({"error": "status must be accepted or declined"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE connections SET status = %s
            WHERE id = %s AND recipient_id = %s AND status = 'pending'
            RETURNING id, status
        """, (new_status, connection_id, current_user))

        updated = cur.fetchone()
        conn.commit()

        if not updated:
            return jsonify({"error": "not found or unauthorized"}), 404

        return jsonify({"status": "updated", "id": str(updated['id']), "connectionStatus": updated['status']})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.get("/api/connections/requests")
def list_incoming_requests():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT c.id, c.created_at, u.id AS requester_id, u.name AS requester_name, u.avatar_url AS requester_avatar_url
        FROM connections c
        JOIN users u ON u.id = c.requester_id
        WHERE c.recipient_id = %s AND c.status = 'pending'
        ORDER BY c.created_at DESC
    """, (current_user,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([{
        "connectionId": str(r['id']),
        "requesterId": str(r['requester_id']),
        "requesterName": r['requester_name'],
        "requesterAvatarUrl": r['requester_avatar_url'],
        "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
    } for r in rows])


@app.get("/api/users/<user_id>/connections")
def list_user_connections(user_id):
    # Private: a user's connection list is only visible to themselves.
    current_user = get_current_user()
    if not current_user or current_user != user_id:
        return jsonify({"error": "unauthorized"}), 401

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT c.id, c.created_at,
               CASE WHEN c.requester_id = %s THEN c.recipient_id ELSE c.requester_id END AS other_id
        FROM connections c
        WHERE c.status = 'accepted' AND (c.requester_id = %s OR c.recipient_id = %s)
        ORDER BY c.created_at DESC
    """, (user_id, user_id, user_id))
    rows = cur.fetchall()

    results = []
    for r in rows:
        cur.execute("SELECT id, name, headline, school, avatar_url, role FROM users WHERE id = %s", (r['other_id'],))
        u = cur.fetchone()
        if u:
            results.append({
                "connectionId": str(r['id']),
                "userId": str(u['id']),
                "name": u['name'],
                "headline": u['headline'],
                "school": u['school'],
                "avatarUrl": u['avatar_url'],
                "role": u['role'],
                "connectedAt": r['created_at'].isoformat() if r['created_at'] else None,
            })

    cur.close()
    conn.close()

    return jsonify(results)


# -----------------------------
# MESSAGES
# -----------------------------
@app.get("/api/messages/threads")
def list_message_threads():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        WITH convo AS (
            SELECT
                CASE WHEN sender_id = %(me)s THEN recipient_id ELSE sender_id END AS other_id,
                text, created_at,
                ROW_NUMBER() OVER (
                    PARTITION BY CASE WHEN sender_id = %(me)s THEN recipient_id ELSE sender_id END
                    ORDER BY created_at DESC
                ) AS rn
            FROM messages
            WHERE sender_id = %(me)s OR recipient_id = %(me)s
        )
        SELECT c.other_id, c.text AS last_message, c.created_at AS last_timestamp,
               u.name, u.avatar_url,
               EXISTS(
                   SELECT 1 FROM messages m2
                   WHERE m2.sender_id = c.other_id AND m2.recipient_id = %(me)s AND m2.read = FALSE
               ) AS unread,
               EXISTS(
                   SELECT 1 FROM connections co
                   WHERE co.status = 'accepted'
                   AND ((co.requester_id = %(me)s AND co.recipient_id = c.other_id)
                     OR (co.recipient_id = %(me)s AND co.requester_id = c.other_id))
               ) AS is_connected
        FROM convo c
        JOIN users u ON u.id = c.other_id
        WHERE c.rn = 1
        ORDER BY c.created_at DESC
    """, {"me": current_user})

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([{
        "participantId": str(r['other_id']),
        "participantName": r['name'],
        "participantAvatarUrl": r['avatar_url'],
        "lastMessage": r['last_message'],
        "timestamp": r['last_timestamp'].isoformat() if r['last_timestamp'] else None,
        "unread": r['unread'],
        "isConnected": bool(r['is_connected']),
    } for r in rows])


@app.get("/api/messages/<other_user_id>")
def get_message_thread(other_user_id):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, sender_id, recipient_id, text, created_at
        FROM messages
        WHERE (sender_id = %s AND recipient_id = %s) OR (sender_id = %s AND recipient_id = %s)
        ORDER BY created_at ASC
    """, (current_user, other_user_id, other_user_id, current_user))
    rows = cur.fetchall()

    # Opening a thread marks the other person's messages to us as read.
    cur.execute("""
        UPDATE messages SET read = TRUE
        WHERE sender_id = %s AND recipient_id = %s AND read = FALSE
    """, (other_user_id, current_user))
    conn.commit()

    cur.close()
    conn.close()

    return jsonify([{
        "id": str(r['id']),
        "senderId": str(r['sender_id']),
        "text": r['text'],
        "timestamp": r['created_at'].isoformat() if r['created_at'] else None,
    } for r in rows])


@app.post("/api/messages/<other_user_id>")
def send_message(other_user_id):
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO messages (sender_id, recipient_id, text)
            VALUES (%s, %s, %s)
            RETURNING id, created_at
        """, (current_user, other_user_id, text))

        result = cur.fetchone()
        conn.commit()

        return jsonify({
            "status": "sent",
            "message": {
                "id": str(result['id']),
                "senderId": current_user,
                "text": text,
                "timestamp": result['created_at'].isoformat() if result['created_at'] else None,
            }
        })

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
        "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
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
            RETURNING id, relationship, text, visible, created_at
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
                "createdAt": result['created_at'].isoformat() if result['created_at'] else None,
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
    current_user = get_current_user()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT p.id, p.content, p.created_at, u.id AS author_id, u.school AS author_school,
               u.name AS author_name, u.headline AS author_headline, u.avatar_url AS author_avatar_url,
               (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS likes_count,
               (SELECT COUNT(*) FROM post_comments pc WHERE pc.post_id = p.id) AS comments_count,
               EXISTS(SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = %s) AS liked_by_me
        FROM posts p
        JOIN users u ON u.id = p.author_id
        ORDER BY p.created_at DESC
        LIMIT 50
    """, (current_user,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([{
        "id": str(r['id']),
        "authorId": str(r['author_id']),
        "authorSchool": r['author_school'],
        "authorName": r['author_name'],
        "authorHeadline": r['author_headline'],
        "authorAvatarUrl": r['author_avatar_url'],
        "content": r['content'],
        "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
        "likesCount": r['likes_count'],
        "commentsCount": r['comments_count'],
        "likedByMe": r['liked_by_me'],
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

        cur.execute("SELECT name, headline, avatar_url, school FROM users WHERE id = %s", (author_id,))
        author_info = cur.fetchone()

        return jsonify({
            "status": "created",
            "post": {
                "id": str(result['id']),
                "authorId": author_id,
                "authorSchool": author_info['school'],
                "authorName": author_info['name'],
                "authorHeadline": author_info['headline'],
                "authorAvatarUrl": author_info['avatar_url'],
                "content": result['content'],
                "createdAt": result['created_at'].isoformat() if result['created_at'] else None,
                "likesCount": 0,
                "commentsCount": 0,
                "likedByMe": False,
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.patch("/api/feed/<post_id>")
def edit_feed_post(post_id):
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT author_id FROM posts WHERE id = %s", (post_id,))
        post = cur.fetchone()
        if not post:
            return jsonify({"error": "post not found"}), 404
        if str(post['author_id']) != user_id:
            return jsonify({"error": "unauthorized"}), 401

        cur.execute(
            "UPDATE posts SET content = %s WHERE id = %s RETURNING content",
            (content, post_id)
        )
        updated = cur.fetchone()
        conn.commit()

        return jsonify({"status": "updated", "content": updated['content']})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.delete("/api/feed/<post_id>")
def delete_feed_post(post_id):
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT author_id FROM posts WHERE id = %s", (post_id,))
        post = cur.fetchone()
        if not post:
            return jsonify({"error": "post not found"}), 404
        if str(post['author_id']) != user_id:
            return jsonify({"error": "unauthorized"}), 401

        # No ON DELETE CASCADE on these foreign keys, so clear child rows first.
        cur.execute("DELETE FROM post_comments WHERE post_id = %s", (post_id,))
        cur.execute("DELETE FROM post_likes WHERE post_id = %s", (post_id,))
        cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))
        conn.commit()

        return jsonify({"status": "deleted"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# FEED POST LIKES
# -----------------------------
@app.post("/api/feed/<post_id>/like")
def toggle_post_like(post_id):
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT 1 FROM post_likes WHERE post_id = %s AND user_id = %s", (post_id, user_id))
        already_liked = cur.fetchone()

        if already_liked:
            cur.execute("DELETE FROM post_likes WHERE post_id = %s AND user_id = %s", (post_id, user_id))
            liked = False
        else:
            cur.execute("INSERT INTO post_likes (post_id, user_id) VALUES (%s, %s)", (post_id, user_id))
            liked = True

        conn.commit()

        cur.execute("SELECT COUNT(*) AS count FROM post_likes WHERE post_id = %s", (post_id,))
        count = cur.fetchone()['count']

        return jsonify({"status": "updated", "likedByMe": liked, "likesCount": count})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# FEED POST COMMENTS
# -----------------------------
@app.get("/api/feed/<post_id>/comments")
def list_post_comments(post_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT c.id, c.text, c.created_at, c.parent_comment_id, u.name AS author_name, u.avatar_url AS author_avatar_url
        FROM post_comments c
        JOIN users u ON u.id = c.author_id
        WHERE c.post_id = %s
        ORDER BY c.created_at ASC
    """, (post_id,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify([{
        "id": str(r['id']),
        "authorName": r['author_name'],
        "authorAvatarUrl": r['author_avatar_url'],
        "text": r['text'],
        "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
        "parentCommentId": str(r['parent_comment_id']) if r['parent_comment_id'] else None,
    } for r in rows])


@app.post("/api/feed/<post_id>/comments")
def create_post_comment(post_id):
    author_id = get_current_user()
    if not author_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json or {}
    text = (data.get("text") or "").strip()
    parent_comment_id = data.get("parentCommentId")
    if not text:
        return jsonify({"error": "text is required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO post_comments (post_id, author_id, parent_comment_id, text)
            VALUES (%s, %s, %s, %s)
            RETURNING id, text, created_at, parent_comment_id
        """, (post_id, author_id, parent_comment_id, text))

        result = cur.fetchone()
        conn.commit()

        cur.execute("SELECT name, avatar_url FROM users WHERE id = %s", (author_id,))
        author_info = cur.fetchone()

        return jsonify({
            "status": "created",
            "comment": {
                "id": str(result['id']),
                "authorName": author_info['name'],
                "authorAvatarUrl": author_info['avatar_url'],
                "text": result['text'],
                "createdAt": result['created_at'].isoformat() if result['created_at'] else None,
                "parentCommentId": str(result['parent_comment_id']) if result['parent_comment_id'] else None,
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

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
    poster = cur.fetchone()
    if not poster or poster['role'] not in ('employer', 'recruiter', 'admin'):
        cur.close()
        conn.close()
        return jsonify({"error": "Only employer or recruiter accounts can post jobs."}), 403

    data = request.json

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


@app.patch("/api/jobs/<job_id>")
def update_job(job_id):
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT posted_by FROM jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if not job:
            return jsonify({"error": "job not found"}), 404
        if str(job['posted_by']) != user_id and not is_admin(user_id):
            return jsonify({"error": "unauthorized"}), 401

        data = request.json or {}
        cur.execute("""
            UPDATE jobs SET is_active = COALESCE(%s, is_active)
            WHERE id = %s
            RETURNING id, is_active
        """, (data.get("isActive"), job_id))

        updated = cur.fetchone()
        conn.commit()

        return jsonify({"status": "updated", "id": str(updated['id']), "isActive": updated['is_active']})

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
    if not is_admin(user_id):
        return jsonify({"error": "unauthorized"}), 401

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
    if not is_admin(user_id):
        return jsonify({"error": "unauthorized"}), 401

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
# SPOTLIGHTS
# -----------------------------
# Short video pitches. Students/graduates/admins upload one (uploading a new one replaces
# any existing one - only one active spotlight per user); recruiters/employers/admins browse
# them. Students and graduates never get to browse other people's - the whole point is a
# recruiter-facing discovery surface, not another social feed.
SPOTLIGHT_VIEWER_ROLES = ("recruiter", "employer", "admin")
SPOTLIGHT_UPLOADER_ROLES = ("student", "graduate", "admin")


@app.post("/api/spotlights")
@limiter.limit("10 per hour")
def create_spotlight():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        uploader = cur.fetchone()
        if not uploader or uploader['role'] not in SPOTLIGHT_UPLOADER_ROLES:
            return jsonify({"error": "Only student and graduate accounts can upload a Spotlight."}), 403

        data = request.json or {}
        video_url = data.get("videoUrl")
        if not video_url:
            return jsonify({"error": "videoUrl is required"}), 400

        duration = data.get("durationSeconds")
        if duration is not None and duration > 60:
            return jsonify({"error": "Spotlights must be 60 seconds or shorter."}), 400

        # Only one active spotlight per user - a new upload replaces whatever was there.
        cur.execute("DELETE FROM spotlights WHERE user_id = %s", (user_id,))
        cur.execute("""
            INSERT INTO spotlights (user_id, title, video_url, thumbnail_url, duration_seconds)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (user_id, data.get("title"), video_url, data.get("thumbnailUrl"), duration))

        result = cur.fetchone()
        conn.commit()
        return jsonify({"status": "spotlight created", "spotlight_id": str(result['id'])})

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.get("/api/spotlights")
def list_spotlights():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        viewer = cur.fetchone()
        if not viewer or viewer['role'] not in SPOTLIGHT_VIEWER_ROLES:
            return jsonify({"error": "Only recruiter and employer accounts can view Spotlights."}), 403

        cur.execute("""
            SELECT s.id, s.title, s.video_url, s.thumbnail_url, s.duration_seconds, s.views, s.created_at,
                   u.id AS user_id, u.name, u.role, u.school, u.major, u.skills, u.avatar_url, u.headline
            FROM spotlights s
            JOIN users u ON u.id = s.user_id
            WHERE s.is_active = TRUE
            ORDER BY s.created_at DESC
        """)
        rows = cur.fetchall()

        spotlights = [{
            "id": str(r['id']),
            "title": r['title'],
            "videoUrl": r['video_url'],
            "thumbnailUrl": r['thumbnail_url'],
            "durationSeconds": r['duration_seconds'],
            "views": r['views'],
            "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
            "user": {
                "id": str(r['user_id']),
                "name": r['name'],
                "role": r['role'],
                "school": r['school'],
                "major": r['major'],
                "skills": r['skills'] or [],
                "avatarUrl": r['avatar_url'],
                "headline": r['headline'],
            },
        } for r in rows]

        return jsonify(spotlights)

    finally:
        cur.close()
        conn.close()


@app.get("/api/spotlights/mine")
def get_my_spotlight():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT id, title, video_url, thumbnail_url, duration_seconds, views, created_at
            FROM spotlights
            WHERE user_id = %s AND is_active = TRUE
            ORDER BY created_at DESC
            LIMIT 1
        """, (user_id,))
        r = cur.fetchone()

        if not r:
            return jsonify(None)

        return jsonify({
            "id": str(r['id']),
            "title": r['title'],
            "videoUrl": r['video_url'],
            "thumbnailUrl": r['thumbnail_url'],
            "durationSeconds": r['duration_seconds'],
            "views": r['views'],
            "createdAt": r['created_at'].isoformat() if r['created_at'] else None,
        })

    finally:
        cur.close()
        conn.close()


@app.delete("/api/spotlights/<spotlight_id>")
def delete_spotlight(spotlight_id):
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT user_id FROM spotlights WHERE id = %s", (spotlight_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "spotlight not found"}), 404
        if str(row['user_id']) != user_id and not is_admin(user_id):
            return jsonify({"error": "unauthorized"}), 403

        cur.execute("DELETE FROM spotlights WHERE id = %s", (spotlight_id,))
        conn.commit()
        return jsonify({"status": "deleted"})

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


@app.post("/api/spotlights/<spotlight_id>/view")
def view_spotlight(spotlight_id):
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        viewer = cur.fetchone()
        if not viewer or viewer['role'] not in SPOTLIGHT_VIEWER_ROLES:
            return jsonify({"error": "Only recruiter and employer accounts can view Spotlights."}), 403

        cur.execute("UPDATE spotlights SET views = views + 1 WHERE id = %s AND is_active = TRUE RETURNING views", (spotlight_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "spotlight not found"}), 404
        conn.commit()
        return jsonify({"views": row['views']})

    except Exception as e:
        conn.rollback()
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