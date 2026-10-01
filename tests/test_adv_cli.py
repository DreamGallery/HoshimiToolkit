import tempfile
import unittest
from pathlib import Path

from adv_csv import relative_names


class AdventureSelectionTests(unittest.TestCase):
    def test_prefix_selects_only_downloaded_chapter_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("adv_main_01_01_00.txt", "adv_main_01_01_01.txt",
                         "adv_main_01_02_00.txt", "other.txt"):
                (root / name).write_text("", encoding="utf-8")
            self.assertEqual(relative_names(root, [], ["adv_main_01_01_"], ".txt"),
                             [Path("adv_main_01_01_00.txt"),
                              Path("adv_main_01_01_01.txt")])


if __name__ == "__main__":
    unittest.main()
