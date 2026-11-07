"""Create the next day's task copies at midnight, even with the app closed."""
import logging
import time
from datetime import datetime, timedelta, timezone
from shared_notes import notes
from mickeys_notes.app import carry_over, current_day, db

while True:
    try:
        with db() as con:
            carry_over(con, current_day())
    except Exception:
        logging.exception('Notes midnight rollover failed')
    now = datetime.now(timezone(timedelta(hours=-7)))
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    time.sleep(max(0.1, min(60, (midnight-now).total_seconds())))
