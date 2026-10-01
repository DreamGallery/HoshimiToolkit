import unittest
from src.help_fetch import collect_help


def category(number):
    return {"id": f"category-{number}", "type": number, "title": "説明",
            "targetTypes": [171], "contents": [{"helpContentId": f"content-{number}",
            "title": "遊び方", "text": "一行\r\n二行\n", "assetIds": []}]}


class HelpTests(unittest.TestCase):
    def test_scope_and_lossless_text(self):
        calls = []
        def fetch(number):
            calls.append(number)
            return [category(number)]
        output = collect_help(fetch)
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(output["content_count"], 3)
        self.assertEqual(output["categories"][2], category(3))

    def test_source_digest_tracks_body(self):
        before = collect_help(lambda n: [category(n)])
        def changed(n):
            row = category(n)
            row["contents"][0]["text"] += "変更"
            return [row]
        self.assertNotEqual(before["source_sha256"], collect_help(changed)["source_sha256"])

    def test_invalid_and_duplicate_sources_fail(self):
        for fetch in (lambda n: [], lambda n: [category(1)],
                      lambda n: [category(n), category(n)]):
            with self.subTest(fetch=fetch), self.assertRaises(ValueError):
                collect_help(fetch)
