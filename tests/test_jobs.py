from conftest import auth_headers, signup_and_login


def test_list_jobs_is_public(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_only_employer_recruiter_or_admin_can_post_a_job(client):
    _, student_token = signup_and_login(client, "jobposter-student@school.edu", role="student")
    resp = client.post("/api/jobs", headers=auth_headers(student_token), json={
        "title": "Should Not Work", "company": "Acme", "location": "Remote",
    })
    assert resp.status_code == 403

    _, employer_token = signup_and_login(client, "jobposter-employer@example.com", role="employer")
    resp = client.post("/api/jobs", headers=auth_headers(employer_token), json={
        "title": "Backend Intern", "company": "Acme", "location": "Remote",
    })
    assert resp.status_code == 200

    resp = client.get("/api/jobs")
    titles = [j["title"] for j in resp.get_json()]
    assert "Backend Intern" in titles


def test_apply_to_job_requires_auth(client):
    _, employer_token = signup_and_login(client, "employer3@example.com", role="employer")
    resp = client.post("/api/jobs", headers=auth_headers(employer_token), json={
        "title": "Data Intern", "company": "Acme", "location": "Remote",
    })
    job_id = resp.get_json()["job_id"]

    resp = client.post("/api/apply", json={"job_id": job_id})
    assert resp.status_code == 401


def test_apply_to_job_and_duplicate_application_rejected(client):
    _, employer_token = signup_and_login(client, "employer4@example.com", role="employer")
    resp = client.post("/api/jobs", headers=auth_headers(employer_token), json={
        "title": "Design Intern", "company": "Acme", "location": "Remote",
    })
    job_id = resp.get_json()["job_id"]

    _, applicant_token = signup_and_login(client, "applicant@example.com")
    resp = client.post("/api/apply", headers=auth_headers(applicant_token), json={"job_id": job_id})
    assert resp.status_code == 200

    resp = client.post("/api/apply", headers=auth_headers(applicant_token), json={"job_id": job_id})
    assert resp.status_code == 400  # already applied

    resp = client.get("/api/my-applications", headers=auth_headers(applicant_token))
    apps = resp.get_json()
    assert len(apps) == 1
    assert apps[0]["job"]["title"] == "Design Intern"


def test_my_applications_only_shows_own_applications(client):
    _, employer_token = signup_and_login(client, "employer5@example.com", role="employer")
    resp = client.post("/api/jobs", headers=auth_headers(employer_token), json={
        "title": "Ops Intern", "company": "Acme", "location": "Remote",
    })
    job_id = resp.get_json()["job_id"]

    _, applicant_a_token = signup_and_login(client, "applicant-a@example.com")
    _, applicant_b_token = signup_and_login(client, "applicant-b@example.com")
    client.post("/api/apply", headers=auth_headers(applicant_a_token), json={"job_id": job_id})

    resp = client.get("/api/my-applications", headers=auth_headers(applicant_b_token))
    assert resp.get_json() == []
