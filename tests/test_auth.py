def test_redirects_to_login_when_not_authenticated(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_wrong_password_shows_error(client):
    resp = client.post("/login", data={"username": "testuser", "password": "wrong"})
    assert resp.status_code == 401
    assert "Неверный логин" in resp.text


def test_login_success_grants_access(logged_in_client):
    resp = logged_in_client.get("/")
    assert resp.status_code == 200
    assert "Чеки" in resp.text


def test_logout_revokes_access(logged_in_client):
    resp = logged_in_client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303

    resp = logged_in_client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
