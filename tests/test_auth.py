from conftest import auth_headers, signup_and_login


def test_signup_requires_all_fields(client):
    resp = client.post("/api/signup", json={"name": "No Email"})
    assert resp.status_code == 400


def test_signup_rejects_short_password(client):
    resp = client.post("/api/signup", json={
        "name": "Short Pw", "email": "short@example.com", "password": "abc",
    })
    assert resp.status_code == 400


def test_signup_rejects_duplicate_email(client):
    signup_and_login(client, "dupe@example.com")
    resp = client.post("/api/signup", json={
        "name": "Second", "email": "dupe@example.com", "password": "password123",
    })
    assert resp.status_code == 500  # unique constraint violation, not a friendly 409, but must not silently succeed
    resp = client.post("/api/login", json={"email": "dupe@example.com", "password": "password123"})
    assert resp.status_code == 401  # the second (never-created) account can't log in


def test_student_signup_requires_edu_email(client):
    resp = client.post("/api/signup", json={
        "name": "Student", "email": "student@gmail.com", "password": "password123", "role": "student",
    })
    assert resp.status_code == 400

    resp = client.post("/api/signup", json={
        "name": "Student", "email": "student@school.edu", "password": "password123", "role": "student",
    })
    assert resp.status_code == 200


def test_login_wrong_password(client):
    signup_and_login(client, "wrongpw@example.com")
    resp = client.post("/api/login", json={"email": "wrongpw@example.com", "password": "not-the-password"})
    assert resp.status_code == 401


def test_login_nonexistent_user(client):
    resp = client.post("/api/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert resp.status_code == 401


def test_protected_endpoint_requires_token(client):
    resp = client.get("/api/2fa/status")
    assert resp.status_code == 401


def test_protected_endpoint_rejects_garbage_token(client):
    resp = client.get("/api/2fa/status", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_change_password_requires_current_password(client):
    _, token = signup_and_login(client, "changepw@example.com", password="original123")

    resp = client.post("/api/change-password", headers=auth_headers(token), json={
        "currentPassword": "wrong-password", "newPassword": "newpassword123",
    })
    assert resp.status_code == 401

    resp = client.post("/api/change-password", headers=auth_headers(token), json={
        "currentPassword": "original123", "newPassword": "newpassword123",
    })
    assert resp.status_code == 200

    # old password no longer works, new one does
    resp = client.post("/api/login", json={"email": "changepw@example.com", "password": "original123"})
    assert resp.status_code == 401
    resp = client.post("/api/login", json={"email": "changepw@example.com", "password": "newpassword123"})
    assert resp.status_code == 200


def test_forgot_password_does_not_reveal_account_existence(client):
    signup_and_login(client, "knownuser@example.com")

    resp_known = client.post("/api/forgot-password", json={"email": "knownuser@example.com"})
    resp_unknown = client.post("/api/forgot-password", json={"email": "nobody-here@example.com"})

    assert resp_known.status_code == 200
    assert resp_unknown.status_code == 200
    assert resp_known.get_json() == resp_unknown.get_json()


def test_reset_password_rejects_invalid_token(client):
    resp = client.post("/api/reset-password", json={"token": "not-a-real-token", "password": "newpassword123"})
    assert resp.status_code == 400


def test_2fa_setup_and_verify_flow(client):
    import pyotp

    _, token = signup_and_login(client, "twofactor@example.com")

    resp = client.post("/api/2fa/setup", headers=auth_headers(token))
    assert resp.status_code == 200
    secret = resp.get_json()["secret"]

    # wrong code is rejected
    resp = client.post("/api/2fa/verify-setup", headers=auth_headers(token), json={"code": "000000"})
    assert resp.status_code == 401

    # correct TOTP code enables 2FA
    code = pyotp.TOTP(secret).now()
    resp = client.post("/api/2fa/verify-setup", headers=auth_headers(token), json={"code": code})
    assert resp.status_code == 200

    resp = client.get("/api/2fa/status", headers=auth_headers(token))
    assert resp.get_json()["enabled"] is True

    # logging in now requires the second factor instead of returning a session token directly
    resp = client.post("/api/login", json={"email": "twofactor@example.com", "password": "testpass123"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["requires2FA"] is True
    pending_token = body["pendingToken"]

    code = pyotp.TOTP(secret).now()
    resp = client.post("/api/login/2fa", json={"pendingToken": pending_token, "code": code})
    assert resp.status_code == 200
    assert resp.get_json()["token"]
