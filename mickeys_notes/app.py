import os, sqlite3, secrets, hmac, time, json
from pathlib import Path
from datetime import date, timedelta, timezone
from datetime import datetime
import calendar
from flask import Flask, request, session, jsonify, render_template, redirect, url_for

app = Flask(__name__)
app.url_map.redirect_defaults = False
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
        {'id': 'morning-water', 'text': 'Drink water'},
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
    con.execute('CREATE TABLE IF NOT EXISTS task_rollovers (source_day TEXT NOT NULL, task_id TEXT NOT NULL, PRIMARY KEY(source_day, task_id))')
    return con

def current_day():
    # B.C. observes UTC-7 year-round from November 2026 onward.
    return datetime.now(timezone(timedelta(hours=-7))).date().isoformat()

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

LIST_TITLES = {'business': 'Business', 'week': 'This Week', 'month': 'This Month', 'quarter': 'This Quarter', 'unfinished': 'Unfinished Tasks'}

@app.get('/lists/<list_key>')
def list_page(list_key):
    if list_key not in LIST_TITLES:
        return 'Not found', 404
    session.setdefault('csrf', secrets.token_hex(24))
    return render_template('general.html', csrf=session['csrf'], list_key=list_key, list_title=LIST_TITLES[list_key])

@app.get('/general')
def general_page():
    session.setdefault('csrf', secrets.token_hex(24))
    return redirect(url_for('list_page', list_key='business'))

@app.route('/api/lists/<list_key>', methods=['GET', 'PUT'])
@app.route('/api/general', methods=['GET', 'PUT'], defaults={'list_key': 'business'})
def general_data(list_key):
    if list_key not in LIST_TITLES:
        return jsonify(error='Unknown list'), 404
    if list_key != 'business':
        return planning_data(list_key)
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

def planning_data(list_key):
    with db() as con:
        if list_key == 'unfinished':
            carry_over(con, current_day())
        con.execute('CREATE TABLE IF NOT EXISTS planning_lists (id TEXT PRIMARY KEY, content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')
        if request.method == 'GET':
            row = con.execute('SELECT content, revision FROM planning_lists WHERE id=?', (list_key,)).fetchone()
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
        con.execute('INSERT OR IGNORE INTO planning_lists VALUES (?, ?, 0)', (list_key, '{"tasks":[],"draft":""}'))
        result = con.execute('UPDATE planning_lists SET content=?, revision=revision+1 WHERE id=? AND revision=?', (json.dumps(content), list_key, payload.get('revision')))
        if result.rowcount != 1:
            return jsonify(error='This list changed in another tab. Reload before editing.'), 409
        return jsonify(revision=payload['revision'] + 1)

@app.put('/api/tasks/move')
def move_task():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('task_id'), str):
        return jsonify(error='Choose a task to move.'), 400
    def parse_ref(ref):
        if not isinstance(ref, dict):
            raise ValueError()
        key = ref.get('list')
        if key == 'days':
            day = ref.get('date')
            date.fromisoformat(day)
            return ('days', day)
        if key not in LIST_TITLES:
            raise ValueError()
        return ('general', 1) if key == 'business' else ('planning_lists', key)
    try:
        source, target = parse_ref(payload.get('source')), parse_ref(payload.get('target'))
    except (TypeError, ValueError):
        return jsonify(error='Choose a valid list and date.'), 400
    if source == target:
        return jsonify(error='Choose a different destination.'), 400
    task_id = payload['task_id']
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS general (id INTEGER PRIMARY KEY CHECK(id=1), content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')
        con.execute('CREATE TABLE IF NOT EXISTS planning_lists (id TEXT PRIMARY KEY, content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')
        con.execute('BEGIN IMMEDIATE')
        def read(ref):
            table, key = ref
            column = 'day' if table == 'days' else 'id'
            row = con.execute(f'SELECT content, revision FROM {table} WHERE {column}=?', (key,)).fetchone()
            blank = {'lists': [], 'note': ''} if table == 'days' else {'tasks': [], 'draft': ''}
            return (json.loads(row['content']), row['revision']) if row else (blank, 0)
        def tasks(ref, content):
            return [t for group in content['lists'] for t in group['tasks']] if ref[0] == 'days' else content['tasks']
        def write(ref, content):
            table, key = ref
            column = 'day' if table == 'days' else 'id'
            con.execute(f'INSERT OR IGNORE INTO {table} VALUES (?, ?, 0)', (key, json.dumps(content)))
            con.execute(f'UPDATE {table} SET content=?, revision=revision+1 WHERE {column}=?', (json.dumps(content), key))
        content, revision = read(source)
        destination, _ = read(target)
        task = next((t for t in tasks(source, content) if t['id'] == task_id), None)
        existing = any(t['id'] == task_id for t in tasks(target, destination))
        if task is None and existing:
            return jsonify(content=content, revision=revision)
        if revision != payload.get('revision'):
            return jsonify(error='This list changed in another tab. Reload before moving.'), 409
        if task is None:
            return jsonify(error='This task no longer exists. Reload and try again.'), 404
        if existing:
            return jsonify(error='This task already exists at the destination. Reload your lists.'), 409
        if source[0] == 'days':
            for group in content['lists']:
                group['tasks'] = [t for t in group['tasks'] if t['id'] != task_id]
            content['taskOrder'] = [key for key in content.get('taskOrder', []) if key != task_id]
            con.execute('INSERT OR IGNORE INTO task_rollovers VALUES (?,?)', (source[1], task_id))
        else:
            content['tasks'] = [t for t in content['tasks'] if t['id'] != task_id]
        if target[0] == 'days':
            group = next((g for g in destination['lists'] if g['id'] == 'daily-' + target[1]), None)
            if group is None:
                group = {'id': 'daily-' + target[1], 'title': 'Daily list', 'priority': 'Medium', 'tasks': []}
                destination['lists'].append(group)
            destination['taskOrder'] = [t['id'] for t in sorted(tasks(target, destination), key=lambda t: (destination.get('taskOrder', []).index(t['id']) if t['id'] in destination.get('taskOrder', []) else len(destination.get('taskOrder', []))))] + [task_id]
            group['tasks'].append(dict(task))
            # Allow a deliberately moved-back task to carry forward again.
            con.execute('DELETE FROM task_rollovers WHERE source_day=? AND task_id=?', (target[1], task_id))
        else:
            destination['tasks'].insert(0, dict(task))
            destination['tasks'] = [t for t in destination['tasks'] if not t['done']] + [t for t in destination['tasks'] if t['done']]
        write(target, destination)
        write(source, content)
        return jsonify(content=content, revision=revision + 1)

@app.put('/api/days/<day>/move-to-general')
def move_to_general(day):
    try:
        date.fromisoformat(day)
    except ValueError:
        return jsonify(error='Invalid date'), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('task_id'), str):
        return jsonify(error='Choose a daily task to move.'), 400
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS general (id INTEGER PRIMARY KEY CHECK(id=1), content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM days WHERE day=?', (day,)).fetchone()
        if not row:
            return jsonify(error='This day no longer exists. Reload and try again.'), 404
        content = json.loads(row['content'])
        task_id = payload['task_id']
        task = next((t for group in content['lists'] for t in group['tasks'] if t['id'] == task_id), None)
        general_row = con.execute('SELECT * FROM general WHERE id=1').fetchone()
        general = json.loads(general_row['content']) if general_row else {'tasks': [], 'draft': ''}
        existing = any(t['id'] == task_id for t in general['tasks'])
        # Retry safely if the first response was lost after the transaction committed.
        if task is None and existing:
            return jsonify(content=content, revision=row['revision'])
        if row['revision'] != payload.get('revision'):
            return jsonify(error='This day changed in another tab. Reload before moving this task.'), 409
        if task is None:
            return jsonify(error='This task no longer exists. Reload and try again.'), 404
        if existing:
            return jsonify(error='This task is already in Business. Reload your lists.'), 409
        general['tasks'].insert(0, dict(task))
        general['tasks'] = [t for t in general['tasks'] if not t['done']] + [t for t in general['tasks'] if t['done']]
        for group in content['lists']:
            group['tasks'] = [t for t in group['tasks'] if t['id'] != task_id]
        content['taskOrder'] = [key for key in content.get('taskOrder', []) if key != task_id]
        con.execute('INSERT OR IGNORE INTO general VALUES (1, ?, 0)', ('{"tasks":[],"draft":""}',))
        con.execute('UPDATE general SET content=?, revision=revision+1 WHERE id=1', (json.dumps(general),))
        con.execute('UPDATE days SET content=?, revision=revision+1 WHERE day=?', (json.dumps(content), day))
        con.execute('INSERT OR IGNORE INTO task_rollovers VALUES (?,?)', (day, task_id))
        return jsonify(content=content, revision=row['revision'] + 1)

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
            if day == current_day():
                carry_over(con, day)
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
        if day < current_day():
            # Include a final autosave arriving just after midnight.
            carry_over(con, current_day())
        return jsonify(revision=revision+1)

def carry_over(con, today):
    """Collect overdue daily tasks once; retain dated historical snapshots.

    Shared planning lists never expire. The latest dated snapshot wins over
    older copies from the previous carry-forward system.
    """
    if not con.in_transaction:
        con.execute('BEGIN IMMEDIATE')
    con.execute('CREATE TABLE IF NOT EXISTS planning_lists (id TEXT PRIMARY KEY, content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0)')
    rows = con.execute('SELECT day, content FROM days ORDER BY day DESC').fetchall()
    latest, incoming = set(), []
    for row in rows:
        content = json.loads(row['content'])
        order = {key: i for i, key in enumerate(content.get('taskOrder', []))}
        tasks = [t for group in content['lists'] for t in group['tasks']]
        tasks.sort(key=lambda t: order.get(t['id'], len(order)))
        processed = {r['task_id'] for r in con.execute('SELECT task_id FROM task_rollovers WHERE source_day=?', (row['day'],))}
        for task in tasks:
            task_id = task['id']
            if row['day'] < today and task_id not in processed:
                if task_id not in latest and not task.get('done', False):
                    incoming.append(dict(task))
                con.execute('INSERT OR IGNORE INTO task_rollovers VALUES (?,?)', (row['day'], task_id))
            latest.add(task_id)
    if not incoming:
        return
    row = con.execute("SELECT content FROM planning_lists WHERE id='unfinished'").fetchone()
    target = json.loads(row['content']) if row else {'tasks': [], 'draft': ''}
    existing = {t['id'] for t in target['tasks']}
    new = [t for t in incoming if t['id'] not in existing]
    if not new:
        return
    target['tasks'] = new + [t for t in target['tasks'] if not t['done']] + [t for t in target['tasks'] if t['done']]
    con.execute('INSERT OR IGNORE INTO planning_lists VALUES (?, ?, 0)', ('unfinished', json.dumps(target)))
    con.execute("UPDATE planning_lists SET content=?, revision=revision+1 WHERE id='unfinished'", (json.dumps(target),))

if __name__ == '__main__': app.run(port=int(os.environ.get('PORT', 5055)), host='127.0.0.1')
