from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.file_operation import classified_path, file_operate


class FileOperationTests(unittest.TestCase):
    def test_asset_subprefixes_route_video_without_moving_other_effects(self):
        root = Path("cache/asset")
        expected = {
            "adv_campaign_2023_0401-03": "movie",
            "eff_cav_mov_hsm-004": "movie",
            "eff_ps_adv_schoolclass-d": "effect",
            "m_sky_night-002": "sky",
            "mot_adv_chr_cmn_talk-024_pose": "animation/motion",
            "scl_photo_studio-05-000": "scene",
            "tln_live_tri-003-small": "timeline/live",
        }
        for name, category in expected.items():
            with self.subTest(name=name):
                self.assertEqual(classified_path(name, root, "assetbundle"),
                                 root / category / name)
        self.assertEqual(
            classified_path("adv_main_01_01_01.txt", "cache/resource", "resources"),
            Path("cache/resource/adventure/adv_main_01_01_01.txt"),
        )

    def test_failed_copy_preserves_previous_resource_for_retry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cache/update/1052"
            destination = root / "cache"
            staged = source / "resource/adventure/adv_test.txt"
            current = destination / "resource/adventure/adv_test.txt"
            staged.parent.mkdir(parents=True)
            current.parent.mkdir(parents=True)
            staged.write_bytes(b"new revision")
            current.write_bytes(b"old revision")

            with patch("src.file_operation.shutil.copyfileobj",
                       side_effect=OSError("copy failed")):
                with self.assertRaisesRegex(OSError, "copy failed"):
                    file_operate("copy", str(source), str(destination), dirs_exist_ok=True)
            self.assertEqual(current.read_bytes(), b"old revision")
            self.assertEqual(staged.read_bytes(), b"new revision")
            self.assertEqual(list(current.parent.glob(".octo-copy-*")), [])

            file_operate("copy", str(source), str(destination), dirs_exist_ok=True)
            self.assertEqual(current.read_bytes(), b"new revision")
            self.assertEqual(staged.read_bytes(), b"new revision")

    def test_failed_move_preserves_previous_resource_for_retry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cache/update/1052"
            destination = root / "cache"
            staged = source / "resource/adventure/adv_test.txt"
            current = destination / "resource/adventure/adv_test.txt"
            staged.parent.mkdir(parents=True)
            current.parent.mkdir(parents=True)
            staged.write_bytes(b"new revision")
            current.write_bytes(b"old revision")

            with patch("src.file_operation.os.replace", side_effect=OSError("move failed")):
                with self.assertRaisesRegex(OSError, "move failed"):
                    file_operate("move", str(source), str(destination))
            self.assertEqual(current.read_bytes(), b"old revision")
            self.assertEqual(staged.read_bytes(), b"new revision")

            file_operate("move", str(source), str(destination))
            self.assertEqual(current.read_bytes(), b"new revision")
            self.assertFalse(staged.exists())


if __name__ == "__main__":
    unittest.main()
