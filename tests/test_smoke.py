def test_status_ok(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_signup_and_login(client):
    resp = client.post("/api/signup", json={
        "name": "Ada Lovelace", "email": "ada@example.com", "password": "password123",
    })
    assert resp.status_code == 200

    resp = client.post("/api/login", json={"email": "ada@example.com", "password": "password123"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["token"]
    assert body["name"] == "Ada Lovelace"
