import unittest
from pathlib import Path
import tempfile
import json
from src.notice_fetch import ROOT, load_session, NoRedirects, collect_community_index, collect_entry_titles
from src.notice_fetch import collect_pages, input_paths, notice_path, paginate_notices, parse_html, sanitize_notices, select_notice_scope

PATH = '/notice/' + 'a' * 64 + '/index.html'

class NoticeFetchTests(unittest.TestCase):
    def test_extract_matches_webview_nodes(self):
        page = parse_html('<html><head><title>お知らせ</title></head><body><h1>予告</h1><p>本文<span>詳細</span>続き</p><script>secret</script><style>x</style><input value="x"><textarea>private</textarea><p>本文</p></body></html>')
        self.assertEqual(page, {'title': 'お知らせ', 'headline': '予告', 'texts': ['予告', '本文', '詳細', '続き']})

    def test_accept_only_official_paths(self):
        self.assertEqual(notice_path('https://stat.game-idolypride.jp'+PATH), PATH)
        self.assertEqual(notice_path('https://stat.game-idolypride.jp'+PATH+'?_v=20260925-144044'), PATH)
        for value in ['http://stat.game-idolypride.jp'+PATH, '//evil.invalid'+PATH, PATH+'?token=a', PATH+'#x', '/notice/../secret', 'https://stat.game-idolypride.jp.evil.invalid'+PATH]:
            with self.subTest(value=value), self.assertRaises(ValueError): notice_path(value)

    def test_cache_revision_cannot_smuggle_query_or_fragment(self):
        for suffix in ['?_v=secret', '?_v=20260925-144044&token=a', '?_v=20260925-144044#x', '?_v=20260925-144044&_v=20260925-144044']:
            with self.subTest(suffix=suffix), self.assertRaises(ValueError): notice_path(PATH+suffix)

    def test_community_index_is_static_and_explicitly_partial(self):
        calls = []
        def fetch(category, limit):
            calls.append((category, limit))
            return [{'id': category, 'title': '静态公告', 'roootAssociateToken': 'secret', 'commonResponse': {'user': 'private'}}]
        data = collect_community_index(100, fetch)
        self.assertEqual(calls, [('notices', 100), ('malfunctionNotices', 100), ('prNotices', 100)])
        self.assertEqual(data['notices'], [{'id': 'notices', 'title': '静态公告'}])
        self.assertIn('not_guaranteed_exhaustive', data['_provenance']['completeness'])
        self.assertEqual(data['_provenance']['returned_per_category']['notices'], 1)
        for limit in (0, 501):
            with self.assertRaises(ValueError): collect_community_index(limit, fetch)

    def test_entry_titles_keep_distinct_fields_and_source_hashes(self):
        original = {'notices': [{'id': 'one', 'title': '本文の題', 'listTitle': '【予告】一覧の題',
                                 'linkDetail': 'https://stat.game-idolypride.jp'+PATH, 'roootAssociateToken': 'secret'}]}
        first = collect_entry_titles(original)['entries'][0]
        self.assertEqual(first['title'], '本文の題')
        self.assertEqual(first['listTitle'], '【予告】一覧の題')
        self.assertNotIn('roootAssociateToken', first)
        self.assertNotEqual(first['field_sha256']['title'], first['field_sha256']['listTitle'])
        original['notices'][0]['listTitle'] = '更新した一覧の題'
        second = collect_entry_titles(original)['entries'][0]
        self.assertNotEqual(first['source_sha256'], second['source_sha256'])
        self.assertEqual(first['field_sha256']['title'], second['field_sha256']['title'])

    def test_entry_titles_reject_ambiguous_identity(self):
        for records in [[{'title':'missing ID'}], [{'id':'same'}, {'id':'same'}]]:
            with self.assertRaises(ValueError): collect_entry_titles({'notices': records})

    def test_source_and_index_input(self):
        self.assertEqual(input_paths({'pages': {PATH: {}}, 'notices': [{'linkDetail':'https://stat.game-idolypride.jp'+PATH},{'linkDetail':'ingame-navigation'}]}), [PATH])
        self.assertEqual(collect_pages({'paths':[PATH]}, lambda _: '<body><p>本文</p></body>')['pages'][PATH]['texts'], ['本文'])

    def test_remove_user_response_and_tokens(self):
        self.assertEqual(sanitize_notices([{'id':'1','title':'題','commonResponse':{'user':'private'},'roootAssociateToken':'secret'}]), [{'id':'1','title':'題'}])

    def test_pagination_all_categories(self):
        calls=[]
        def fetch(category, offset):
            calls.append((category,offset)); return {'notices':[{'id':str(category)+'-next'}]}
        result=paginate_notices({'notices':[{'id':'1'}],'noticeHasNext':True,'malfunctionNotices':[],'malfunctionNoticeHasNext':True}, fetch)
        self.assertEqual(calls,[(1,1),(2,0)])
        self.assertEqual(len(result['notices']),2)

    def test_pagination_refuses_stalls_and_truncation(self):
        for batch in [[],[{'id':'1'}]]:
            with self.assertRaises(ValueError):
                paginate_notices({'notices':[{'id':'1'}],'noticeHasNext':True},lambda *_:{'notices':batch,'hasNext':True})
        with self.assertRaises(ValueError):
            paginate_notices({'noticeHasNext':True},lambda *_:{'notices':[{'id':'2'}],'hasNext':True},max_pages=1)

    def test_default_window_never_fetches_history_or_claims_no_expiry(self):
        first = {'notices': [{'id': 'first', 'title': '案内', 'startTime': 1}], 'noticeHasNext': True,
                 'commonResponse': {'private': True}}
        def forbidden(*args):
            self.fail('Default window must not call FetchList')
        result = select_notice_scope(first, forbidden)
        self.assertEqual(result['notices'], first['notices'])
        self.assertEqual(result['_pagination']['scope'], 'current_window')
        self.assertTrue(result['_pagination']['first_page_has_next']['notices'])
        self.assertFalse(result['_pagination']['exhaustive'])
        self.assertNotIn('commonResponse', result)

    def test_archive_is_explicit_and_follows_pagination(self):
        calls = []
        def more(category, offset):
            calls.append((category, offset)); return {'notices': [{'id': 'history'}], 'hasNext': False}
        result = select_notice_scope({'notices': [{'id': 'first'}], 'noticeHasNext': True}, more, archive=True)
        self.assertEqual(calls, [(1, 1)])
        self.assertEqual([r['id'] for r in result['notices']], ['first', 'history'])
        self.assertEqual(result['_pagination']['scope'], 'archive')
        self.assertTrue(result['_pagination']['exhaustive'])

    def test_session_must_stay_in_cache(self):
        with self.assertRaises(ValueError): load_session(Path('/tmp/session.json'))
        with tempfile.TemporaryDirectory(dir=ROOT / 'cache') as directory:
            path=Path(directory)/'session.json'
            path.write_text(json.dumps({'metadata':{'x-app-version':'1.0','x-auth-token':'test'}}))
            self.assertEqual(load_session(path)['x-app-version'],'1.0')
            path.write_text(json.dumps({'metadata':{'x-app-version':'1.0','host':'evil.invalid'}}))
            with self.assertRaises(ValueError): load_session(path)

    def test_redirects_do_not_forward(self):
        with self.assertRaises(ValueError): NoRedirects().redirect_request(None,None,302,'',{},'https://evil.invalid')

    def test_empty_html_rejected(self):
        with self.assertRaises(ValueError): parse_html('<html><head><title>Error</title></head></html>')

if __name__ == '__main__': unittest.main()
