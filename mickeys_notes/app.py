import os, sqlite3, secrets, hmac, time, json
from pathlib import Path
from datetime import date, timedelta
import calendar
from flask import Flask, request, session, jsonify, render_template, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=os.environ.get('RENDER') == 'true', MAX_CONTENT_LENGTH=1024*1024, PERMANENT_SESSION_LIFETIME=timedelta(days=365))
DATA = Path(os.environ.get('NOTES_DATA_DIR', Path(__file__).parent / 'data'))
if os.environ.get('RENDER') == 'true':
    disk_root = Path(os.environ.get('NOTES_DISK_ROOT', DATA)).resolve()
    if not os.environ.get('NOTES_DATA_DIR') or not os.path.ismount(disk_root) or not DATA.resolve().is_relative_to(disk_root):
        raise RuntimeError('A persistent disk must be mounted at NOTES_DATA_DIR before this app starts.')
    if not os.environ.get('SECRET_KEY'):
        raise RuntimeError('Set a persistent SECRET_KEY before deploying.')
DATA.mkdir(parents=True, exist_ok=True)
DEFAULT_ROUTINES = {
    'morning': [
        {'id': 'morning-wake', 'text': 'Wake up between 6:00–6:45 a.m.'},
        {'id': 'morning-bible', 'text': 'Read 1 page of the Bible'},
        {'id': 'morning-prayer', 'text': 'Pray'},
        {'id': 'morning-water', 'text': 'Drink 2 cups of copper water'},
    ],
    'night': [
        {'id': 'night-visualization', 'text': '10 minutes of visualization'},
        {'id': 'night-bmt', 'text': '30–45 minutes of solid work on BMT'},
        {'id': 'night-bible', 'text': 'Read 1 page of the Bible'},
        {'id': 'night-prayer-reflection', 'text': 'Prayer and reflection'},
    ],
}
def db():
    con = sqlite3.connect(DATA / 'notes.sqlite3', timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')
    return con

@app.before_request
def protect():
    if request.path == '/healthz' or request.path.startswith('/static/'):
        return
    password = os.environ.get('NOTES_PASSWORD')
    public_access = app.config.get('PUBLIC_ACCESS', False)
    if request.path == '/transfer' and public_access:
        return 'Not found', 404
    if not password and os.environ.get('RENDER') and not public_access:
        return 'Set NOTES_PASSWORD before using this app.', 503
    if password and not public_access and not session.get('authenticated') and request.path != '/login':
        return (jsonify(error='Please sign in again.'), 401) if request.path.startswith('/api/') else redirect(url_for('login'))
    if (request.method == 'PUT' or request.path == '/transfer' and request.method == 'POST') and not hmac.compare_digest(request.headers.get('X-CSRF-Token', '') or request.form.get('csrf', ''), session.get('csrf', '!')):
        return jsonify(error='Refresh the page and try again.'), 403

@app.route('/login', methods=['GET','POST'])
def login():
    if app.config.get('PUBLIC_ACCESS', False):
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        if hmac.compare_digest(request.form.get('password',''), os.environ.get('NOTES_PASSWORD','')):
            session.clear(); session.permanent = True; session['authenticated'] = True
            return redirect(url_for('index'))
        time.sleep(1)
        error = 'That password didn’t match.'
    return render_template('login.html', error=error)

@app.get('/')
def index():
    session.setdefault('csrf', secrets.token_hex(24))
    return render_template('index.html', csrf=session['csrf'])

@app.get('/general')
def general_page():
    session.setdefault('csrf', secrets.token_hex(24))
    return render_template('general.html', csrf=session['csrf'])

@app.route('/api/general', methods=['GET', 'PUT'])
def general_data():
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS general (id INTEGER PRIMARY KEY CHECK(id=1), content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')
        if request.method == 'GET':
            row = con.execute('SELECT content, revision FROM general WHERE id=1').fetchone()
            return jsonify(content=json.loads(row['content']) if row else {'tasks': [], 'draft': ''}, revision=row['revision'] if row else 0)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Invalid list'), 400
        content = payload.get('content')
        if not isinstance(content, dict) or not isinstance(content.get('tasks'), list) or not isinstance(content.get('draft', ''), str):
            return jsonify(error='Invalid list'), 400
        tasks = content['tasks']
        if not all(isinstance(t, dict) and isinstance(t.get('id'), str) and isinstance(t.get('text'), str) and isinstance(t.get('done'), bool) for t in tasks) or len({t['id'] for t in tasks}) != len(tasks):
            return jsonify(error='Invalid tasks'), 400
        content = {'tasks': [t for t in tasks if not t['done']] + [t for t in tasks if t['done']], 'draft': content.get('draft', '')}
        con.execute('INSERT OR IGNORE INTO general VALUES (1, ?, 0)', ('{"tasks":[],"draft":""}',))
        result = con.execute('UPDATE general SET content=?, revision=revision+1 WHERE id=1 AND revision=?', (json.dumps(content), payload.get('revision')))
        if result.rowcount != 1:
            return jsonify(error='This list changed in another tab. Reload before editing.'), 409
        return jsonify(revision=payload['revision'] + 1)

@app.get('/manifest.webmanifest')
def manifest():
    base = request.script_root
    return jsonify(id=base+'/', name='Mickey’s Notes', short_name='Mickey’s Notes',
                   start_url=base+'/', scope=base+'/', display='standalone',
                   background_color='#f7f8f5', theme_color='#f7f8f5',
                   icons=[dict(src=url_for('static', filename='icon.png'), sizes='512x512', type='image/png', purpose='any')])

@app.after_request
def private_cache(response):
    if not request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.route('/transfer', methods=['GET', 'POST'])
def transfer():
    session.setdefault('csrf', secrets.token_hex(24))
    message = None
    if request.method == 'POST':
        try:
            payload = json.load(request.files['backup'].stream)
            days = payload['days']
            if payload.get('version') != 1 or not isinstance(days, list):
                raise ValueError()
            for item in days:
                date.fromisoformat(item['day'])
                content = item['content']
                if not isinstance(content, dict) or not isinstance(content.get('lists'), list) or not isinstance(content.get('note'), str):
                    raise ValueError()
                int(item.get('revision', 0))
            routines_data = payload.get('routines')
            if routines_data is not None and not all(isinstance(routines_data.get(key), list) for key in ['morning', 'night']):
                raise ValueError()
        except (KeyError, ValueError, TypeError, AttributeError):
            return 'That backup is not valid. No data was changed.', 400
        with db() as con:
            con.execute('CREATE TABLE IF NOT EXISTS routines (id INTEGER PRIMARY KEY CHECK(id=1), content TEXT NOT NULL)')
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT COUNT(*) FROM days').fetchone()[0] or con.execute('SELECT COUNT(*) FROM routines').fetchone()[0]:
                return 'This app already contains saved data. Import was stopped to avoid overwriting it.', 409
            for item in days:
                con.execute('INSERT INTO days VALUES (?,?,?)', (item['day'], json.dumps(item['content']), int(item.get('revision', 0))))
            if routines_data is not None:
                con.execute('INSERT INTO routines VALUES (1,?)', (json.dumps(routines_data),))
        message = f"Imported {len(days)} saved day(s)."
    return render_template('transfer.html', csrf=session['csrf'], message=message)

@app.route('/api/routines', methods=['GET', 'PUT'])
def routines():
    import json
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS routines (id INTEGER PRIMARY KEY CHECK(id=1), content TEXT NOT NULL)')
        if request.method == 'GET':
            row = con.execute('SELECT content FROM routines WHERE id=1').fetchone()
            content = json.loads(row['content']) if row else DEFAULT_ROUTINES
            content = {**content, 'morning': [habit for habit in content['morning'] if habit['id'] not in {'morning-apple-cider', 'morning-shower'}]}
            return jsonify(content)
        content=request.get_json()
        if not isinstance(content,dict) or not all(isinstance(content.get(k),list) and all(isinstance(t,dict) and isinstance(t.get('id'),str) and isinstance(t.get('text'),str) for t in content[k]) for k in ['morning','night']):
            return jsonify(error='Invalid routines'),400
        con.execute('INSERT OR REPLACE INTO routines VALUES(1,?)',(json.dumps(content),))
        return jsonify(ok=True)

@app.get('/healthz')
def health(): return {'status':'ok'}

@app.get('/api/calendar/<month>')
def calendar_days(month):
    try:
        first = date.fromisoformat(month + '-01')
        if first.strftime('%Y-%m') != month:
            raise ValueError()
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    except ValueError:
        return jsonify(error='Invalid month'), 400
    with db() as con:
        rows = con.execute('SELECT day FROM days WHERE day BETWEEN ? AND ? ORDER BY day', (first.isoformat(), last.isoformat())).fetchall()
    return jsonify(days=[row['day'] for row in rows])

@app.route('/api/days/<day>', methods=['GET','PUT'])
def day_data(day):
    try: date.fromisoformat(day)
    except ValueError: return jsonify(error='Invalid date'), 400
    with db() as con:
        if request.method == 'GET':
            row = con.execute('SELECT * FROM days WHERE day=?',(day,)).fetchone()
            import json
            return jsonify(content=json.loads(row['content']) if row else {'lists':[], 'note':''}, revision=row['revision'] if row else 0)
        import json
        payload = request.get_json()
        if not isinstance(payload, dict): return jsonify(error='Invalid notes'),400
        content = payload.get('content',{})
        if not isinstance(content,dict) or not isinstance(content.get('lists'), list) or not isinstance(content.get('note'), str): return jsonify(error='Invalid notes'),400
        revision = payload.get('revision')
        con.execute('INSERT OR IGNORE INTO days VALUES (?, ?, 0)', (day, '{"lists":[],"note":""}'))
        result=con.execute('UPDATE days SET content=?, revision=revision+1 WHERE day=? AND revision=?',(json.dumps(content),day,revision))
        if result.rowcount != 1: return jsonify(error='This day changed in another tab. Reload before editing.'),409
        return jsonify(revision=revision+1)

if __name__ == '__main__': app.run(port=int(os.environ.get('PORT', 5055)), host='127.0.0.1')
