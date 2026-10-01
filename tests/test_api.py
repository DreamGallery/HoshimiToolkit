import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import proto.octodb_pb2 as octop
import main
from src import octo_manager, resource_download
from google.protobuf.json_format import MessageToDict


def database(revision: int, names: tuple[str, ...]) -> octop.Database:
    result = octop.Database(revision=revision, urlFormat="https://cdn.example/{o}")
    for name in names:
        result.assetBundleList.add(name=name, objectName=name, size=10)
    return result


class ApiManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ("CACHE", "MANIFEST", "DIFF", "PENDING"):
            value = root if name == "CACHE" else root / f"{name}.json"
            patcher = patch.object(octo_manager, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_full_manifest_pending_until_both_types_complete(self):
        full = database(1, ("one",))
        full.resourceList.add(name="story.txt", objectName="story", size=10)
        with patch.object(octo_manager, "request_update", return_value=b"full") as request:
            with patch.object(octo_manager, "decrypt_database_from_api", return_value=full):
                selected = octo_manager.update_from_api()
        self.assertEqual((len(selected.assetBundleList), len(selected.resourceList)), (1, 1))
        request.assert_called_once_with(0)
        self.assertTrue(octo_manager.PENDING.exists())
        octo_manager.mark_download_complete("resource")
        self.assertEqual(len(octo_manager._pending().assetBundleList), 1)
        octo_manager.mark_download_complete("ab")
        self.assertFalse(octo_manager.PENDING.exists())

    def test_incremental_update_compares_complete_manifests(self):
        first = database(1, ("unchanged", "removed"))
        with patch.object(octo_manager, "request_update", return_value=b"first"):
            with patch.object(octo_manager, "decrypt_database_from_api", return_value=first):
                octo_manager.update_from_api()
        octo_manager.mark_download_complete("ALL")
        diff = database(2, ("new",))
        full = database(2, ("unchanged", "new"))
        full.assetBundleList[0].state = octop.Data.LATEST
        with patch.object(octo_manager, "request_update", side_effect=[b"diff", b"full"]) as request:
            with patch.object(octo_manager, "decrypt_database_from_api", side_effect=[diff, full]):
                selected = octo_manager.update_from_api()
        self.assertEqual([item.name for item in selected.assetBundleList], ["new"])
        self.assertEqual([call.args[0] for call in request.call_args_list], [1, 0])
        saved = json.loads(octo_manager.MANIFEST.read_text())
        self.assertEqual([item["name"] for item in saved["assetBundleList"]], ["unchanged", "new"])

    def test_unfinished_downloads_survive_revisions_while_content_matches(self):
        first = database(1, ("unfinished",))
        with patch.object(octo_manager, "request_update", return_value=b"first"):
            with patch.object(octo_manager, "decrypt_database_from_api", return_value=first):
                octo_manager.update_from_api()
        second_diff = database(2, ("new",))
        second_full = database(2, ("unfinished", "new"))
        with patch.object(octo_manager, "request_update", side_effect=[b"diff", b"full"]):
            with patch.object(octo_manager, "decrypt_database_from_api",
                              side_effect=[second_diff, second_full]):
                selected = octo_manager.update_from_api()
        self.assertEqual([item.name for item in selected.assetBundleList],
                         ["unfinished", "new"])

        third_diff = database(3, ("latest",))
        third_full = database(3, ("new", "latest"))
        with patch.object(octo_manager, "request_update", side_effect=[b"diff", b"full"]):
            with patch.object(octo_manager, "decrypt_database_from_api",
                              side_effect=[third_diff, third_full]):
                selected = octo_manager.update_from_api()
        self.assertEqual([item.name for item in selected.assetBundleList],
                         ["new", "latest"])

    def test_targeted_completion_removes_only_matching_pending_content(self):
        first = database(1, ("done", "later"))
        with patch.object(octo_manager, "request_update", return_value=b"first"):
            with patch.object(octo_manager, "decrypt_database_from_api", return_value=first):
                octo_manager.update_from_api()
        finished = database(1, ("done",))
        octo_manager.mark_download_complete("ab", finished)
        self.assertEqual([item.name for item in octo_manager._pending().assetBundleList],
                         ["later"])
        stale = database(1, ("later",))
        stale.assetBundleList[0].objectName = "old-object"
        octo_manager.mark_download_complete("ab", stale)
        self.assertEqual([item.name for item in octo_manager._pending().assetBundleList],
                         ["later"])

    def test_revision_race_does_not_replace_local_manifest(self):
        first = database(1, ("one",))
        with patch.object(octo_manager, "request_update", return_value=b"first"):
            with patch.object(octo_manager, "decrypt_database_from_api", return_value=first):
                octo_manager.update_from_api()
        before = octo_manager.MANIFEST.read_bytes()
        with patch.object(octo_manager, "request_update", side_effect=[b"diff", b"full"]):
            with patch.object(octo_manager, "decrypt_database_from_api",
                              side_effect=[database(2, ("two",)), database(3, ("three",))]):
                with self.assertRaisesRegex(ValueError, "revision changed"):
                    octo_manager.update_from_api()
        self.assertEqual(octo_manager.MANIFEST.read_bytes(), before)


class DownloadTests(unittest.TestCase):
    def test_url_and_raw_integrity(self):
        raw = b"story data"
        item = octop.Data(name="story.txt", objectName="object", size=len(raw),
                          md5=hashlib.md5(raw).hexdigest(), uploadVersionId=205051,
                          generation=17)
        url = resource_download.object_url("https://cdn/{v}/{type}/{o}?g={g}", item,
                                           "resources")
        self.assertEqual(url, "https://cdn/205051/resources/object?g=17")
        response = type("Response", (), {"content": raw})()
        with patch.object(resource_download, "send_request", return_value=response):
            with patch.object(resource_download, "file_store") as store:
                resource_download.one_task(item, "resources", url, Path("/tmp"))
        store.assert_called_once()
        item.md5 = "0" * 32
        with patch.object(resource_download, "send_request", return_value=response):
            with self.assertRaisesRegex(ValueError, "MD5 mismatch"):
                resource_download.one_task(item, "resources", url, Path("/tmp"))

    def test_download_all_uses_full_manifest_when_revision_is_current(self):
        full = database(7, ("unchanged", "changed"))
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps(MessageToDict(full)), encoding="utf-8")
            args = Namespace(source="api", cache_file="", reset=False,
                             name=[], prefix=[], init_download=False,
                             download_all=True, manifest_only=False,
                             download_type="ab", no_images=True)
            with patch.object(main, "MANIFEST", manifest):
                with patch.object(main, "update_from_api", return_value=None):
                    with patch.object(main, "mark_download_complete"):
                        with patch.object(main, "download_resource", return_value=(0, 0)) as download:
                            main.run_once(args)
        selected = download.call_args.args[0]
        self.assertEqual([item.name for item in selected.assetBundleList],
                         ["unchanged", "changed"])

    def test_download_changed_uses_pending_instead_of_full_manifest(self):
        pending = database(7, ("new",))
        args = Namespace(source="api", cache_file="", reset=False,
                         name=[], prefix=[], init_download=False,
                         download_all=False, download_changed=True,
                         manifest_only=False, download_type="ab", no_images=True)
        with patch.object(main, "update_from_api", return_value=pending):
            with patch.object(main, "download_resource", return_value=(1, 0)) as download:
                with patch.object(main, "file_operate"):
                    with patch.object(main.shutil, "rmtree"):
                        with patch.object(main, "mark_download_complete") as complete:
                            main.run_once(args)
        self.assertEqual([item.name for item in download.call_args.args[0].assetBundleList],
                         ["new"])
        complete.assert_called_once_with("ab", pending)

    def test_download_changed_prefix_selects_pending_and_marks_only_that_subset(self):
        pending = database(7, ("adv_first", "img_other"))
        args = Namespace(source="api", cache_file="", reset=False,
                         name=[], prefix=["adv_"], init_download=False,
                         download_all=False, download_changed=True,
                         manifest_only=False, download_type="ab", no_images=True)
        with patch.object(main, "update_from_api", return_value=pending):
            with patch.object(main, "download_resource", return_value=(0, 0)) as download:
                with patch.object(main, "mark_download_complete") as complete:
                    main.run_once(args)
        selected = download.call_args.args[0]
        self.assertEqual([item.name for item in selected.assetBundleList], ["adv_first"])
        complete.assert_called_once_with("ab", selected)

    def test_verified_local_resource_is_reused_without_network(self):
        raw = b"story data"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "adventure" / "adv_test.txt"
            file.parent.mkdir()
            file.write_bytes(raw)
            selected = octop.Database(revision=7,
                                      urlFormat="https://cdn.example/{o}")
            selected.resourceList.add(name=file.name, objectName="object",
                                      size=len(raw), md5=hashlib.md5(raw).hexdigest())
            with patch.object(resource_download, "RESOURCE_PATH", str(root)):
                with patch.object(resource_download, "one_task") as fetch:
                    self.assertEqual(resource_download.download_resource(
                        selected, "resource", False), (0, 0))
            fetch.assert_not_called()

    def test_verified_staged_resource_survives_a_retry(self):
        raw = b"story data"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "update" / "7" / "resource" / "adventure" / "adv_test.txt"
            staged.parent.mkdir(parents=True)
            staged.write_bytes(raw)
            selected = octop.Database(revision=7,
                                      urlFormat="https://cdn.example/{o}")
            selected.resourceList.add(name=staged.name, objectName="object",
                                      size=len(raw), md5=hashlib.md5(raw).hexdigest())
            with patch.object(resource_download, "RESOURCE_PATH", str(root / "resource")):
                with patch.object(resource_download, "UPDATE_PATH", str(root / "update")):
                    with patch.object(resource_download, "one_task") as fetch:
                        self.assertEqual(resource_download.download_resource(
                            selected, "resource", False), (0, 1))
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
