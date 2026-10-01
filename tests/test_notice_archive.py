import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from src import notice_archive as archive

ONE = '/notice/' + 'a'*64 + '/index.html'
TWO = '/notice/' + 'b'*64 + '/index.html'


class NoticeArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache/pages'
        self.cache.mkdir(parents=True)
        self.output = self.root / 'source.json'
        self.report = self.root / 'report.json'
        self.patch = patch.object(archive, 'ROOT', self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_successful_pages_survive_failure_and_resume(self):
        def fetch(path):
            if path == TWO: raise HTTPError('https://unused.invalid',404,'',{},None)
            return '<body><h1>本文</h1></body>'
        report = archive.archive_pages({'paths':[ONE,TWO]},self.cache,self.output,self.report,fetch=fetch,workers=2)
        self.assertEqual(report['downloaded'],1)
        self.assertEqual(report['failed'],1)
        self.assertFalse(report['complete'])
        self.assertEqual(report['failures'][0]['http_status'],404)
        self.assertEqual(report['failures'][0]['attempts'],1)
        self.assertIn(ONE,json.loads(self.output.read_text())['pages'])
        calls=[]
        def retry(path):
            calls.append(path);return '<body><p>追加</p></body>'
        report = archive.archive_pages({'paths':[ONE,TWO]},self.cache,self.output,self.report,fetch=retry)
        self.assertEqual(calls,[TWO])
        self.assertTrue(report['complete'])
        self.assertEqual(report['cached'],1)

    def test_corrupt_cache_and_refresh_fetch_again(self):
        calls=[]
        def fetch(path):
            calls.append(path);return '<body><p>本文</p></body>'
        archive.collect_one(ONE,self.cache,False,fetch)
        archive.collect_one(ONE,self.cache,False,fetch)
        self.assertEqual(len(calls),1)
        cache=self.cache/('a'*64+'.json')
        data=json.loads(cache.read_text());data['page']['texts']=['corrupt'];cache.write_text(json.dumps(data))
        archive.collect_one(ONE,self.cache,False,fetch)
        archive.collect_one(ONE,self.cache,True,fetch)
        self.assertEqual(len(calls),3)

    def test_transient_retries_are_bounded(self):
        calls=[]
        def fail(path):
            calls.append(path);raise HTTPError('https://unused.invalid',503,'',{},None)
        report=archive.collect_one(ONE,self.cache,False,fail,sleep=lambda _:None)
        self.assertEqual(len(calls),3)
        self.assertEqual(report['attempts'],3)
        self.assertEqual(report['http_status'],503)


if __name__ == '__main__': unittest.main()
