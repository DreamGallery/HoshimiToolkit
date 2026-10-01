import csv
import tempfile
import unittest
from pathlib import Path

from src.adv_csv import coverage_file, export_file, fields, merge_file, rebase_file, save_patch


SCRIPT = ("[title title=Episode 1]\r\n"
          "[message text=こんにちは name=アイドル clip=\\{\"time\":1\\}]\r\n"
          "[choicegroup choices=[choice text=はい] choices=[choice text=いいえ]]\r\n"
          "[narration text=夜が明けた]\r\n"
          "[message text=誰もいない name=]\r\n"
          "[timeline]")


class AdventureCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "adv_test.txt"
        self.csv = Path(self.temp.name) / "adv_test.csv"
        self.output = Path(self.temp.name) / "output.txt"
        self.source.write_bytes(SCRIPT.encode("utf-8"))

    def edit(self, changes: dict[str, str]):
        with self.csv.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            row["trans"] = changes.get(row["id"], row["trans"])
        with self.csv.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["id", "name", "text", "trans"],
                                    lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def test_extract_and_noop_roundtrip_is_byte_identical(self):
        self.assertEqual(export_file(self.source, self.csv), 6)
        with self.csv.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["id"] for row in rows[:-2]],
                         ["1:title:1", "2:text:1", "3:choice:1", "3:choice:2",
                          "4:narration:1", "5:text:1"])
        self.assertFalse(any(":name:" in row["id"] for row in rows))
        self.assertEqual(merge_file(self.source, self.csv, self.output), 0)
        self.assertEqual(self.output.read_bytes(), self.source.read_bytes())

    def test_translated_fields_leave_commands_and_choices_intact(self):
        export_file(self.source, self.csv)
        self.edit({"2:text:1": "你好", "3:choice:2": "不=是"})
        self.assertEqual(merge_file(self.source, self.csv, self.output,
                                    {"アイドル": "偶像"}), 3)
        merged = self.output.read_bytes().decode("utf-8")
        self.assertIn("[message text=你好 name=偶像 clip=", merged)
        self.assertIn("[choicegroup choices=[choice text=はい] choices=[choice text=不\\=是]]", merged)
        self.assertTrue(merged.endswith("[timeline]"))
        self.assertIn("\r\n", merged)

    def test_changed_source_is_rejected(self):
        export_file(self.source, self.csv)
        self.source.write_bytes(SCRIPT.replace("こんにちは", "こんばんは").encode("utf-8"))
        with self.assertRaisesRegex(ValueError, "different script"):
            merge_file(self.source, self.csv, self.output)

    def test_missing_csv_cell_is_rejected_before_writing_outputs(self):
        export_file(self.source, self.csv)
        lines = self.csv.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].rsplit(",", 1)[0]
        self.csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.output.write_text("existing output", encoding="utf-8")
        for command in (merge_file, save_patch, rebase_file):
            with self.subTest(command=command.__name__):
                with self.assertRaisesRegex(ValueError, "missing or extra cells"):
                    command(self.source, self.csv, self.output)
                self.assertEqual(self.output.read_text(encoding="utf-8"),
                                 "existing output")

    def test_unescaped_bracket_is_rejected(self):
        export_file(self.source, self.csv)
        self.edit({"2:text:1": "错误]"})
        with self.assertRaisesRegex(ValueError, "Unsafe script"):
            merge_file(self.source, self.csv, self.output)
        self.edit({"2:text:1": "错误["})
        with self.assertRaisesRegex(ValueError, "Unsafe script"):
            merge_file(self.source, self.csv, self.output)

    def test_named_placeholder_cannot_be_lost(self):
        self.source.write_text("[message text={user}くん、こんにちは name=アイドル]\n",
                               encoding="utf-8")
        export_file(self.source, self.csv)
        self.edit({"1:text:1": "你好"})
        with self.assertRaisesRegex(ValueError, "Placeholders differ"):
            save_patch(self.source, self.csv, Path(self.temp.name) / "patch.json")
        with self.assertRaisesRegex(ValueError, "Placeholders differ"):
            merge_file(self.source, self.csv, self.output)
        self.edit({"1:text:1": "{user}，你好"})
        self.assertEqual(merge_file(self.source, self.csv, self.output), 1)

    def test_visible_line_breaks_must_be_preserved(self):
        self.source.write_text(r"[message text=一行\n二行 name=アイドル]" + "\n",
                               encoding="utf-8")
        export_file(self.source, self.csv)
        self.edit({"1:text:1": "第一行第二行"})
        with self.assertRaisesRegex(ValueError, "Visible line breaks"):
            merge_file(self.source, self.csv, self.output)
        self.edit({"1:text:1": r"第一行\n第二行"})
        self.assertEqual(merge_file(self.source, self.csv, self.output), 1)

    def test_sparse_patch_recreates_translated_csv_without_original_text(self):
        import json

        patch = Path(self.temp.name) / "adv_test.json"
        export_file(self.source, self.csv)
        self.edit({"2:text:1": "你好", "3:choice:2": "不是"})
        self.assertEqual(save_patch(self.source, self.csv, patch), 2)
        self.assertNotIn("こんにちは", patch.read_text(encoding="utf-8"))
        self.assertEqual(len(json.loads(patch.read_text(encoding="utf-8"))["translations"]), 2)
        export_file(self.source, self.csv, patch)
        self.assertEqual(merge_file(self.source, self.csv, self.output), 2)
        self.assertIn("text=你好", self.output.read_text(encoding="utf-8"))

        self.source.write_bytes(SCRIPT.replace("こんにちは", "こんばんは").encode("utf-8"))
        with self.assertRaisesRegex(ValueError, "different script"):
            export_file(self.source, self.csv, patch)

    def test_name_glossary_applies_only_when_merging_txt(self):
        import json

        glossary = {"アイドル": "偶像"}
        patch = Path(self.temp.name) / "adv_test.json"
        export_file(self.source, self.csv)
        with self.csv.open(encoding="utf-8", newline="") as stream:
            names = [row for row in csv.DictReader(stream) if ":name:" in row["id"]]
        self.assertEqual(names, [])
        self.assertEqual(save_patch(self.source, self.csv, patch), 0)
        self.assertEqual(json.loads(patch.read_text(encoding="utf-8"))["translations"], {})
        self.assertEqual(merge_file(self.source, self.csv, self.output, glossary), 1)
        self.assertIn("name=偶像", self.output.read_text(encoding="utf-8"))
        self.assertEqual(merge_file(self.source, self.csv, self.output,
                                    {"アイドル": "明星"}), 1)
        self.assertIn("name=明星", self.output.read_text(encoding="utf-8"))

    def test_name_glossary_fills_only_exact_standalone_text(self):
        self.source.write_text("[message text={user}さん name=アイドル]\n"
                               "[message text={user}さん、おはよう name=アイドル]\n",
                               encoding="utf-8")
        export_file(self.source, self.csv)
        glossary = {"{user}さん": "{user}先生", "アイドル": "偶像"}
        coverage = coverage_file(self.source, None, glossary)
        self.assertEqual((coverage.text_translated, coverage.text_total), (1, 2))
        self.assertEqual((coverage.names_translated, coverage.names_total), (2, 2))
        self.assertEqual(merge_file(self.source, self.csv, self.output, glossary), 3)
        merged = self.output.read_text(encoding="utf-8")
        self.assertIn("text={user}先生 name=偶像", merged)
        self.assertIn("text={user}さん、おはよう name=偶像", merged)

    def test_user_honorific_in_translated_line_comes_from_glossary(self):
        self.source.write_text("[message text={user}さん\\n今日はどう？ name=アイドル]\n",
                               encoding="utf-8")
        export_file(self.source, self.csv)
        self.edit({"1:text:1": "{user}さん\\n今天怎么样？"})
        self.assertEqual(merge_file(self.source, self.csv, self.output,
                                    {"{user}さん": "{user}先生"}), 1)
        self.assertIn("text={user}先生\\n今天怎么样？",
                      self.output.read_text(encoding="utf-8"))
        with self.csv.open(encoding="utf-8", newline="") as stream:
            self.assertEqual(next(csv.DictReader(stream))["trans"],
                             "{user}さん\\n今天怎么样？")

    def test_coverage_counts_manual_text_and_shared_names_separately(self):
        patch = Path(self.temp.name) / "adv_test.json"
        glossary = {"アイドル": "偶像"}
        export_file(self.source, self.csv)
        self.edit({"2:text:1": "你好"})
        save_patch(self.source, self.csv, patch, glossary)
        coverage = coverage_file(self.source, patch, glossary)
        self.assertEqual((coverage.text_translated, coverage.text_total), (1, 6))
        self.assertEqual((coverage.names_translated, coverage.names_total), (1, 1))

    def test_semantic_ids_and_legacy_ids_round_trip_through_patch(self):
        import json

        patch = Path(self.temp.name) / "adv_test.json"
        export_file(self.source, self.csv)
        self.edit({"3:choice:1": "好的", "4:narration:1": "天亮了"})
        self.assertEqual(save_patch(self.source, self.csv, patch), 2)
        translations = json.loads(patch.read_text(encoding="utf-8"))["translations"]
        self.assertEqual(translations, {"3:text:1": "好的", "4:text:1": "天亮了"})
        export_file(self.source, self.csv, patch)
        self.assertEqual(merge_file(self.source, self.csv, self.output), 2)
        self.assertIn("[narration text=天亮了]", self.output.read_text(encoding="utf-8"))

        # Previously exported CSVs keep working without manual ID migration.
        with self.csv.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            row["id"] = row["id"].replace(":choice:", ":text:").replace(
                ":narration:", ":text:")
        with self.csv.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["id", "name", "text", "trans"],
                                    lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        self.assertEqual(merge_file(self.source, self.csv, self.output), 2)
        self.assertEqual(save_patch(self.source, self.csv, patch), 2)

    def test_rebase_preserves_unique_text_after_line_shift(self):
        export_file(self.source, self.csv)
        self.edit({"2:text:1": "你好"})
        self.source.write_bytes(("[timeline]\r\n" + SCRIPT).encode("utf-8"))
        rebased = Path(self.temp.name) / "rebased.csv"
        preserved, lost = rebase_file(self.source, self.csv, rebased)
        self.assertEqual(lost, [])
        self.assertGreaterEqual(preserved, 1)
        merge_file(self.source, rebased, self.output)
        self.assertIn("text=你好", self.output.read_text(encoding="utf-8"))

    def test_rebase_legacy_choice_id_after_line_shift(self):
        export_file(self.source, self.csv)
        self.edit({"3:choice:1": "好的"})
        with self.csv.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[2]["id"] = "3:text:1"
        with self.csv.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["id", "name", "text", "trans"],
                                    lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        self.source.write_bytes(("[timeline]\r\n" + SCRIPT).encode("utf-8"))
        rebased = Path(self.temp.name) / "rebased.csv"
        preserved, lost = rebase_file(self.source, self.csv, rebased)
        self.assertEqual((preserved, lost), (1, []))
        with rebased.open(encoding="utf-8", newline="") as stream:
            rebased_rows = list(csv.DictReader(stream))
        self.assertEqual(rebased_rows[2]["id"], "4:choice:1")
        self.assertEqual(rebased_rows[2]["trans"], "好的")

    def test_rebase_does_not_change_semantic_type(self):
        self.source.write_text("[choice text=はい]\n", encoding="utf-8")
        export_file(self.source, self.csv)
        self.edit({"1:choice:1": "好的"})
        self.source.write_text("[narration text=はい]\n", encoding="utf-8")
        rebased = Path(self.temp.name) / "rebased.csv"
        preserved, lost = rebase_file(self.source, self.csv, rebased)
        self.assertEqual(preserved, 0)
        self.assertEqual([item["translation"] for item in lost], ["好的"])

    def test_rebase_reports_changed_original_without_reusing_translation(self):
        export_file(self.source, self.csv)
        self.edit({"2:text:1": "你好"})
        self.source.write_bytes(SCRIPT.replace("こんにちは", "こんばんは").encode("utf-8"))
        rebased = Path(self.temp.name) / "rebased.csv"
        _, lost = rebase_file(self.source, self.csv, rebased)
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["translation"], "你好")
        with rebased.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(next(row for row in rows if row["text"] == "こんばんは")["trans"], "")

    def test_rebase_does_not_move_title_translation_into_dialogue(self):
        self.source.write_text("[title title=同じ]\n"
                               "[message text=同じ name=アイドル]\n", encoding="utf-8")
        export_file(self.source, self.csv)
        self.edit({"1:title:1": "相同标题"})
        self.source.write_text("[message text=同じ name=アイドル]\n", encoding="utf-8")
        rebased = Path(self.temp.name) / "rebased.csv"
        _, lost = rebase_file(self.source, self.csv, rebased)
        self.assertEqual([item["translation"] for item in lost], ["相同标题"])
        with rebased.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["trans"], "")


if __name__ == "__main__":
    unittest.main()
