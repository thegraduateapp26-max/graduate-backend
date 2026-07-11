import os
import datetime
import bcrypt
import jwt

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import google.auth
from google.cloud import secretmanager
from google.cloud.sql.connector import Connector
from flask_swagger_ui import get_swaggerui_blueprint


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

connector = Connector()

JWT_SECRET = os.environ.get("JWT_SECRET", "super-secret-key")


# -----------------------------
# Helpers
# -----------------------------
def get_project_id():
    pid = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if pid:
        return pid
    _, pid = google.auth.default()
    return pid


def get_db_password():
    project_id = get_project_id()
    secret_name = os.environ.get("DB_PASSWORD_SECRET_NAME", "web-app-db-secret")
    client = secretmanager.SecretManagerServiceClient()
    secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": secret_path})
    return response.payload.data.decode("utf-8")


def get_conn():
    return connector.connect(
        os.environ["CLOUD_SQL_DATABASE_CONNECTION_NAME"],
        "pg8000",
        user=os.environ.get("CLOUD_SQL_DATABASE_USER", "postgres"),
        password=get_db_password(),
        db=os.environ.get("CLOUD_SQL_DATABASE_NAME", "postgres"),
    )


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
# Admin Dashboard
# -----------------------------
@app.get("/admin")
def admin():
    return render_template("admin.html")


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
            VALUES (%s,%s,%s,%s,'pending')
            RETURNING id;
        """, (name, email, password_hash, role))

        user_id = cur.fetchone()[0]
        conn.commit()
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

    user_id = user[0]
    password_hash = user[1]
    name = user[2]
    role = user[3]

    if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
        return jsonify({"error": "invalid credentials"}), 401

    token = jwt.encode({
        "user_id": str(user_id),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=1)
    }, JWT_SECRET, algorithm="HS256")

    return jsonify({
        "token": token,
        "user_id": str(user_id),
        "name": name,
        "role": role,
    })


# -----------------------------
# USERS
# -----------------------------
@app.get("/api/users")
def list_users():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, role, verification_status, created_at
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
            "id": str(r[0]),
            "name": r[1],
            "role": r[2],
            "verificationStatus": r[3],
            "createdAt": r[4].isoformat() if r[4] else None,
        })

    return jsonify(users)


# -----------------------------
# JOBS
# -----------------------------
@app.get("/api/jobs")
def list_jobs():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id,title,company,location,salary_range,
        job_type,description,url,tags,created_at,is_active
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
            "id": str(r[0]),
            "title": r[1],
            "company": r[2],
            "location": r[3],
            "salaryRange": r[4],
            "jobType": r[5],
            "description": r[6],
            "url": r[7],
            "tags": r[8] or [],
            "createdAt": r[9].isoformat() if r[9] else None,
            "isActive": r[10]
        })

    return jsonify(jobs)


# -----------------------------
# CREATE JOB (SECURED)
# -----------------------------
@app.post("/api/jobs")
def create_job():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO jobs (title, company, location, salary_range, job_type, description, url, tags, posted_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (
        data.get("title"), data.get("company"), data.get("location"),
        data.get("salaryRange"), data.get("jobType"), data.get("description"),
        data.get("url"), data.get("tags"), user_id
    ))

    job_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "job created", "job_id": str(job_id)})


# -----------------------------
# SCHOLARSHIPS
# -----------------------------
@app.get("/api/scholarships")
def list_scholarships():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id,title,provider,amount,deadline,
        description,url,tags,created_at,is_active
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
            "id": str(r[0]),
            "title": r[1],
            "provider": r[2],
            "amount": r[3],
            "deadline": r[4].isoformat() if r[4] else None,
            "description": r[5],
            "url": r[6],
            "tags": r[7] or [],
            "createdAt": r[8].isoformat() if r[8] else None,
            "isActive": r[9]
        })

    return jsonify(scholarships)


# -----------------------------
# CREATE SCHOLARSHIP (SECURED)
# -----------------------------
@app.post("/api/scholarships")
def create_scholarship():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    data = request.json

    title = data.get("title")
    provider = data.get("provider")

    if not title or not provider:
        return jsonify({"error": "title and provider are required"}), 400

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO scholarships (title, provider, amount, deadline, description, url, tags, is_active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE)
            RETURNING id
        """, (
            title,
            provider,
            data.get("amount"),
            data.get("deadline"),
            data.get("description"),
            data.get("url"),
            data.get("tags", []),
        ))

        scholarship_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({"status": "scholarship created", "scholarship_id": str(scholarship_id)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()


# -----------------------------
# DELETE SCHOLARSHIP (SECURED)
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
        return jsonify({"status": "scholarship deleted"})
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