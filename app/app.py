# version 5
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

APP_VERSION = "1.2.2"
LIBRARY_ROOT = Path(os.getenv("LIBRARY_PATH", "/library")).resolve()
DATA_ROOT = Path(os.getenv("DATA_PATH", "/data")).resolve()
CACHE_ROOT = DATA_ROOT / "cache"
APP_PIN = os.getenv("APP_PIN", "").strip()
CONVERSION_TIMEOUT_SECONDS = max(
    30,
    int(os.getenv("CONVERSION_TIMEOUT_SECONDS", "300")),
)
MAX_UPLOAD_SIZE_MB = max(1, int(os.getenv("MAX_UPLOAD_SIZE_MB", "100")))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

SUPPORTED_EXTENSIONS = {
    ".azw",
    ".azw3",
    ".docx",
    ".epub",
    ".fb2",
    ".htm",
    ".html",
    ".mobi",
    ".odt",
    ".pdf",
    ".prc",
    ".rtf",
    ".txt",
}

def load_or_create_secret_key() -> str:
    configured_key = os.getenv("SECRET_KEY", "").strip()
    if configured_key:
        return configured_key

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    secret_path = DATA_ROOT / "secret_key"

    try:
        existing_key = secret_path.read_text(encoding="utf-8").strip()
        if existing_key:
            return existing_key
    except FileNotFoundError:
        pass

    generated_key = secrets.token_hex(32)
    try:
        descriptor = os.open(
            secret_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        existing_key = secret_path.read_text(encoding="utf-8").strip()
        if existing_key:
            return existing_key
        raise RuntimeError(f"Secret key file is empty: {secret_path}")

    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(generated_key)
        handle.write("\n")
    return generated_key


app = Flask(__name__)
app.secret_key = load_or_create_secret_key()
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    APP_PIN=bool(APP_PIN),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
    MAX_CONTENT_LENGTH=MAX_UPLOAD_SIZE_MB * 1024 * 1024,
    MAX_UPLOAD_SIZE_MB=MAX_UPLOAD_SIZE_MB,
)


@dataclass(frozen=True)
class Book:
    relative_path: str
    absolute_path: Path
    book_id: str
    title: str
    author: str
    extension: str
    size_bytes: int
    modified_timestamp: float

    @property
    def size_label(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    @property
    def modified_label(self) -> str:
        return datetime.fromtimestamp(
            self.modified_timestamp,
            tz=timezone.utc,
        ).strftime("%d %b %Y")


class ConversionError(RuntimeError):
    pass


def ensure_directories() -> None:
    LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)


def encode_book_id(relative_path: str) -> str:
    return base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")


def decode_book_id(book_id: str) -> str:
    try:
        padded = book_id + "=" * (-len(book_id) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise FileNotFoundError("Invalid book identifier") from error


def resolve_library_file(relative_path: str) -> Path:
    candidate = (LIBRARY_ROOT / relative_path).resolve()
    try:
        candidate.relative_to(LIBRARY_ROOT)
    except ValueError as error:
        raise FileNotFoundError("Book is outside the configured library") from error

    if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise FileNotFoundError("Book was not found")
    return candidate


def clean_display_text(value: str) -> str:
    value = value.replace("_", " ").replace(".", " ")
    return re.sub(r"\s+", " ", value).strip()


def title_and_author(path: Path) -> tuple[str, str]:
    stem = clean_display_text(path.stem)
    separators = (" - ", " – ", " — ", " by ")
    for separator in separators:
        if separator in stem:
            left, right = stem.rsplit(separator, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    return stem or path.name, "Unknown author"


def make_book(path: Path) -> Book:
    relative = path.relative_to(LIBRARY_ROOT).as_posix()
    stat = path.stat()
    title, author = title_and_author(path)
    return Book(
        relative_path=relative,
        absolute_path=path,
        book_id=encode_book_id(relative),
        title=title,
        author=author,
        extension=path.suffix.lower().lstrip(".").upper(),
        size_bytes=stat.st_size,
        modified_timestamp=stat.st_mtime,
    )


def iter_library_files() -> Iterable[Path]:
    if not LIBRARY_ROOT.exists():
        return []
    return (
        path
        for path in LIBRARY_ROOT.rglob("*")
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def scan_books(search: str = "", sort: str = "title") -> list[Book]:
    query = search.casefold().strip()
    books: list[Book] = []

    for path in iter_library_files():
        try:
            book = make_book(path)
        except (FileNotFoundError, OSError):
            continue

        searchable = f"{book.title} {book.author} {book.relative_path}".casefold()
        if query and query not in searchable:
            continue
        books.append(book)

    if sort == "newest":
        books.sort(key=lambda item: item.modified_timestamp, reverse=True)
    elif sort == "format":
        books.sort(key=lambda item: (item.extension, item.title.casefold()))
    else:
        books.sort(key=lambda item: (item.title.casefold(), item.author.casefold()))
    return books


def get_book_from_id(book_id: str) -> Book:
    relative_path = decode_book_id(book_id)
    return make_book(resolve_library_file(relative_path))


def safe_download_stem(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^A-Za-z0-9()\[\] _.-]+", "", ascii_text).strip(" .")
    return ascii_text[:140] or "ebook"


def unique_library_path(filename: str) -> Path:
    candidate = LIBRARY_ROOT / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        numbered = LIBRARY_ROOT / f"{stem} ({counter}){suffix}"
        if not numbered.exists():
            return numbered
        counter += 1


def has_mobi_header(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(80)
    except OSError:
        return False
    return len(header) >= 68 and header[60:68] == b"BOOKMOBI"


def copy_upload_to_library(source: Path, destination: Path) -> None:
    """Copy a validated upload into the library and commit it atomically.

    DATA_ROOT and LIBRARY_ROOT are separate Docker bind mounts, so a direct
    os.replace(source, destination) can fail with EXDEV. The staging file is
    created beside the destination, making the final os.replace operation a
    same-filesystem rename.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".kindleshelf-upload-",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as staging_file:
            staging_path = Path(staging_file.name)
            with source.open("rb") as source_file:
                shutil.copyfileobj(source_file, staging_file, length=1024 * 1024)
            staging_file.flush()
            os.fsync(staging_file.fileno())

        os.chmod(staging_path, 0o664)
        os.replace(staging_path, destination)
        staging_path = None
    finally:
        if staging_path is not None:
            try:
                staging_path.unlink(missing_ok=True)
            except OSError:
                pass


def conversion_cache_path(book: Book) -> Path:
    cache_key = hashlib.sha256(
        (
            f"{book.relative_path}\0{book.size_bytes}\0"
            f"{book.modified_timestamp}\0mobi-v1"
        ).encode("utf-8")
    ).hexdigest()
    return CACHE_ROOT / cache_key / f"{safe_download_stem(book.title)}.mobi"


def convert_to_mobi(book: Book) -> Path:
    if book.absolute_path.suffix.lower() == ".mobi":
        return book.absolute_path

    output_path = conversion_cache_path(book)
    if output_path.is_file() and output_path.stat().st_size > 0:
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_path.parent) as temporary_directory:
        temporary_output = Path(temporary_directory) / output_path.name
        command = [
            "ebook-convert",
            str(book.absolute_path),
            str(temporary_output),
            "--output-profile",
            "kindle",
            "--mobi-file-type",
            "both",
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=CONVERSION_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as error:
            raise ConversionError("Calibre's ebook-convert command is not installed.") from error
        except subprocess.TimeoutExpired as error:
            raise ConversionError("The conversion took too long and was stopped.") from error

        if result.returncode != 0 or not temporary_output.is_file():
            diagnostic = (result.stderr or result.stdout or "Unknown Calibre error").strip()
            diagnostic = diagnostic[-1200:]
            raise ConversionError(
                "Calibre could not convert this file. It may be damaged or DRM-protected. "
                f"Details: {diagnostic}"
            )

        os.replace(temporary_output, output_path)
    return output_path


CSRF_MAX_AGE_SECONDS = 12 * 60 * 60
csrf_serializer = URLSafeTimedSerializer(app.secret_key, salt="kindleshelf-csrf-v1")


def csrf_token() -> str:
    return csrf_serializer.dumps({"purpose": "form"})


def valid_csrf_token() -> bool:
    supplied = request.form.get("csrf_token", "")
    if not supplied:
        return False

    try:
        payload = csrf_serializer.loads(
            supplied,
            max_age=CSRF_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return False

    return payload == {"purpose": "form"}


app.jinja_env.globals["csrf_token"] = csrf_token


def login_required() -> bool:
    return bool(APP_PIN) and not session.get("authenticated", False)


@app.before_request
def require_pin() -> object | None:
    allowed_endpoints = {"login", "health", "static"}
    if request.endpoint not in allowed_endpoints and login_required():
        return redirect(url_for("login", next=request.full_path))
    return None


@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "form-action 'self'; frame-ancestors 'self'; base-uri 'self'",
    )
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PIN:
        return redirect(url_for("index"))

    if request.method == "POST":
        entered_pin = request.form.get("pin", "")
        if secrets.compare_digest(entered_pin, APP_PIN):
            session["authenticated"] = True
            destination = request.form.get("next") or url_for("index")
            if not destination.startswith("/") or destination.startswith("//"):
                destination = url_for("index")
            return redirect(destination)
        flash("Incorrect PIN.", "error")

    return render_template("login.html", next=request.args.get("next", ""))


@app.post("/logout")
def logout():
    if not valid_csrf_token():
        abort(400)
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "title")
    if sort not in {"title", "newest", "format"}:
        sort = "title"

    books = scan_books(search=search, sort=sort)
    return render_template(
        "index.html",
        books=books,
        search=search,
        sort=sort,
    )


@app.route("/upload", methods=["GET", "POST"])
def upload_book():
    if request.method == "GET":
        return render_template("upload.html")

    if not valid_csrf_token():
        abort(400)

    uploaded_file = request.files.get("ebook")
    if uploaded_file is None or not uploaded_file.filename:
        flash("Choose a MOBI file to upload.", "error")
        return redirect(url_for("upload_book"))

    original_name = Path(uploaded_file.filename).name
    if Path(original_name).suffix.lower() != ".mobi":
        flash("Only .mobi files can be uploaded.", "error")
        return redirect(url_for("upload_book"))

    safe_name = secure_filename(original_name)
    if not safe_name or Path(safe_name).suffix.lower() != ".mobi":
        flash("The uploaded filename is not valid.", "error")
        return redirect(url_for("upload_book"))

    ensure_directories()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="kindleshelf-upload-",
            suffix=".mobi",
            dir=DATA_ROOT,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            uploaded_file.save(temporary_file)

        if temporary_path.stat().st_size == 0:
            flash("The selected file is empty.", "error")
            return redirect(url_for("upload_book"))

        if not has_mobi_header(temporary_path):
            flash("The selected file does not appear to be a valid MOBI ebook.", "error")
            return redirect(url_for("upload_book"))

        destination = unique_library_path(safe_name)
        copy_upload_to_library(temporary_path, destination)
    except PermissionError:
        flash(
            "The file could not be uploaded because KindleShelf does not have write "
            "permission for the library folder.",
            "error",
        )
        return redirect(url_for("upload_book"))
    except OSError as error:
        flash(f"The file could not be uploaded: {error}", "error")
        return redirect(url_for("upload_book"))
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    flash(f'"{destination.name}" was uploaded to the library.', "success")
    return redirect(url_for("index"))


@app.get("/book/<book_id>/download")
def download_book(book_id: str):
    try:
        book = get_book_from_id(book_id)
    except FileNotFoundError:
        abort(404)

    try:
        output_path = convert_to_mobi(book)
    except ConversionError as error:
        return render_template("conversion_error.html", book=book, error=str(error)), 422

    return send_file(
        output_path,
        mimetype="application/x-mobipocket-ebook",
        as_attachment=True,
        download_name=f"{safe_download_stem(book.title)}.mobi",
        conditional=True,
        max_age=0,
    )


@app.route("/book/<book_id>/remove", methods=["GET", "POST"])
def remove_book(book_id: str):
    try:
        book = get_book_from_id(book_id)
    except FileNotFoundError:
        abort(404)

    if request.method == "GET":
        return render_template("confirm_remove.html", book=book)

    if not valid_csrf_token():
        abort(400)

    cached_output = conversion_cache_path(book)
    try:
        book.absolute_path.unlink()
        if cached_output.parent.is_dir():
            shutil.rmtree(cached_output.parent, ignore_errors=True)
    except PermissionError:
        flash(
            "The file could not be removed because KindleShelf does not have write permission. "
            "Check the PUID, PGID and library volume settings.",
            "error",
        )
        return redirect(url_for("index"))
    except OSError as error:
        flash(f"The file could not be removed: {error}", "error")
        return redirect(url_for("index"))

    flash(f'"{book.title}" was removed from the library.', "success")
    return redirect(url_for("index"))


@app.get("/health")
def health():
    try:
        ensure_directories()
    except OSError as error:
        return {"status": "unhealthy", "error": str(error)}, 503

    return {
        "status": "healthy",
        "version": APP_VERSION,
        "library": str(LIBRARY_ROOT),
        "library_writable": os.access(LIBRARY_ROOT, os.W_OK),
    }


@app.errorhandler(400)
def bad_request(_error):
    return render_template(
        "error.html",
        title="Invalid request",
        message="The request could not be verified. Refresh the page and try again.",
    ), 400


@app.errorhandler(404)
def not_found(_error):
    return render_template(
        "error.html",
        title="Not found",
        message="The requested page or book was not found.",
    ), 404


@app.errorhandler(413)
def upload_too_large(_error):
    return render_template(
        "error.html",
        title="Upload too large",
        message=(
            f"The selected file exceeds the {MAX_UPLOAD_SIZE_MB} MB upload limit. "
            "Increase MAX_UPLOAD_SIZE_MB in .env if required."
        ),
    ), 413


@app.errorhandler(500)
def internal_error(_error):
    return render_template(
        "error.html",
        title="Application error",
        message="Something went wrong while processing the request.",
    ), 500


ensure_directories()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
