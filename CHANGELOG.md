<!-- version 3 -->
# Changelog

## 1.2.4 - 18 November 2026

- `X-Forwarded-For` is only trusted from `TRUSTED_PROXIES`, so clients connecting directly cannot fake their IP to dodge the PIN lockout.
- CSRF tokens are now tied to the browser session.
- Calibre conversions are limited by `MAX_CONCURRENT_CONVERSIONS` (default 1). Extra requests wait up to a minute, then show a "try again" message.
- Cached MOBIs for books removed or changed over SMB are now deleted after each conversion.

## 1.2.3 - 18 November 2026

- Fixed the web port: Compose mapped to container port 8112 but Gunicorn listens on 8080.
- The `SECRET_KEY` placeholder from the old `.env.example` is now ignored, so a random key is generated instead of signing sessions with a public value.
- Added a login lockout after 5 wrong PINs in 15 minutes.
- Fixed a server error when a PIN containing non-ASCII characters was entered.
- Blocked `/\host` style open redirects after login.
- Log out button is only shown when logged in.
- `SMB_PASSWORD` is now required, so Samba never starts with its built-in default password.
- Pinned the Python and Samba images; healthcheck uses Python instead of curl.
- Added `.gitignore`, `.gitattributes` (LF line endings) and pytest smoke tests.

## 1.2.2 - 10 July 2026

- Fixed MOBI uploads failing with `Invalid cross-device link` when `/data` and `/library` are separate Docker bind mounts.
- Uploads are now copied to a temporary file inside the library filesystem before an atomic final rename.
- Added cleanup for incomplete upload staging files.
- Retained the stateless signed CSRF tokens introduced in 1.2.1.
- Clarified the `SESSION_COOKIE_SECURE` setting for installations accessed through both HTTP and HTTPS.

## 1.2.1 - 10 July 2026

- Replaced session-stored CSRF tokens with signed stateless tokens so forms remain valid across Gunicorn workers and proxy access paths.
- Added a persistent generated secret key when `SECRET_KEY` is left blank.
- Added the missing CSRF token to the Log out form.
- Added proxy-aware request handling for NGINX Proxy Manager.

## 1.2.0 - 10 July 2026

- Added a web interface for uploading DRM-free `.mobi` ebooks.
- Added an **Upload** navigation link between **Library** and **Log out**.
- Added CSRF protection, filename sanitisation, MOBI header validation and duplicate-name handling for uploads.
- Added a configurable `MAX_UPLOAD_SIZE_MB` limit, defaulting to 100 MB.
- Updated the footer to link **SWAKES** to `https://www.swakes.co.uk`.
- Updated the library instructions to mention web uploads.

## 1.1.0 - 10 July 2026

- Removed the **Enter code** navigation link.
- Removed transfer-code creation and transfer-code routes.
- Removed selection checkboxes from library entries.
- Removed original-file downloads from the library interface.
- Added an **X** removal button beside every library item.
- Added a confirmation page before permanent deletion.
- Added CSRF protection to deletion requests.
- Changed the application library mount from read-only to read/write.
- Aligned Samba file ownership with `PUID` and `PGID`.
- Retained the Kindle-width compatibility improvements.
- Changed the default host web port to `8112`.

## 1.0.0

- Initial release.
