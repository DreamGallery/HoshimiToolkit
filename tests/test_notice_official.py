import json
import sys
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src import notice_official as module


class OfficialNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'cache').mkdir()
        self.path = self.root / 'cache/account.json'
        self.root_patch = patch.object(module, 'ROOT', self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_initial_creation_is_explicit_and_reuses_cache(self):
        with self.assertRaises(module.CollectorError): module.load_account(self.path, False)
        account = module.load_account(self.path, True)
        account['firebase_refresh_token'] = 'fixture'
        module.save_account(self.path, account)
        self.assertEqual(module.load_account(self.path, False)['firebase_refresh_token'], 'fixture')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(module.CollectorError): module.load_account(self.root / 'outside.json', True)

    def test_uncertain_create_is_not_repeated_and_lock_prevents_concurrency(self):
        account = module.load_account(self.path, True)
        account['pending_create'] = 'firebase_signup'
        module.save_account(self.path, account)
        with self.assertRaises(module.CollectorError): module.load_account(self.path, True)
        with module.account_lock(self.path):
            with self.assertRaises(module.CollectorError):
                with module.account_lock(self.path): pass
        self.assertFalse(self.path.with_suffix('.json.lock').exists())

    def test_rotating_refresh_token_and_expiry_are_saved(self):
        account = {}
        before = time.time()
        module.update_firebase_tokens(account, {'id_token':'id-fixture','refresh_token':'rotated-fixture','expires_in':'3600'}, refresh=True)
        self.assertEqual(account['firebase_refresh_token'], 'rotated-fixture')
        self.assertGreater(account['expires_at'], before + 3500)
        for response in ({}, {'idToken':'x','refreshToken':'y','expiresIn':'-1'}):
            with self.assertRaises(module.CollectorError): module.update_firebase_tokens({}, response)

    def make_collector(self):
        collector = module.OfficialCollector.__new__(module.OfficialCollector)
        collector.path = self.path
        collector.allow_create = True
        collector.allow_initialize = False
        collector.account = {'schema':1,'purpose':'dedicated_notice_collector'}
        collector.app_version = 'test-version'
        collector.channel = object()
        collector.config = {'qseed':{},'grpc':{'content-type':'application/grpc'}}
        collector.requests = SimpleNamespace(get=lambda *a,**kw:SimpleNamespace(status_code=200,text='seed-fixture'),RequestException=OSError)
        collector.api = SimpleNamespace(SystemCheckRequest=lambda **kw:kw, AuthCreateRequest=lambda **kw:kw, AuthLoginRequest=lambda **kw:kw)
        # No Home/User/Reward services exist in this fake transport.
        collector.stubs = SimpleNamespace(SystemStub=lambda _:SimpleNamespace(Check='check'),AuthStub=lambda _:SimpleNamespace(Login='login'))
        collector.update_master = lambda metadata: self.operations.append('master_get')
        self.operations = []
        def firebase(operation, payload):
            self.operations.append(operation)
            if operation == 'refresh':
                return {'id_token':'refreshed-fixture','refresh_token':'rotated-fixture','expires_in':'3600'}
            return {'idToken':'id-fixture','refreshToken':'refresh-fixture','expiresIn':'3600','localId':'uid-fixture'}
        def call(method, request, metadata, **kwargs):
            self.operations.append(method)
            return SimpleNamespace(firebaseCustomToken='custom-fixture',gameAuthToken='game-fixture',requiredFirebaseReauthenticate=False)
        collector.firebase = firebase
        collector.call = call
        return collector

    def test_authentication_chain_never_calls_gameplay_and_reuses_account(self):
        collector = self.make_collector()
        collector.authenticate()
        self.assertEqual(self.operations, ['signup','check','check','login','master_get'])
        self.operations.clear()
        collector.authenticate()
        self.assertEqual(self.operations, ['check','check','login','master_get'])
        self.assertNotIn('firebase_custom_token', collector.account)
        self.assertTrue(collector.account['game_registered'])

    def test_firebase_reauthentication_refreshes_same_identity_once(self):
        collector = self.make_collector()
        original = collector.call
        logins = []
        def call(method, *args, **kwargs):
            response = original(method, *args, **kwargs)
            if method == 'login':
                logins.append(1)
                response.requiredFirebaseReauthenticate = len(logins) == 1
            return response
        collector.call = call
        collector.authenticate()
        self.assertEqual(self.operations, ['signup','check','check','login','refresh','login','master_get'])
        self.assertEqual(collector.account['firebase_uid'], 'uid-fixture')

    def test_date_changed_stops_before_any_gameplay_call(self):
        collector = self.make_collector()
        class RpcError(Exception):
            def initial_metadata(self): return [('x-error-code','1003')]
            def trailing_metadata(self): return []
        collector.grpc = SimpleNamespace(RpcError=RpcError)
        collector.account['metadata'] = {'x-app-version':'test-version','x-master-version':'fixture'}
        collector.stubs.NoticeStub = lambda _: SimpleNamespace(List='list',FetchList='fetch')
        def fail(*args, **kwargs): raise RpcError()
        collector.call = fail
        with patch.dict(sys.modules, {
            'google.protobuf.empty_pb2':SimpleNamespace(Empty=lambda:None),
            'google.protobuf.json_format':SimpleNamespace(MessageToDict=lambda obj, **kw:obj),
        }):
            with self.assertRaisesRegex(module.CollectorError, 'DateChanged.*Home.Login'):
                collector.fetch(100)
        self.assertEqual(self.operations, [])

    def test_daily_initialization_is_opt_in_and_runs_once(self):
        collector = self.make_collector()
        collector.allow_initialize = True
        class RpcError(Exception):
            def initial_metadata(self): return [('x-error-code','1003')]
            def trailing_metadata(self): return []
        collector.grpc = SimpleNamespace(RpcError=RpcError)
        collector.account['metadata'] = {'x-app-version':'test-version','x-master-version':'fixture'}
        collector.stubs.NoticeStub = lambda _: SimpleNamespace(List='list',FetchList='fetch')
        calls = []
        def fail(*args, **kwargs):
            calls.append('list')
            raise RpcError()
        collector.call = fail
        collector.initialize_day = lambda metadata: calls.append('initialize')
        with patch.dict(sys.modules, {
            'google.protobuf.empty_pb2':SimpleNamespace(Empty=lambda:None),
            'google.protobuf.json_format':SimpleNamespace(MessageToDict=lambda obj, **kw:obj),
        }):
            with self.assertRaises(module.CollectorError): collector.fetch(100)
        self.assertEqual(calls, ['list','initialize','list'])

    def test_failed_signup_leaves_uncertain_marker_without_retry(self):
        collector = self.make_collector()
        def fail(*a): raise module.CollectorError('transport')
        collector.firebase = fail
        with self.assertRaises(module.CollectorError): collector.authenticate()
        self.assertEqual(json.loads(self.path.read_text())['pending_create'], 'firebase_signup')
        with self.assertRaises(module.CollectorError): module.load_account(self.path, True)


if __name__ == '__main__': unittest.main()
