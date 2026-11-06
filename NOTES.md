# Mickey’s Notes on the existing estimator service

The owner explicitly approved password-free public access on November 5, 2026. Anyone with the URL can read/edit notes. The migration route is disabled in public mode.

The existing Render Starter service and its existing 1 GB `/var/data` disk host both apps. No additional service or disk is needed.

- Existing quoting app: `/` (Streamlit on loopback port 8501).
- Notes: `/notes/` (Gunicorn on loopback port 8502).
- Caddy proxies HTTP and WebSocket traffic on `$PORT`.
- Notes open without a password (`PUBLIC_ACCESS=True` in shared_notes.py), with a separate CSRF session cookie scoped to `/notes`. Quoting authentication is unchanged.
- Notes SQLite file: `/var/data/mickeys-notes/notes.sqlite3`. Quoting files are unchanged.
- A stable, application-specific session signing key is derived from the notes passcode. Changing the passcode signs notes sessions out.
- Build: `bash build.sh`; start: `python serve.py`. Keep the existing service plan, region, disk and environment variables unchanged.
- Private saved-day migration: temporarily turn off public mode before using `/notes/transfer` with a version-1 JSON backup. Import only succeeds into an empty notes database; no existing entries are overwritten.
- The notes source is bundled in `mickeys_notes/` from the private `mickeys-notes` repository. Keep that directory in sync for future notes releases. No user notes or credentials belong in Git.
- Phone: open `/notes/` in Safari, then Share → Add to Home Screen.

Rollback: restore the previous Streamlit start command if necessary; the original quoting source and database have not changed. Notes stay on the disk even if the notes route is disabled.
