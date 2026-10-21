"""Real cookie/JWT/CSRF and ORM authorization against isolated PostgreSQL 16."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select, update

from app.database import create_engine, create_session_factory
from app.main import create_app
from app.models import Application, AuthSession, CSRFContext, Document, User
from app.security import context_digest, decode_session, verify_password
from app.seed import ImportError, import_accounts

ACCOUNTS = Path(__file__).resolve().parents[2] / "seed/accounts.json"
ORIGIN = "https://testserver"
SECRET = "isolated-auth-tests-secret-with-at-least-32-characters"
SOURCE = json.loads(ACCOUNTS.read_text())
CLIENT = SOURCE["clients"][0]
SECOND = SOURCE["clients"][1]
BANK = SOURCE["underwriters"][0]


def in_database(settings, callback):
    async def run():
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                result = await callback(db)
                await db.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture(scope="session")
def auth_settings(database):
    settings = database.model_copy(
        update={
            "auth_jwt_secret": SecretStr(SECRET),
            "auth_allowed_origin": ORIGIN,
            "auth_cookie_secure": True,
        }
    )

    async def seed(db):
        return await import_accounts(create_session_factory(db.bind), ACCOUNTS)

    assert in_database(settings, seed) == (10, 0)
    return settings


@pytest.fixture
def client(auth_settings):
    with TestClient(create_app(auth_settings), base_url=ORIGIN) as client:
        yield client


def csrf(client):
    response = client.get("/api/auth/csrf")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"csrf_token"}
    return response.json()["csrf_token"]


def headers(token, origin=ORIGIN):
    return {"Origin": origin, "X-CSRF-Token": token}


def login(client, account=CLIENT):
    token = csrf(client)
    response = client.post(
        "/api/auth/login",
        json={"login": account["login"], "password": account["password"]},
        headers=headers(token),
    )
    assert response.status_code == 200
    assert set(response.json()) == {"id", "login", "display_name", "role"}
    assert response.headers["cache-control"] == "no-store"
    return response


def test_import_is_idempotent_and_hashes_are_argon2id(auth_settings):
    async def check(db):
        before = {
            u.login: (u.id, u.password_hash, u.display_name, u.role)
            for u in (
                await db.scalars(
                    select(User).where(
                        User.login.in_(
                            [a["login"] for a in SOURCE["clients"] + SOURCE["underwriters"]]
                        )
                    )
                )
            ).all()
        }
        assert len(before) == 10
        for row in before.values():
            assert row[1].startswith("$argon2id$")
        assert await import_accounts(create_session_factory(db.bind), ACCOUNTS) == (0, 10)
        after = {
            u.login: (u.id, u.password_hash, u.display_name, u.role)
            for u in (
                await db.scalars(
                    select(User)
                    .where(User.login.in_(before))
                    .execution_options(populate_existing=True)
                )
            ).all()
        }
        assert before == after
        assert verify_password(before[CLIENT["login"]][1], CLIENT["password"])

    in_database(auth_settings, check)


def test_import_conflict_rolls_back_and_does_not_reset_password(auth_settings, tmp_path):
    name = uuid4().hex
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps(
            {
                "clients": [
                    {
                        "login": name,
                        "password": "never-print-this-password",
                        "display_name": "Demo",
                    },
                    {**BANK},
                ],
                "underwriters": [],
            }
        )
    )

    async def check(db):
        with pytest.raises(ImportError, match="role conflicts") as error:
            await import_accounts(create_session_factory(db.bind), path)
        assert "never-print" not in str(error.value)
        assert await db.scalar(select(User).where(User.login == name)) is None
        # Changed source password/display name must leave an existing account intact.
        before = await db.scalar(select(User).where(User.login == CLIENT["login"]))
        saved = before.password_hash
        path.write_text(
            json.dumps(
                {
                    "clients": [{**CLIENT, "password": "replacement", "display_name": "Changed"}],
                    "underwriters": [],
                }
            )
        )
        assert await import_accounts(create_session_factory(db.bind), path) == (0, 1)
        await db.refresh(before)
        assert before.password_hash == saved
        assert before.display_name == CLIENT["display_name"]

    in_database(auth_settings, check)


@pytest.mark.parametrize("account", [CLIENT, BANK], ids=["client", "underwriter"])
def test_complete_flow_rotation_and_logout_revokes_copied_jwt(client, auth_settings, account):
    old_token = csrf(client)
    old_cookie = client.cookies.get("csrf_context")
    assert csrf(client) == old_token
    assert client.cookies.get("csrf_context") == old_cookie
    response = login(client, account)
    user = response.json()
    jwt_cookie = client.cookies.get("access_token")
    assert client.cookies.get("csrf_context") != old_cookie
    for cookie in response.headers.get_list("set-cookie"):
        assert (
            "HttpOnly" in cookie
            and "SameSite=lax" in cookie
            and "Path=/" in cookie
            and "Secure" in cookie
        )
    assert client.post("/api/auth/logout", headers=headers(old_token)).status_code == 403
    new_token = csrf(client)
    assert new_token != old_token and csrf(client) == new_token
    assert client.get("/api/auth/me").json() == user
    # PostgreSQL state and cryptographic configuration survive application recreation.
    with TestClient(create_app(auth_settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get("/api/auth/me").json() == user
        assert csrf(restarted) == new_token
    response = client.post("/api/auth/logout", headers=headers(new_token))
    assert response.status_code == 204 and response.content == b""
    assert response.headers["cache-control"] == "no-store"
    for cookie in response.headers.get_list("set-cookie"):
        assert (
            "Max-Age=0" in cookie
            and "HttpOnly" in cookie
            and "Secure" in cookie
            and "SameSite=lax" in cookie
            and "Path=/" in cookie
        )
    assert not client.cookies
    assert client.get("/api/auth/me").status_code == 401
    with TestClient(create_app(auth_settings), base_url=ORIGIN) as replay:
        replay.cookies.set("access_token", jwt_cookie)
        assert replay.get("/api/auth/me").status_code == 401
    client.cookies.set("csrf_context", old_cookie)
    assert (
        client.post(
            "/api/auth/login",
            json={"login": account["login"], "password": account["password"]},
            headers=headers(old_token),
        ).status_code
        == 403
    )
    assert csrf(client) not in {old_token, new_token}


def test_same_public_error_for_unknown_login_and_wrong_password(client):
    token = csrf(client)
    errors = []
    for name, password in (
        (uuid4().hex, CLIENT["password"]),
        (CLIENT["login"], "wrong-password-never-echo"),
    ):
        response = client.post(
            "/api/auth/login", json={"login": name, "password": password}, headers=headers(token)
        )
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert password not in response.text and name not in response.text
        errors.append({k: v for k, v in response.json().items() if k != "request_id"})
        assert "access_token" not in client.cookies
    assert errors[0] == errors[1]


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "signature",
        "algorithm",
        "none",
        "issuer",
        "audience",
        "role",
        "subject",
        "session",
        "iat",
        "exp-type",
        *["missing-" + k for k in ("sub", "role", "sid", "iat", "exp", "iss", "aud")],
    ],
)
def test_invalid_jwt(client, auth_settings, case):
    login(client)
    claims = decode_session(auth_settings, client.cookies.get("access_token"))
    algorithm, key = "HS256", SECRET
    if case == "expired":
        claims["exp"] = int(datetime.now(UTC).timestamp()) - 1
    elif case == "signature":
        key = "wrong-secret-of-at-least-32-characters"
    elif case == "algorithm":
        algorithm = "HS384"
    elif case == "none":
        algorithm, key = "none", None
    elif case == "issuer":
        claims["iss"] = "other"
    elif case == "audience":
        claims["aud"] = "other"
    elif case == "role":
        claims["role"] = "underwriter"
    elif case == "subject":
        claims["sub"] = str(uuid4())
    elif case == "session":
        claims["sid"] = str(uuid4())
    elif case == "iat":
        claims["iat"] += 3600
    elif case == "exp-type":
        claims["exp"] = str(claims["exp"])
    else:
        claims.pop(case.removeprefix("missing-"))
    token = jwt.encode(claims, key, algorithm=algorithm)
    client.cookies.clear()
    client.cookies.set("access_token", token)
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert token not in response.text and SECRET not in response.text


@pytest.mark.parametrize(
    "case", ["revoked", "missing", "expired", "role", "session-role", "another-user"]
)
def test_server_side_session_checks(client, auth_settings, case):
    login(client)
    claims = decode_session(auth_settings, client.cookies.get("access_token"))

    async def change(db):
        session = await db.get(AuthSession, UUID(claims["sid"]))
        if case == "revoked":
            session.revoked_at = datetime.now(UTC)
        elif case == "missing":
            await db.execute(delete(CSRFContext).where(CSRFContext.session_id == session.id))
            await db.delete(session)
        elif case == "expired":
            session.created_at = datetime.now(UTC) - timedelta(hours=10)
            session.expires_at = datetime.now(UTC) - timedelta(hours=1)
        elif case == "role":
            user = await db.get(User, UUID(claims["sub"]))
            user.role = "underwriter"
        elif case == "session-role":
            session.role = "underwriter"
        else:
            session.user_id = await db.scalar(select(User.id).where(User.login == SECOND["login"]))

    in_database(auth_settings, change)
    try:
        assert client.get("/api/auth/me").status_code == 401
    finally:
        if case == "role":

            async def restore(db):
                await db.execute(
                    update(User).where(User.id == UUID(claims["sub"])).values(role="client")
                )

            in_database(auth_settings, restore)


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "missing-token",
        "wrong-token",
        "missing-cookie",
        "wrong-cookie",
        "foreign-token",
        "foreign-cookie",
        "expired",
    ],
)
def test_csrf_failures(client, auth_settings, authenticated, case):
    if authenticated:
        login(client)
    token = csrf(client)
    request_headers = headers(token)
    if case == "missing-token":
        request_headers.pop("X-CSRF-Token")
    elif case == "wrong-token":
        request_headers["X-CSRF-Token"] = "wrong"
    elif case == "missing-cookie":
        client.cookies.delete("csrf_context")
    elif case == "wrong-cookie":
        client.cookies.delete("csrf_context")
        client.cookies.set("csrf_context", "arbitrary-client-value")
    elif case.startswith("foreign"):
        with TestClient(create_app(auth_settings), base_url=ORIGIN) as foreign:
            if authenticated:
                login(foreign, SECOND)
            foreign_token = csrf(foreign)
            if case == "foreign-token":
                request_headers["X-CSRF-Token"] = foreign_token
            else:
                client.cookies.delete("csrf_context")
                client.cookies.set("csrf_context", foreign.cookies.get("csrf_context"))
                request_headers["X-CSRF-Token"] = foreign_token
                # For anonymous requests, use a mismatched cookie/token pair.
                if not authenticated:
                    request_headers["X-CSRF-Token"] = token
    else:
        digest = context_digest(client.cookies.get("csrf_context"))

        async def expire(db):
            await db.execute(
                update(CSRFContext)
                .where(CSRFContext.cookie_digest == digest)
                .values(
                    created_at=datetime.now(UTC) - timedelta(hours=2),
                    expires_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )

        in_database(auth_settings, expire)
    path = "/api/auth/logout" if authenticated else "/api/auth/login"
    body = {} if authenticated else {"login": CLIENT["login"], "password": CLIENT["password"]}
    response = client.post(path, json=body, headers=request_headers)
    assert response.status_code == 403
    assert token not in response.text


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "http://testserver",
        "https://testserver:443",
        "https://testserver.evil.test",
        "https://evil.test/testserver",
        "https://testserver/",
    ],
)
@pytest.mark.parametrize("authenticated", [False, True])
def test_exact_origin_is_required(client, origin, authenticated):
    if authenticated:
        login(client)
    token = csrf(client)
    h = {"X-CSRF-Token": token, "X-Forwarded-Host": "testserver", "X-Forwarded-Proto": "https"}
    if origin is not None:
        h["Origin"] = origin
    path = "/api/auth/logout" if authenticated else "/api/auth/login"
    assert (
        client.post(
            path, json={"login": CLIENT["login"], "password": CLIENT["password"]}, headers=h
        ).status_code
        == 403
    )


@pytest.fixture(scope="session")
def resources(auth_settings):
    async def create(db):
        owner = await db.scalar(select(User).where(User.login == CLIENT["login"]))
        second = await db.scalar(select(User).where(User.login == SECOND["login"]))
        application = Application(owner_id=owner.id)
        other = Application(owner_id=second.id)
        db.add_all([application, other])
        await db.flush()
        documents = []
        for app, user in ((application, owner), (other, second)):
            doc = Document(
                application_id=app.id,
                uploaded_by=user.id,
                application_revision=1,
                document_type="DEMO",
                filename="demo.txt",
                media_type="text/plain",
                size_bytes=0,
                storage_key=uuid4().hex,
                sha256="0" * 64,
            )
            db.add(doc)
            documents.append(doc)
        await db.flush()
        return str(application.id), str(other.id), str(documents[0].id), str(documents[1].id)

    return in_database(auth_settings, create)


@pytest.mark.parametrize("account", [CLIENT, SECOND, BANK], ids=["owner", "other-client", "bank"])
def test_all_business_routes_check_access_before_501(client, account, resources):
    from test_contract import OPERATIONS, wire_request

    from app.api import examples as ex

    login(client, account)
    token = csrf(client)
    application, _, document, foreign_document = resources
    for path, method, operation in OPERATIONS:
        if path.startswith("/api/auth/"):
            continue
        verb, url, kwargs = wire_request(path, method, operation)
        url = url.replace(ex.APPLICATION_ID, application).replace(ex.DOCUMENT_ID, document)
        kwargs["headers"].update(headers(token))
        roles = operation["x-required-roles"]
        role = "underwriter" if account == BANK else "client"
        expected = 501
        if role not in roles:
            expected = 403
        elif account == SECOND and "{application_id}" in path:
            expected = 404
        response = client.request(verb, url, **kwargs)
        assert response.status_code == expected, (method, path, response.status_code)
    # A permitted application does not permit substituting a document from another one.
    if account != SECOND:
        assert (
            client.get(
                f"/api/applications/{application}/documents/{foreign_document}/download"
            ).status_code
            == 404
        )
        assert (
            client.get(f"/api/applications/{uuid4()}/documents/{document}/download").status_code
            == 404
        )


def test_local_http_cookie_opt_in(auth_settings):
    settings = auth_settings.model_copy(
        update={"auth_cookie_secure": False, "auth_allowed_origin": "http://testserver"}
    )
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        token = csrf(client)
        response = client.post(
            "/api/auth/login",
            json={"login": CLIENT["login"], "password": CLIENT["password"]},
            headers=headers(token, "http://testserver"),
        )
        assert response.status_code == 200
        assert all("Secure" not in cookie for cookie in response.headers.get_list("set-cookie"))
        assert client.get("/api/auth/me").status_code == 200
        token = csrf(client)
        assert (
            client.post("/api/auth/logout", headers=headers(token, "http://testserver")).status_code
            == 204
        )


@pytest.mark.parametrize("reason", ["revoked", "removed", "secret_rotated", "malformed", "expired"])
def test_csrf_recovers_invalid_browser_session(client, auth_settings, reason):
    login(client)
    old_csrf = csrf(client)
    old_cookie = client.cookies.get("csrf_context")
    claims = decode_session(auth_settings, client.cookies.get("access_token"))
    session_id = UUID(claims["sid"])

    async def invalidate(db):
        session = await db.get(AuthSession, session_id)
        if reason == "revoked":
            session.revoked_at = datetime.now(UTC)
        else:
            await db.execute(delete(CSRFContext).where(CSRFContext.session_id == session_id))
            await db.delete(session)

    settings = auth_settings
    if reason in {"revoked", "removed"}:
        in_database(settings, invalidate)
    elif reason == "secret_rotated":
        settings = settings.model_copy(
            update={"auth_jwt_secret": SecretStr("rotated-test-secret-with-at-least-32-characters")}
        )
    else:
        # Keep a browser-valid cookie while its JWT is invalid or expired.
        token = "malformed-jwt"
        if reason == "expired":
            claims["exp"] = int((datetime.now(UTC) - timedelta(seconds=60)).timestamp())
            token = jwt.encode(claims, SECRET, algorithm="HS256")
        for cookie in client.cookies.jar:
            if cookie.name == "access_token":
                cookie.value = token

    with TestClient(create_app(settings), base_url=ORIGIN) as browser:
        browser.cookies.update(client.cookies)
        credentials = {"login": CLIENT["login"], "password": CLIENT["password"]}
        assert browser.get("/api/auth/me").status_code == 401
        assert browser.post("/api/auth/logout", headers=headers(old_csrf)).status_code == 401
        assert (
            browser.post("/api/auth/login", json=credentials, headers=headers(old_csrf)).status_code
            == 401
        )

        response = browser.get("/api/auth/csrf")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert browser.cookies.get("access_token") is None
        assert browser.cookies.get("csrf_context") != old_cookie
        cleared = next(h for h in response.headers.get_list("set-cookie") if "access_token=" in h)
        assert all(
            flag in cleared
            for flag in ("Max-Age=0", "HttpOnly", "Secure", "SameSite=lax", "Path=/")
        )
        new_csrf = response.json()["csrf_token"]
        assert new_csrf != old_csrf and csrf(browser) == new_csrf
        assert browser.get("/api/auth/me").status_code == 401
        assert (
            browser.post("/api/auth/login", json=credentials, headers=headers(old_csrf)).status_code
            == 403
        )
        assert (
            browser.post(
                "/api/auth/login", json=credentials, headers={"X-CSRF-Token": new_csrf}
            ).status_code
            == 403
        )
        assert (
            browser.post(
                "/api/auth/login",
                json=credentials,
                headers=headers(new_csrf, "https://foreign.test"),
            ).status_code
            == 403
        )
        assert (
            browser.post(
                "/api/auth/login",
                json={**credentials, "password": "wrong"},
                headers=headers(new_csrf),
            ).status_code
            == 401
        )
        assert (
            browser.post("/api/auth/login", json=credentials, headers=headers(new_csrf)).status_code
            == 200
        )
        assert browser.get("/api/auth/me").json()["login"] == CLIENT["login"]
        assert csrf(browser) != new_csrf
