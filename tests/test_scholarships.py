from conftest import auth_headers, make_admin, signup_and_login


def test_list_scholarships_is_public(client):
    resp = client.get("/api/scholarships")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_only_admin_can_create_or_delete_scholarships(client, flask_app):
    _, regular_token = signup_and_login(client, "regular@example.com")
    resp = client.post("/api/scholarships", headers=auth_headers(regular_token), json={
        "title": "Should Not Work", "provider": "Nobody",
    })
    assert resp.status_code == 401

    admin_id, admin_token = signup_and_login(client, "scholarship-admin@example.com")
    make_admin(flask_app, admin_id)

    resp = client.post("/api/scholarships", headers=auth_headers(admin_token), json={
        "title": "Real Scholarship", "provider": "Real Org", "amount": "$1,000",
    })
    assert resp.status_code == 200
    scholarship_id = resp.get_json()["scholarship_id"]

    resp = client.get("/api/scholarships")
    assert any(s["title"] == "Real Scholarship" for s in resp.get_json())

    resp = client.delete(f"/api/scholarships/{scholarship_id}", headers=auth_headers(regular_token))
    assert resp.status_code == 401

    resp = client.delete(f"/api/scholarships/{scholarship_id}", headers=auth_headers(admin_token))
    assert resp.status_code == 200

    resp = client.get("/api/scholarships")
    assert not any(s["title"] == "Real Scholarship" for s in resp.get_json())
