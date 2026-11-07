import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
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
    def roll(self,day):
        with notes.db() as con:notes.carry_over(con,day)
    def tasks(self,day):return [t for l in self.get(day)['content']['lists'] for t in l['tasks']]
    def test_unchecked_history_and_routines_preserved(self):
        original=self.put('2026-11-06',[{'id':'a','text':'Call customer','done':False},{'id':'b','text':'Finished','done':True}],habits={'morning-wake':True,'night-bible':False},draft='draft',taskOrder=['b','a'])
        self.roll('2026-11-07');self.roll('2026-11-07')
        self.assertEqual(self.get('2026-11-06')['content'],original)
        self.assertEqual(self.get('2026-11-06')['revision'],4)
        self.assertEqual([t['id'] for t in self.tasks('2026-11-07')],['a'])
        self.assertNotIn('habits',self.get('2026-11-07')['content'])
        self.assertNotIn('draft',self.get('2026-11-07')['content'])
    def test_finishing_today_does_not_check_yesterday_or_resurrect(self):
        self.put('2026-11-06',[{'id':'a','text':'Call','done':False}]);self.roll('2026-11-07')
        self.put('2026-11-07',[{'id':'a','text':'Call','done':True}]);self.roll('2026-11-08')
        self.assertFalse(self.tasks('2026-11-06')[0]['done'])
        self.assertTrue(self.tasks('2026-11-07')[0]['done'])
        self.assertIsNone(self.get('2026-11-08'))
    def test_deletion_not_recreated_and_missing_days_get_history(self):
        self.put('2026-11-06',[{'id':'a','text':'Call','done':False}]);self.roll('2026-11-09')
        for day in ['2026-11-06','2026-11-07','2026-11-08','2026-11-09']:
            self.assertEqual(len(self.tasks(day)),1)
        self.put('2026-11-09',[]);self.roll('2026-11-10');self.roll('2026-11-10')
        self.assertIsNone(self.get('2026-11-10'))
        self.assertEqual(self.tasks('2026-11-09'),[])
    def test_planned_tasks_and_order_preserved(self):
        self.put('2026-11-06',[{'id':'a','text':'First','done':False},{'id':'b','text':'Second','done':False}],taskOrder=['b','a'])
        self.put('2026-11-07',[{'id':'c','text':'Planned','done':False}],habits={'morning-wake':True})
        self.roll('2026-11-07')
        day=self.get('2026-11-07')['content']
        self.assertEqual(day['taskOrder'],['b','a','c'])
        self.assertTrue(day['habits']['morning-wake'])
    def test_final_autosave_after_midnight_is_carried_once(self):
        self.put('2026-11-06',[]);self.roll('2026-11-07')
        client=notes.app.test_client()
        with client.session_transaction() as sess:sess['csrf']='test'
        content={'lists':[{'id':'daily','tasks':[{'id':'late','text':'Last task','done':False}]}],'note':''}
        with patch.object(notes,'current_day',return_value='2026-11-07'):
            response=client.put('/api/days/2026-11-06',json={'content':content,'revision':4},headers={'X-CSRF-Token':'test'})
        self.assertEqual(response.status_code,200)
        self.roll('2026-11-07')
        self.assertEqual(len(self.tasks('2026-11-07')),1)
        self.assertEqual(self.get('2026-11-06')['content'],content)

if __name__=='__main__':unittest.main()
