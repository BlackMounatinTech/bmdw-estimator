import tempfile
import unittest
from pathlib import Path
import mickeys_notes.app as notes

class MoveAllTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = notes.DATA
        notes.DATA = Path(self.tmp.name)
        self.client = notes.app.test_client()
        with self.client.session_transaction() as session:
            session['csrf'] = 'test'
        self.headers = {'X-CSRF-Token': 'test'}
    def tearDown(self):
        notes.DATA = self.old
        self.tmp.cleanup()
    def test_all_twenty_directions_and_retries(self):
        keys = ['days', *notes.LIST_TITLES]
        def url(key):
            return '/api/days/2099-06-01' if key == 'days' else '/api/lists/' + key
        def ref(key):
            return {'list': key, 'date': '2099-06-01'} if key == 'days' else {'list': key}
        for source in keys:
            for target in keys:
                if source == target: continue
                for file in notes.DATA.glob('*.sqlite3'): file.unlink()
                task = {'id': 'move', 'text': 'Exact text', 'done': True, 'priority': 'High', 'completed_on': notes.current_day()}
                content = {'lists': [{'id': 'daily', 'tasks': [task]}], 'note': 'Keep', 'draft': 'Draft', 'habits': {'old': True}, 'taskOrder': ['move']} if source == 'days' else {'tasks': [task], 'draft': 'Draft'}
                self.assertEqual(self.client.put(url(source), json={'content': content, 'revision': 0}, headers=self.headers).status_code, 200)
                body = {'task_id': 'move', 'revision': 1, 'source': ref(source), 'target': ref(target)}
                response = self.client.put('/api/tasks/move', json=body, headers=self.headers)
                self.assertEqual(response.status_code, 200, (source, target, response.json))
                moved = self.client.get(url(target)).json['content']
                tasks = [t for g in moved['lists'] for t in g['tasks']] if target == 'days' else moved['tasks']
                self.assertEqual(tasks, [task])
                after = response.json['content']
                self.assertEqual(after['draft'], 'Draft')
                if source == 'days':
                    self.assertEqual(after['note'], 'Keep')
                    self.assertEqual(after['habits'], {'old': True})
                    self.assertEqual(after['taskOrder'], [])
                    with notes.db() as con:
                        self.assertIsNotNone(con.execute("SELECT 1 FROM task_rollovers WHERE source_day='2099-06-01' AND task_id='move'").fetchone())
                self.assertEqual(self.client.put('/api/tasks/move', json=body, headers=self.headers).status_code, 200)
                self.assertEqual(self.client.get(url(target)).json['revision'], 1)
    def test_stale_invalid_and_rollback(self):
        self.client.put('/api/lists/business', json={'revision': 0, 'content': {'tasks': [{'id': 'x', 'text': 'Keep', 'done': False}], 'draft': ''}}, headers=self.headers)
        body = {'task_id': 'x', 'revision': 0, 'source': {'list': 'business'}, 'target': {'list': 'week'}}
        self.assertEqual(self.client.put('/api/tasks/move', json=body, headers=self.headers).status_code, 409)
        body['revision'] = 1
        body['target'] = {'list': 'days', 'date': 'invalid'}
        self.assertEqual(self.client.put('/api/tasks/move', json=body, headers=self.headers).status_code, 400)
        body['target'] = {'list': 'week'}
        self.client.get('/api/lists/week')
        with notes.db() as con:
            con.execute("CREATE TRIGGER fail_move BEFORE UPDATE ON general BEGIN SELECT RAISE(ABORT,'rollback test'); END")
        self.assertEqual(self.client.put('/api/tasks/move', json=body, headers=self.headers).status_code, 500)
        self.assertEqual(len(self.client.get('/api/lists/business').json['content']['tasks']), 1)
        self.assertEqual(self.client.get('/api/lists/week').json['content']['tasks'], [])
