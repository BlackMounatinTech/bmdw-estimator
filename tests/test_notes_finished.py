import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch
import mickeys_notes.app as notes

class FinishedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=notes.DATA; notes.DATA=Path(self.tmp.name)
        self.client=notes.app.test_client()
        with self.client.session_transaction() as s:s['csrf']='test'
        self.headers={'X-CSRF-Token':'test'}
    def tearDown(self):
        notes.DATA=self.old;self.tmp.cleanup()
    def test_all_sources_stay_today_archive_tomorrow_readonly(self):
        keys=['days',*notes.LIST_TITLES]
        with patch.object(notes,'current_day',return_value='2026-11-25'):
            for key in keys:
                task={'id':key,'text':key,'done':True}
                content={'lists':[{'id':'daily','tasks':[task]}],'note':''} if key=='days' else {'tasks':[task],'draft':'keep'}
                url='/api/days/2026-11-25' if key=='days' else '/api/lists/'+key
                self.assertEqual(self.client.put(url,json={'content':content,'revision':0},headers=self.headers).status_code,200)
                self.assertEqual(self.client.get(url).status_code,200)
            self.assertEqual(self.client.get('/api/lists/finished').json['tasks'],[])
        with patch.object(notes,'current_day',return_value='2026-11-26'):
            result=self.client.get('/api/lists/finished').json['tasks']
            self.assertEqual({x['task']['id'] for x in result},set(keys))
            self.assertTrue(all(x['completed_on']=='2026-11-25' for x in result))
            self.assertEqual(len(self.client.get('/api/lists/finished').json['tasks']),len(keys))
            for key in notes.LIST_TITLES:
                data=self.client.get('/api/lists/'+key).json['content']
                self.assertEqual(data['tasks'],[]);self.assertEqual(data['draft'],'keep')
            self.assertEqual(self.client.get('/api/lists/unfinished').json['content']['tasks'],[])
            self.assertEqual(self.client.put('/api/lists/finished',json={},headers=self.headers).status_code,403)
            html=self.client.get('/lists/finished').get_data(as_text=True)
            self.assertIn('Finished Tasks',html);self.assertNotIn('general.js',html)
    def test_uncheck_cancels_archive_and_historical_recovery(self):
        with notes.db() as con:
            con.execute('INSERT INTO days VALUES(?,?,1)',('2026-11-20',json.dumps({'lists':[{'id':'d','tasks':[{'id':'old','text':'Historical finished','done':True}]}],'note':''})))
        with patch.object(notes,'current_day',return_value='2026-11-25'):
            body={'revision':0,'content':{'tasks':[{'id':'new','text':'New','done':True}],'draft':''}}
            self.client.put('/api/lists/business',json=body,headers=self.headers)
            body['revision']=1;body['content']['tasks'][0]['done']=False
            self.client.put('/api/lists/business',json=body,headers=self.headers)
        with patch.object(notes,'current_day',return_value='2026-11-26'):
            rows=self.client.get('/api/lists/finished').json['tasks']
            self.assertEqual([r['task']['id'] for r in rows],['old'])
            self.assertEqual(rows[0]['completed_on'],'2026-11-20')
            self.assertEqual(self.client.get('/api/lists/business').json['content']['tasks'][0]['done'],False)
