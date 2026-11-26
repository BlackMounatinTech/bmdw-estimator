import json
import tempfile
import unittest
from pathlib import Path
import mickeys_notes.app as notes

class FinishedDeleteTests(unittest.TestCase):
    def test_delete_is_selective_protected_and_not_recreated(self):
        with tempfile.TemporaryDirectory() as folder:
            old = notes.DATA
            notes.DATA = Path(folder)
            try:
                # Existing pre-delete archive schema must migrate without losing records.
                with notes.db() as con:
                    con.execute('CREATE TABLE finished_tasks (id TEXT PRIMARY KEY, task TEXT NOT NULL, completed_on TEXT, source TEXT NOT NULL, archived_on TEXT NOT NULL)')
                    for key in ['mundane', 'milestone']:
                        con.execute('INSERT INTO finished_tasks VALUES (?,?,?,?,?)',(key,json.dumps({'id':key,'text':key,'done':True}),'2026-11-01','Business','2026-11-02'))
                client=notes.app.test_client()
                with client.session_transaction() as session:session['csrf']='test'
                url='/api/finished/mundane/delete'
                self.assertEqual(client.put(url).status_code,403)
                self.assertEqual(client.put(url,headers={'X-CSRF-Token':'test'}).status_code,200)
                self.assertEqual(client.put(url,headers={'X-CSRF-Token':'test'}).status_code,200)
                self.assertEqual([x['task']['id'] for x in client.get('/api/lists/finished').json['tasks']],['milestone'])
                # An old historical snapshot cannot bring a deleted archive entry back.
                with notes.db() as con:
                    con.execute('INSERT INTO days VALUES (?,?,0)',('2026-11-01',json.dumps({'lists':[{'id':'d','tasks':[{'id':'mundane','text':'mundane','done':True}]}],'note':''})))
                self.assertEqual([x['task']['id'] for x in client.get('/api/lists/finished').json['tasks']],['milestone'])
                self.assertEqual(client.put('/api/finished/missing/delete',headers={'X-CSRF-Token':'test'}).status_code,404)
            finally:notes.DATA=old
