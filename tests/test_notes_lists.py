import tempfile
import unittest
from pathlib import Path
import mickeys_notes.app as notes

class PlanningListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = notes.DATA
        notes.DATA = Path(self.tmp.name)
        self.client = notes.app.test_client()
        with self.client.session_transaction() as session:
            session['csrf'] = 'test'
    def tearDown(self):
        notes.DATA = self.old
        self.tmp.cleanup()
    def put(self, key, text, revision=0):
        return self.client.put('/api/lists/' + key, json={'revision': revision, 'content': {'tasks': [{'id': key, 'text': text, 'done': False}], 'draft': key}}, headers={'X-CSRF-Token': 'test'})
    def test_isolated_persistent_lists_and_conflicts(self):
        for key in notes.LIST_TITLES:
            self.assertEqual(self.put(key, key).status_code, 200)
        for key in notes.LIST_TITLES:
            result = self.client.get('/api/lists/' + key).json
            self.assertEqual(result['content']['tasks'][0]['text'], key)
            self.assertEqual(result['revision'], 1)
            self.assertEqual(self.put(key, 'stale').status_code, 409)
        self.assertEqual(self.client.get('/api/general').json, self.client.get('/api/lists/business').json)
        self.assertEqual(self.client.get('/api/lists/invalid').status_code, 404)
    def test_navigation_and_legacy_link(self):
        for url in ['/', '/tomorrow', *('/lists/' + key for key in notes.LIST_TITLES)]:
            result = self.client.get(url)
            self.assertEqual(result.status_code, 200)
            html = result.get_data(as_text=True)
            for title in ['Today', 'Tomorrow', *notes.LIST_TITLES.values()]:
                self.assertIn('>' + title + '</a>', html)
            self.assertNotIn('General to-do', html)
        self.assertEqual(self.client.get('/general').location, '/lists/business')

    def test_today_tomorrow_have_date_heading_without_calendar(self):
        for url, mode in [('/', 'today'), ('/tomorrow', 'tomorrow')]:
            html = self.client.get(url).get_data(as_text=True)
            self.assertIn('name="day-view" content="' + mode + '"', html)
            self.assertIn('id="day-title"', html)
            self.assertNotIn('calendar-panel', html)
            self.assertNotIn('id="calendar"', html)
