# Backend test suite

Runs the real Flask app against a throwaway local Postgres instance (spun up and torn down
automatically per test session) - not against the production database.

## Prerequisites

- PostgreSQL client + server binaries on `PATH` (`initdb`, `pg_ctl`, `createdb`) - already
  required for local dev; on macOS via Homebrew: `brew install postgresql@18`.
- `pip install -r requirements-dev.txt`

## Running

```bash
cd graduate-backend
pytest
```

No environment variables need to be set manually - `tests/conftest.py` creates the test
database, points `DATABASE_URL`/`JWT_SECRET` at it, and tears it down when the run finishes.
Every table is truncated between tests so they don't leak state into each other.

## What's covered

- `test_auth.py` - signup validation (missing fields, short password, duplicate email, .edu
  requirement), login, protected-endpoint auth, change-password, forgot/reset-password,
  full 2FA setup + login flow.
- `test_idor.py` - cross-user access control: every endpoint that takes another user's ID or
  another user's resource ID (profile, export, delete, connections, endorsements, feed posts,
  job listings) refuses to act on it as anyone but the owner or an admin.
- `test_jobs.py` - role-gated job posting, applying, duplicate-application rejection,
  per-user application scoping.
- `test_feed.py` - post create/edit/delete, likes, comments.
- `test_scholarships.py` - admin-only create/delete.

Not covered yet: Google Sign-In (requires mocking Google's token verification), spotlights,
messaging, connections beyond the accept/decline path, email content itself (only that sends
don't block the request - Resend calls fail closed since `RESEND_API_KEY` isn't set in tests).
