from conftest import auth_headers, signup_and_login


def test_create_feed_post_requires_auth(client):
    resp = client.post("/api/feed", json={"content": "hello"})
    assert resp.status_code == 401


def test_create_feed_post_requires_content(client):
    _, token = signup_and_login(client, "poster@example.com")
    resp = client.post("/api/feed", headers=auth_headers(token), json={"content": "   "})
    assert resp.status_code == 400


def test_create_edit_delete_feed_post(client):
    _, token = signup_and_login(client, "author@example.com", name="Feed Author")
    resp = client.post("/api/feed", headers=auth_headers(token), json={"content": "First post"})
    assert resp.status_code == 200
    post = resp.get_json()["post"]
    assert post["authorName"] == "Feed Author"
    post_id = post["id"]

    resp = client.get("/api/feed")
    assert any(p["id"] == post_id and p["content"] == "First post" for p in resp.get_json())

    resp = client.patch(f"/api/feed/{post_id}", headers=auth_headers(token), json={"content": "Edited post"})
    assert resp.status_code == 200

    resp = client.delete(f"/api/feed/{post_id}", headers=auth_headers(token))
    assert resp.status_code == 200

    resp = client.get("/api/feed")
    assert not any(p["id"] == post_id for p in resp.get_json())


def test_like_toggle(client):
    _, author_token = signup_and_login(client, "liked-author@example.com")
    resp = client.post("/api/feed", headers=auth_headers(author_token), json={"content": "Like me"})
    post_id = resp.get_json()["post"]["id"]

    _, liker_token = signup_and_login(client, "liker@example.com")

    resp = client.post(f"/api/feed/{post_id}/like", headers=auth_headers(liker_token))
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "updated", "likedByMe": True, "likesCount": 1}

    resp = client.post(f"/api/feed/{post_id}/like", headers=auth_headers(liker_token))
    assert resp.get_json() == {"status": "updated", "likedByMe": False, "likesCount": 0}


def test_comments(client):
    _, author_token = signup_and_login(client, "commented-author@example.com")
    resp = client.post("/api/feed", headers=auth_headers(author_token), json={"content": "Comment on me"})
    post_id = resp.get_json()["post"]["id"]

    _, commenter_token = signup_and_login(client, "commenter@example.com")
    resp = client.post(f"/api/feed/{post_id}/comments", headers=auth_headers(commenter_token), json={"text": "Nice post!"})
    assert resp.status_code == 200

    resp = client.get(f"/api/feed/{post_id}/comments")
    comments = resp.get_json()
    assert len(comments) == 1
    assert comments[0]["text"] == "Nice post!"
