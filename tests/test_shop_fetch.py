import unittest
from src.shop_fetch import collect_shop, METHODS

class ShopTests(unittest.TestCase):
    def test_full_directory_and_whitelist(self):
        calls = []
        def fetch(method, sid):
            calls.append((method, sid))
            if method == 'List':
                return {'shops': [{'id': 'one', 'name': '一', 'type': 'ShopType_NormalShop', 'isPurchased': True}, {'id': 'two', 'type': 'ShopType_LoginBonusShop'}, {'id': 'three', 'type': 4}], 'commonResponse': {'secret': 'private'}}
            item = {'id': sid + '-item', 'name': '商品', 'description': '説明', 'leftCount': 99, 'unlocked': True}
            if method == 'ListItem':
                return {'shopItems': [item]}
            return {('loginBonusPackageItem' if 'Login' in method else 'conditionRewardPackageItem'): {'shopItem': item, 'isPurchased': True}}
        result = collect_shop(fetch)
        self.assertTrue(result['complete'])
        self.assertEqual(result['successful_shop_count'], 3)
        self.assertIn(('ListItem', 'two'), calls)
        self.assertIn(('GetConditionRewardPackageItem', 'three'), calls)
        text = str(result)
        for forbidden in ('leftCount', 'isPurchased', 'commonResponse', 'unlocked', 'private'):
            self.assertNotIn(forbidden, text)
        self.assertTrue(set(method for method, _ in calls) <= METHODS)

    def test_failure_distinct_from_empty_and_continue(self):
        def fetch(method, sid):
            if method == 'List':
                return {'shops': [{'id': 'bad'}, {'id': 'empty'}]}
            if sid == 'bad':
                raise RuntimeError('secret token server payload')
            return {}
        result = collect_shop(fetch)
        self.assertFalse(result['complete'])
        self.assertEqual(result['shops'][0]['status'], 'failed')
        self.assertEqual(result['shops'][1]['requests'][0]['returned_count'], 0)
        self.assertNotIn('secret', str(result))

    def test_missing_package_not_empty_success(self):
        result = collect_shop(lambda method, sid: {'shops': [{'id': 'bonus', 'type': 3}]} if method == 'List' else {})
        self.assertFalse(result['complete'])
        self.assertEqual(result['shops'][0]['requests'][1]['status'], 'failed')

    def test_directory_failure_propagates(self):
        def fetch(*args):
            raise RuntimeError('no directory')
        with self.assertRaises(RuntimeError):
            collect_shop(fetch)

    def test_duplicate_id_incomplete(self):
        result = collect_shop(lambda method, sid: {'shops': [{'id': 'a'}, {'id': 'a'}]} if method == 'List' else {})
        self.assertFalse(result['complete'])

class RecoveryTests(unittest.TestCase):
    def test_recovery_once_each(self):
        from src.shop_fetch import recover_read, ShopReadError
        queue = ['1003', '1002', '401', None, '1003']
        events = []
        def call(*args):
            code = queue.pop(0)
            if code: raise ValueError(code)
            return {'ok': True}
        read = recover_read(call, lambda error: [str(error), 'private'], refresh_auth=lambda: events.append('auth'), update_master=lambda: events.append('master'), initialize_day=lambda: events.append('day'), allow_auth=True, allow_day=True)
        self.assertEqual(events, [])
        self.assertEqual(read('List', None), {'ok': True})
        self.assertEqual(events, ['day', 'master', 'auth'])
        with self.assertRaises(ShopReadError) as caught: read('ListItem', 'a')
        self.assertEqual(caught.exception.safe_codes, ['1003'])

    def test_disabled_recovery(self):
        from src.shop_fetch import recover_read, ShopReadError
        events = []
        def call(*args): raise ValueError('1003')
        read = recover_read(call, lambda error: ['1003'], refresh_auth=lambda: events.append('auth'), update_master=lambda: events.append('master'), initialize_day=lambda: events.append('day'))
        with self.assertRaises(ShopReadError): read('List', None)
        self.assertEqual(events, [])

    def test_duplicate_payload_conflict(self):
        def build(name):
            def fetch(method, sid):
                if method == 'List': return {'shops': [{'id': 'a', 'type': 3}]}
                if method == 'ListItem': return {'shopItems': [{'id': 'x', 'name': 'original'}]}
                return {'loginBonusPackageItem': {'shopItem': {'id': 'x', 'name': name}}}
            return collect_shop(fetch)
        self.assertTrue(build('original')['complete'])
        changed = build('changed')
        self.assertFalse(changed['complete'])
        self.assertEqual(changed['shops'][0]['conflicts'], [{'item_id': 'x', 'field': 'name'}])
