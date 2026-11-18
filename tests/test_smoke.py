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
