"""Mount Mickey's private notes alongside the existing estimator."""
import hashlib
import os
from pathlib import Path
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.wrappers import Response

password = (os.environ.get('NOTES_PASSWORD') or os.environ.get('BMDW_APP_PASSWORD', '')).strip()
root = Path(os.environ.get('BMDW_DATA_DIR', '/var/data'))
os.environ['NOTES_DATA_DIR'] = str(root / 'mickeys-notes')
os.environ['NOTES_DISK_ROOT'] = str(root)
os.environ['NOTES_PASSWORD'] = password
os.environ['SECRET_KEY'] = hashlib.sha256(('mickeys-notes-session-v1:' + password).encode()).hexdigest()
if password:
    from mickeys_notes.app import app as notes
    notes.config.update(SESSION_COOKIE_NAME='mickeys_notes_session', SESSION_COOKIE_PATH='/notes')
else:
    # Keep the estimator available while the owner sets a notes passcode.
    notes = Response('Private notes setup: set NOTES_PASSWORD in the Render environment.', status=503)
app = DispatcherMiddleware(Response('Not found', status=404), {'/notes': notes})
