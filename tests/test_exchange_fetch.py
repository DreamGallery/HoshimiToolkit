import unittest
from src.exchange_fetch import collect_exchange

class ExchangeTests(unittest.TestCase):
    def test_all_booths_and_static_whitelist(self):
        calls = []
        def fetch(method, unused):
            calls.append(method)
            return {'booths': [{'id': 'a', 'name': '店', 'unlocked': True, 'exchanges': [{'id': 'x', 'name': '商品', 'description': '説明', 'rewardId': 'r', 'leftCount': 3, 'unlocked': True, 'nextResetTime': '123'}]}, {'id': 'b', 'exchanges': []}], 'commonResponse': {'account': 'private'}}
        result = collect_exchange(fetch)
        self.assertEqual(calls, ['List'])
        self.assertTrue(result['complete'])
        self.assertEqual(result['returned_booth_count'], 2)
        self.assertEqual(result['unique_exchange_count'], 1)
        for field in ['commonResponse', 'unlocked', 'leftCount', 'nextResetTime', 'private']:
            self.assertNotIn(field, str(result))

    def test_conflicting_duplicate(self):
        for name, complete in [('first', True), ('other', False)]:
            result = collect_exchange(lambda *args: {'booths': [{'id': 'a', 'exchanges': [{'id': 'x', 'name': 'first'}, {'id': 'x', 'name': name}]}]})
            self.assertEqual(result['complete'], complete)
            self.assertEqual(result['unique_exchange_count'], 1)
            if not complete: self.assertEqual(result['conflicts'][0]['field'], 'name')

    def test_failed_request_never_empty_success(self):
        def fetch(*args): raise RuntimeError('private server response')
        with self.assertRaises(RuntimeError): collect_exchange(fetch)

    def test_invalid_id_and_collection(self):
        result = collect_exchange(lambda *args: {'booths': [{'id': 'a', 'exchanges': [{'name': 'missing'}]}, {'id': 'b', 'exchanges': 'bad'}]})
        self.assertFalse(result['complete'])
        self.assertEqual(result['successful_booth_count'], 0)

    def test_empty_valid_directory(self):
        self.assertTrue(collect_exchange(lambda *args: {})['complete'])
