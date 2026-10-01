import unittest
import hashlib
from src.rule_fetch import apply_runtime_fallback, collect_rules, split_segments


class RuleTests(unittest.TestCase):
    def test_roundtrip(self):
        for source in ("", "\n", "a\r\nb\nc\r\n", "日本語\rEnglish", "\n\n", "text"):
            with self.subTest(source=source):
                self.assertEqual("".join(s["source"] + s["line_ending"] for s in split_segments(source)), source)

    def test_all_categories_keep_empty_and_license(self):
        output = collect_rules(lambda n: "" if n == 4 else "text\n")
        self.assertEqual(len(output["rules"]), 7)
        self.assertEqual(output["rules"]["4"]["source"], "")
        self.assertFalse(output["rules"]["6"]["translate"])

    def test_runtime_body_fills_only_empty_response_with_provenance(self):
        source = "特定商取引法\r\n■ 販売業者\nQualiArts"
        fallback = {"schema_version": 1, "rules": {"4": {"source": source,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "provenance": {"kind": "game_runtime_capture"}}}}
        original = collect_rules(lambda n: "" if n == 4 else "official")
        merged = apply_runtime_fallback(original, fallback)
        self.assertEqual(merged["rules"]["4"]["source"], source)
        self.assertEqual(original["rules"]["4"]["source"], "")
        self.assertEqual("".join(s["source"] + s["line_ending"] for s in
                         merged["rules"]["4"]["segments"]), source)
        fresh = collect_rules(lambda n: "new official")
        self.assertEqual(apply_runtime_fallback(fresh, fallback), fresh)
        fallback["rules"]["4"]["source"] += "changed"
        with self.assertRaises(ValueError):
            apply_runtime_fallback(original, fallback)
