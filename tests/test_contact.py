from conftest import auth_headers, signup_and_login


def test_contact_requires_all_fields(client):
    resp = client.post("/api/contact", json={"name": "Ada"})
    assert resp.status_code == 400


def test_contact_rejects_overlong_message(client):
    resp = client.post("/api/contact", json={
        "name": "Ada", "email": "ada@example.com", "message": "x" * 5001,
    })
    assert resp.status_code == 400


def test_contact_works_when_logged_out(client):
    resp = client.post("/api/contact", json={
        "name": "Ada Lovelace", "email": "ada@example.com", "message": "How do I reset my password?",
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "sent"


def test_contact_works_when_logged_in(client):
    _, token = signup_and_login(client, "loggedin-contact@example.com")
    resp = client.post("/api/contact", headers=auth_headers(token), json={
        "name": "Test User", "email": "loggedin-contact@example.com", "message": "Found a bug on the jobs page.",
    })
    assert resp.status_code == 200
