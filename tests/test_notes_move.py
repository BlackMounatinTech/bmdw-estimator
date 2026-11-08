import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import mickeys_notes.app as notes


class MoveTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = notes.DATA
        notes.DATA = Path(self.tmp.name)
        self.client = notes.app.test_client()
        with self.client.session_transaction() as session:
            session['csrf'] = 'test'
        self.day = '2026-11-07'
        self.content = {'lists': [{'id': 'daily', 'tasks': [
            {'id': 'a', 'text': 'Call supplier', 'done': False},
            {'id': 'b', 'text': 'Keep here', 'done': False}]}],
            'note': 'Keep note', 'draft': 'Keep draft', 'habits': {'morning-water': True}, 'taskOrder': ['a', 'b']}
        with notes.db() as con:
            con.execute('INSERT INTO days VALUES (?,?,4)', (self.day, json.dumps(self.content)))
            con.execute('CREATE TABLE general (id INTEGER PRIMARY KEY, content TEXT NOT NULL, revision INTEGER NOT NULL)')
            con.execute('INSERT INTO general VALUES (1,?,7)', (json.dumps({'tasks': [
                {'id': 'old', 'text': 'Existing', 'done': False},
                {'id': 'done', 'text': 'Done', 'done': True}], 'draft': 'General draft'}),))

    def tearDown(self):
        notes.DATA = self.old
        self.tmp.cleanup()

    def move(self, revision=4, csrf='test'):
        return self.client.put('/api/days/' + self.day + '/move-to-general',
            json={'task_id': 'a', 'revision': revision}, headers={'X-CSRF-Token': csrf})

    def read(self, table):
        with notes.db() as con:
            row = con.execute('SELECT * FROM ' + table).fetchone()
            return json.loads(row['content']), row['revision']

    def test_atomic_move_preserves_other_data_and_retries(self):
        response = self.move()
        self.assertEqual(response.status_code, 200)
        daily, rev = self.read('days')
        self.assertEqual(rev, 5)
        self.assertEqual([t['id'] for t in daily['lists'][0]['tasks']], ['b'])
        for key in ['note', 'draft', 'habits']:
            self.assertEqual(daily[key], self.content[key])
        self.assertEqual(daily['taskOrder'], ['b'])
        general, rev = self.read('general')
        self.assertEqual(rev, 8)
        self.assertEqual([t['id'] for t in general['tasks']], ['a', 'old', 'done'])
        self.assertEqual(general['draft'], 'General draft')
        self.assertEqual(self.move().status_code, 200)
        self.assertEqual(self.read('general')[1], 8)
        with notes.db() as con:
            notes.carry_over(con, '2026-11-08')
            tomorrow = json.loads(con.execute('SELECT content FROM days WHERE day=?', ('2026-11-08',)).fetchone()[0])
        self.assertEqual([t['id'] for group in tomorrow['lists'] for t in group['tasks']], ['b'])

    def test_stale_revision_and_csrf_leave_both_lists_unchanged(self):
        self.assertEqual(self.move(revision=3).status_code, 409)
        self.assertEqual(self.move(csrf='wrong').status_code, 403)
        self.assertEqual(self.read('days'), (self.content, 4))
        self.assertEqual(self.read('general')[1], 7)

    def test_general_save_conflicts_after_move(self):
        general, rev = self.read('general')
        self.assertEqual(self.move().status_code, 200)
        response = self.client.put('/api/general', json={'content': general, 'revision': rev}, headers={'X-CSRF-Token': 'test'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.read('general')[0]['tasks'][0]['id'], 'a')

    def test_database_failure_rolls_back_both_lists(self):
        with notes.db() as con:
            con.execute("CREATE TRIGGER fail_day BEFORE UPDATE ON days BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with patch.dict(notes.app.config, {'TESTING': False}):
            self.assertEqual(self.move().status_code, 500)
        self.assertEqual(self.read('days'), (self.content, 4))
        self.assertEqual(self.read('general')[1], 7)


if __name__ == '__main__':
    unittest.main()
