"""Cross-user access control (IDOR) checks - every endpoint here takes another user's ID or
another user's resource ID in the URL, and must refuse to act on it as anyone but the owner
(or an admin, where that's the documented exception)."""
from conftest import auth_headers, make_admin, signup_and_login


def test_cannot_update_another_users_profile(client):
    user_a, token_a = signup_and_login(client, "idor-a@example.com")
    user_b, token_b = signup_and_login(client, "idor-b@example.com")

    resp = client.patch(f"/api/users/{user_b}", headers=auth_headers(token_a), json={"headline": "hijacked"})
    assert resp.status_code == 401

    # sanity: B can still update their own profile
    resp = client.patch(f"/api/users/{user_b}", headers=auth_headers(token_b), json={"headline": "my own headline"})
    assert resp.status_code == 200


def test_cannot_export_another_users_data(client):
    user_a, token_a = signup_and_login(client, "export-a@example.com")
    user_b, token_b = signup_and_login(client, "export-b@example.com")

    resp = client.get(f"/api/users/{user_b}/export", headers=auth_headers(token_a))
    assert resp.status_code == 401

    resp = client.get(f"/api/users/{user_b}/export", headers=auth_headers(token_b))
    assert resp.status_code == 200


def test_cannot_delete_another_users_account(client):
    user_a, token_a = signup_and_login(client, "del-a@example.com")
    user_b, token_b = signup_and_login(client, "del-b@example.com", password="bpassword123")

    resp = client.delete(f"/api/users/{user_b}", headers=auth_headers(token_a), json={"password": "bpassword123"})
    assert resp.status_code == 401

    # B is untouched and can still log in
    resp = client.post("/api/login", json={"email": "del-b@example.com", "password": "bpassword123"})
    assert resp.status_code == 200


def test_cannot_view_another_users_connections(client):
    user_a, token_a = signup_and_login(client, "conn-a@example.com")
    user_b, token_b = signup_and_login(client, "conn-b@example.com")

    resp = client.get(f"/api/users/{user_b}/connections", headers=auth_headers(token_a))
    assert resp.status_code == 401


def test_cannot_respond_to_someone_elses_connection_request(client):
    user_a, token_a = signup_and_login(client, "reqA@example.com")
    user_b, token_b = signup_and_login(client, "reqB@example.com")
    user_c, token_c = signup_and_login(client, "reqC@example.com")

    # A sends a request to B
    resp = client.post(f"/api/users/{user_b}/connect", headers=auth_headers(token_a))
    assert resp.status_code == 200
    connection_id = resp.get_json()["connectionId"]

    # C (an unrelated third party) tries to accept it on B's behalf
    resp = client.patch(f"/api/connections/{connection_id}", headers=auth_headers(token_c), json={"status": "accepted"})
    assert resp.status_code == 404  # scoped query finds nothing for a non-recipient, not "unauthorized"

    # B (the real recipient) can
    resp = client.patch(f"/api/connections/{connection_id}", headers=auth_headers(token_b), json={"status": "accepted"})
    assert resp.status_code == 200


def test_cannot_toggle_visibility_of_someone_elses_endorsement(client):
    student_id, student_token = signup_and_login(client, "endorsee@school.edu", role="student")
    _, prof_token = signup_and_login(client, "prof@school.edu", role="professor")
    _, other_token = signup_and_login(client, "endorse-other@example.com")

    resp = client.post(f"/api/users/{student_id}/endorsements", headers=auth_headers(prof_token), json={
        "text": "Great student.", "relationship": "Professor",
    })
    assert resp.status_code == 200
    endorsement_id = resp.get_json()["endorsement"]["id"]

    resp = client.patch(f"/api/endorsements/{endorsement_id}", headers=auth_headers(other_token), json={"visible": False})
    assert resp.status_code == 404

    resp = client.patch(f"/api/endorsements/{endorsement_id}", headers=auth_headers(student_token), json={"visible": False})
    assert resp.status_code == 200


def test_cannot_edit_or_delete_another_users_post(client):
    _, token_a = signup_and_login(client, "post-a@example.com")
    _, token_b = signup_and_login(client, "post-b@example.com")

    resp = client.post("/api/feed", headers=auth_headers(token_a), json={"content": "A's original post"})
    assert resp.status_code == 200
    post_id = resp.get_json()["post"]["id"]

    resp = client.patch(f"/api/feed/{post_id}", headers=auth_headers(token_b), json={"content": "hijacked"})
    assert resp.status_code == 401

    resp = client.delete(f"/api/feed/{post_id}", headers=auth_headers(token_b))
    assert resp.status_code == 401

    # post is untouched
    resp = client.get("/api/feed")
    posts = resp.get_json()
    assert any(p["id"] == post_id and p["content"] == "A's original post" for p in posts)


def test_only_job_owner_or_admin_can_deactivate_job(client):
    _, employer_token = signup_and_login(client, "employer@example.com", role="employer")
    _, other_employer_token = signup_and_login(client, "employer2@example.com", role="employer")

    resp = client.post("/api/jobs", headers=auth_headers(employer_token), json={
        "title": "Backend Intern", "company": "Acme", "location": "Remote",
    })
    assert resp.status_code == 200
    job_id = resp.get_json()["job_id"]

    resp = client.patch(f"/api/jobs/{job_id}", headers=auth_headers(other_employer_token), json={"isActive": False})
    assert resp.status_code == 401

    resp = client.patch(f"/api/jobs/{job_id}", headers=auth_headers(employer_token), json={"isActive": False})
    assert resp.status_code == 200


def test_admin_can_moderate_any_users_verification(client, flask_app):
    user_id, _ = signup_and_login(client, "toverify@example.com")
    admin_id, admin_token = signup_and_login(client, "admin@example.com")
    make_admin(flask_app, admin_id)  # is_admin() re-checks role from the DB on every call, not the JWT, so no re-login needed

    resp = client.patch(f"/api/users/{user_id}/verification", headers=auth_headers(admin_token), json={"status": "approved"})
    assert resp.status_code == 200

    _, non_admin_token = signup_and_login(client, "notadmin@example.com")
    resp = client.patch(f"/api/users/{user_id}/verification", headers=auth_headers(non_admin_token), json={"status": "rejected"})
    assert resp.status_code == 401
