def test_register_login_me(client, auth):
    headers, tokens = auth()
    assert tokens["token_type"] == "bearer"
    r = client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "user@example.com"
    assert "password_hash" not in r.json()

    r = client.post("/api/v1/auth/login", json={"email": "USER@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 200


def test_duplicate_email_rejected(client, auth):
    auth()
    r = client.post("/api/v1/auth/register", json={"email": "user@example.com", "password": "another-password-1"})
    assert r.status_code == 409


def test_wrong_password(client, auth):
    auth()
    r = client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "wrong-password"})
    assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert r.status_code == 401


def test_weak_password_rejected(client):
    r = client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "short"})
    assert r.status_code == 422


def test_requires_auth(client):
    assert client.get("/api/v1/cycles").status_code == 401
    assert client.get("/api/v1/cycles", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_password_stored_as_argon2id(client, auth, db_session):
    from sqlalchemy import select

    from app.models import User

    auth()
    db = db_session
    h = db.scalar(select(User.password_hash))
    assert h.startswith("$argon2id$")


def test_refresh_rotation_and_reuse_detection(client, auth):
    _, tokens = auth()
    old = tokens["refresh_token"]
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": old})
    assert r.status_code == 200
    new = r.json()["refresh_token"]
    assert new != old

    # Reusing the rotated token is treated as theft: rejected AND the family is revoked.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": new}).status_code == 401


def test_logout_revokes_refresh(client, auth):
    _, tokens = auth()
    assert client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_login_rate_limited(client, auth):
    auth()
    codes = [client.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "nope-nope"}).status_code for _ in range(12)]
    assert 429 in codes


def test_users_cannot_see_each_others_data(client, auth):
    h1, _ = auth("a@example.com")
    h2, _ = auth("b@example.com")
    r = client.post("/api/v1/cycles", json={"start_date": "2026-08-01"}, headers=h1)
    cid = r.json()["data"]["id"]
    assert client.get(f"/api/v1/cycles/{cid}", headers=h2).status_code == 404
    assert client.delete(f"/api/v1/cycles/{cid}", headers=h2).status_code == 404
    assert client.get("/api/v1/cycles", headers=h2).json() == []


def test_x_access_token_header_accepted_and_preferred(client, auth):
    h, _ = auth()
    token = h["Authorization"].removeprefix("Bearer ")
    assert client.get("/api/v1/auth/me", headers={"X-Access-Token": token}).status_code == 200
    # a proxy-rewritten Authorization header must not break the custom header
    assert client.get("/api/v1/auth/me", headers={"X-Access-Token": token, "Authorization": "Basic Zm9vOmJhcg=="}).status_code == 200
    assert client.get("/api/v1/auth/me", headers={"X-Access-Token": "garbage"}).status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
