import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import mickeys_notes.app as notes

class DailyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=notes.DATA;notes.DATA=Path(self.tmp.name)
    def tearDown(self):
        notes.DATA=self.old;self.tmp.cleanup()
    def put(self,day,tasks,**extra):
        content={'lists':[{'id':'daily','tasks':tasks}],'note':'day note',**extra}
        with notes.db() as con:con.execute('INSERT OR REPLACE INTO days VALUES(?,?,4)',(day,json.dumps(content)))
        return content
    def get(self,day):
        with notes.db() as con:r=con.execute('SELECT * FROM days WHERE day=?',(day,)).fetchone()
        return dict(r,content=json.loads(r['content'])) if r else None
    def queue(self):
        with notes.db() as con:r=con.execute("SELECT content FROM planning_lists WHERE id='unfinished'").fetchone()
        return json.loads(r['content'])['tasks'] if r else []
    def roll(self,day):
        with notes.db() as con:notes.carry_over(con,day)
    def test_only_unfinished_daily_tasks_roll_and_history_is_preserved(self):
        original=self.put('2026-11-06',[{'id':'a','text':'Call','done':False},{'id':'b','text':'Finished','done':True}],habits={'old':True},draft='draft')
        self.roll('2026-11-07');self.roll('2026-11-07')
        self.assertEqual(self.get('2026-11-06')['content'],original)
        self.assertEqual(self.get('2026-11-06')['revision'],4)
        self.assertEqual([t['id'] for t in self.queue()],['a'])
        self.assertIsNone(self.get('2026-11-07'))
    def test_completed_or_deleted_queue_tasks_do_not_resurrect(self):
        self.put('2026-11-06',[{'id':'a','text':'Call','done':False}]);self.roll('2026-11-07')
        with notes.db() as con:con.execute("UPDATE planning_lists SET content=? WHERE id='unfinished'", ('{"tasks":[],"draft":""}',))
        self.roll('2026-11-10');self.assertEqual(self.queue(),[])
    def test_legacy_copies_use_latest_status_without_duplicates(self):
        for day in ['2026-11-06','2026-11-07']:
            self.put(day,[{'id':'a','text':'Call','done':False},{'id':'b','text':'Done later','done':False}])
        self.put('2026-11-08',[{'id':'a','text':'Call','done':False},{'id':'b','text':'Done later','done':True}])
        self.roll('2026-11-10')
        self.assertEqual([t['id'] for t in self.queue()],['a'])
        self.assertIsNone(self.get('2026-11-10'))
    def test_planned_days_and_other_lists_do_not_roll(self):
        self.put('2026-11-06',[{'id':'a','text':'First','done':False},{'id':'b','text':'Second','done':False}],taskOrder=['b','a'])
        planned=self.put('2026-11-07',[{'id':'c','text':'Planned','done':False}])
        client=notes.app.test_client()
        with client.session_transaction() as sess:sess['csrf']='test'
        with patch.object(notes,'current_day',return_value='2026-11-07'):
            for key in ['business','week','month','quarter']:
                client.put('/api/lists/'+key,json={'revision':0,'content':{'tasks':[{'id':key,'text':key,'done':False}],'draft':'keep'}},headers={'X-CSRF-Token':'test'})
            self.roll('2026-11-07')
            self.assertEqual([t['id'] for t in self.queue()],['b','a'])
            self.assertEqual(self.get('2026-11-07')['content'],planned)
            for key in ['business','week','month','quarter']:
                self.assertEqual(client.get('/api/lists/'+key).json['content']['draft'],'keep')
    def test_late_autosave_and_unfinished_get_trigger(self):
        self.put('2026-11-06',[]);self.roll('2026-11-07')
        client=notes.app.test_client()
        with client.session_transaction() as sess:sess['csrf']='test'
        content={'lists':[{'id':'daily','tasks':[{'id':'late','text':'Last task','done':False}]}],'note':''}
        with patch.object(notes,'current_day',return_value='2026-11-07'):
            response=client.put('/api/days/2026-11-06',json={'content':content,'revision':4},headers={'X-CSRF-Token':'test'})
            result=client.get('/api/lists/unfinished')
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(result.json['content']['tasks']),1)
        self.assertIsNone(self.get('2026-11-07'))
