"""Smoke tests: run with `python -m pytest` from the repo root."""
import base64
import importlib
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp())
os.environ.update(
    LIBRARY_PATH=str(ROOT / "library"),
    DATA_PATH=str(ROOT / "data"),
    APP_PIN="1234",
    SECRET_KEY="replace-with-a-long-random-value",
    TRUSTED_PROXIES="10.9.9.9",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
app_module = importlib.import_module("app")
app = app_module.app
app.config["TESTING"] = True


def logged_in_client():
    client = app.test_client()
    app_module.failed_logins.clear()
    response = client.post("/login", data={"pin": "1234", "next": "/"})
    assert response.status_code == 302
    return client


def csrf(client):
    html = client.get("/upload").get_data(as_text=True)
    return html.split('name="csrf_token" value="')[1].split('"')[0]


def test_placeholder_secret_key_is_ignored():
    assert app.secret_key != "replace-with-a-long-random-value"


def test_health_is_public():
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"


def test_pages_need_pin_and_logout_hidden_on_login():
    client = app.test_client()
    assert client.get("/").status_code == 302
    page = client.get("/login").get_data(as_text=True)
    assert "Log out" not in page


def test_main_routes_after_login():
    client = logged_in_client()
    for path in ("/", "/upload", "/?sort=newest&q=x"):
        assert client.get(path).status_code == 200


def test_open_redirect_blocked():
    client = app.test_client()
    app_module.failed_logins.clear()
    for target in ("//evil.example", "/\\evil.example", "https://evil.example"):
        response = client.post("/login", data={"pin": "1234", "next": target})
        assert response.headers["Location"] == "/"


def test_pin_lockout_and_non_ascii_pin():
    client = app.test_client()
    app_module.failed_logins.clear()
    for _ in range(5):
        assert client.post("/login", data={"pin": "£999"}).status_code == 200
    assert client.post("/login", data={"pin": "1234"}).status_code == 429


def test_path_traversal_id_is_404():
    client = logged_in_client()
    bad_id = base64.urlsafe_b64encode(b"../../etc/passwd").decode().rstrip("=")
    assert client.get(f"/book/{bad_id}/download").status_code == 404


def test_upload_download_remove_mobi():
    client = logged_in_client()
    mobi = b"\0" * 60 + b"BOOKMOBI" + b"\0" * 100
    response = client.post(
        "/upload",
        data={"csrf_token": csrf(client), "ebook": (io.BytesIO(mobi), "My Book - Author.mobi")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert (ROOT / "library" / "My_Book_-_Author.mobi").is_file()

    book_id = app_module.encode_book_id("My_Book_-_Author.mobi")
    assert client.get(f"/book/{book_id}/download").data == mobi

    assert client.post(f"/book/{book_id}/remove", data={}).status_code == 400
    response = client.post(f"/book/{book_id}/remove", data={"csrf_token": csrf(client)})
    assert response.status_code == 302
    assert not (ROOT / "library" / "My_Book_-_Author.mobi").exists()


def test_upload_rejects_non_mobi():
    client = logged_in_client()
    response = client.post(
        "/upload",
        data={"csrf_token": csrf(client), "ebook": (io.BytesIO(b"hello"), "x.mobi")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"does not appear to be a valid MOBI" in response.data


def test_forwarded_for_only_trusted_from_proxy():
    app_module.failed_logins.clear()
    direct = app.test_client()
    for spoofed in range(6):
        direct.post(
            "/login",
            data={"pin": "nope"},
            headers={"X-Forwarded-For": f"203.0.113.{spoofed}"},
            environ_base={"REMOTE_ADDR": "192.0.2.50"},
        )
    assert "192.0.2.50" in app_module.failed_logins
    assert len(app_module.failed_logins) == 1

    app_module.failed_logins.clear()
    proxied = app.test_client()
    proxied.post(
        "/login",
        data={"pin": "nope"},
        headers={"X-Forwarded-For": "198.51.100.7"},
        environ_base={"REMOTE_ADDR": "10.9.9.9"},
    )
    assert list(app_module.failed_logins) == ["198.51.100.7"]


def test_csrf_token_is_tied_to_session():
    alice, mallory = logged_in_client(), logged_in_client()
    token = csrf(mallory)
    (ROOT / "library" / "Keep Me.epub").write_bytes(b"x")
    book_id = app_module.encode_book_id("Keep Me.epub")
    assert alice.post(f"/book/{book_id}/remove", data={"csrf_token": token}).status_code == 400
    assert (ROOT / "library" / "Keep Me.epub").exists()


def test_cache_prune_keeps_current_books_only():
    (ROOT / "library" / "Cached - Writer.epub").write_bytes(b"x")
    book = app_module.get_book_from_id(app_module.encode_book_id("Cached - Writer.epub"))
    current = app_module.conversion_cache_path(book).parent
    stale = app_module.CACHE_ROOT / "stale-entry"
    current.mkdir(parents=True, exist_ok=True)
    stale.mkdir(parents=True, exist_ok=True)
    app_module.prune_conversion_cache()
    assert current.is_dir() and not stale.exists()


def test_conversion_busy_message(monkeypatch):
    (ROOT / "library" / "Queued - Writer.epub").write_bytes(b"x")
    book = app_module.get_book_from_id(app_module.encode_book_id("Queued - Writer.epub"))
    monkeypatch.setattr(app_module, "CONVERSION_QUEUE_SECONDS", 0.05)
    assert app_module.conversion_slots.acquire(timeout=1)
    try:
        try:
            app_module.convert_to_mobi(book)
        except app_module.ConversionError as error:
            assert "Try again" in str(error)
        else:
            raise AssertionError("expected ConversionError")
    finally:
        app_module.conversion_slots.release()
