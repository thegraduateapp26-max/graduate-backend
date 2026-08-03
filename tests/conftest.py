"""
Spins up a throwaway local Postgres instance (via Homebrew's `postgres`/`pg_ctl`/`initdb`,
already required for local dev on this machine) for the whole test session, so tests run
against a real Postgres - matching production behavior (UUID columns, arrays, etc that don't
exist in SQLite) - without ever touching the real Railway database.
"""
import os
import shutil
import subprocess
import tempfile
import time

import psycopg2
import pytest

PG_PORT = "55433"


def _run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


@pytest.fixture(scope="session")
def postgres_dsn():
    tmp_dir = tempfile.mkdtemp(prefix="graduate_test_pg_")
    data_dir = os.path.join(tmp_dir, "data")
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}

    _run(["initdb", "-D", data_dir, "-U", "postgres", "--auth=trust", "--no-locale", "-E", "UTF8"], env=env)
    _run([
        "pg_ctl", "-D", data_dir,
        "-o", f"-p {PG_PORT} -k {tmp_dir} -c listen_addresses=''",
        "-l", os.path.join(tmp_dir, "log.txt"), "start",
    ], env=env)

    try:
        _run(["createdb", "-h", tmp_dir, "-p", PG_PORT, "-U", "postgres", "graduate_test"], env=env)
        yield f"postgresql://postgres@/graduate_test?host={tmp_dir}&port={PG_PORT}"
    finally:
        subprocess.run(["pg_ctl", "-D", data_dir, "stop", "-m", "immediate"], env=env, capture_output=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def flask_app(postgres_dsn):
    # app.py reads these at import time (JWT_SECRET has no insecure fallback, by design -
    # see app.py's comment - so it must be set before the module is imported at all).
    os.environ["DATABASE_URL"] = postgres_dsn
    os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production"
    os.environ.pop("GOOGLE_CLIENT_ID", None)
    os.environ.pop("RESEND_API_KEY", None)  # email sends fail closed (caught + logged) without a key - fine for tests

    import app as app_module  # import AFTER env is set - this is what triggers init_db()

    app_module.app.config["TESTING"] = True
    # Limiter.enabled is a plain instance attribute fixed at construction time, not something
    # re-read from app.config per request - so the config flag alone does nothing here.
    app_module.limiter.enabled = False  # tests create far more users/posts/etc per minute than the real limits allow
    return app_module


@pytest.fixture()
def client(flask_app):
    return flask_app.app.test_client()


@pytest.fixture(autouse=True)
def _clean_db(flask_app):
    """Truncates every app table before each test so tests don't leak state into each other,
    without paying the cost of tearing down/recreating the whole database per test."""
    yield
    conn = psycopg2.connect(flask_app.DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    """)
    tables = [r[0] for r in cur.fetchall()]
    if tables:
        cur.execute("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE")
    conn.commit()
    cur.close()
    conn.close()


def signup_and_login(client, email, password="testpass123", role="graduate", name="Test User", **extra):
    payload = {"name": name, "email": email, "password": password, "role": role, **extra}
    resp = client.post("/api/signup", json=payload)
    assert resp.status_code == 200, resp.get_json()
    user_id = resp.get_json()["user_id"]

    resp = client.post("/api/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.get_json()
    token = resp.get_json()["token"]
    return user_id, token


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def make_admin(flask_app, user_id):
    # "admin" is deliberately not a self-signup role (app.py's signup() silently downgrades
    # any unrecognized role to "graduate") - promoting a user is only ever a direct DB
    # operation, in tests as in production, so tests do the same thing here.
    conn = psycopg2.connect(flask_app.DATABASE_URL)
    cur = conn.cursor()
    cur.execute("UPDATE users SET role = 'admin' WHERE id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()
