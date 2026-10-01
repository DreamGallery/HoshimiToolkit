import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from src.master_fetch import fetch_packs, firebase_api_key, token_from_xml


class MasterFetchTests(unittest.TestCase):
    def test_firebase_key_follows_environment_cache_ini_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "firebase-settings.json"
            settings.write_text('{"api_key": "cached-key"}', encoding="utf-8")
            ini = root / "config.ini"
            ini.write_text('[Firebase settings]\nAPI_KEY = ini-key\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(firebase_api_key(settings, ini), "cached-key")
                with patch.dict(os.environ, {"IDOLY_FIREBASE_API_KEY": " env-key "}):
                    self.assertEqual(firebase_api_key(settings, ini), "env-key")
                settings.unlink()
                self.assertEqual(firebase_api_key(settings, ini), "ini-key")
                ini.write_text('[Firebase settings]\nAPI_KEY =\n', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Firebase API key is missing"):
                    firebase_api_key(settings, ini)

    def test_expired_token_uses_configured_key(self):
        with tempfile.TemporaryDirectory() as directory:
            auth_xml = Path(directory) / "firebase-auth.xml"
            record = {"issued_at": 0, "expires_in": "1", "access_token": "old",
                      "refresh_token": "refresh"}
            auth_xml.write_text('<root><string name="GET_TOKEN_RESPONSE">' +
                                json.dumps(record) + '</string></root>', encoding="utf-8")
            calls = []

            def post(url, **kwargs):
                calls.append((url, kwargs))
                return SimpleNamespace(ok=True, json=lambda: {"id_token": "new"})

            with patch("src.master_fetch.firebase_api_key", return_value="configured-key"):
                result = token_from_xml(auth_xml, SimpleNamespace(post=post),
                                        {"firebase": {"Content-Type": "application/json"}})
            self.assertEqual(result, "new")
            self.assertEqual(calls[0][0], "https://securetoken.googleapis.com/v1/token")
            self.assertEqual(calls[0][1]["params"], {"key": "configured-key"})

    def test_invalid_table_name_is_rejected_before_creating_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw"
            bad_pack = SimpleNamespace(type="../outside", fileName="escape.db",
                                       fileSize=5, cryptoKey="key",
                                       downloadUrl="https://example.test/escape")
            tag = SimpleNamespace(version="1", masterTagPacks=[bad_pack])
            with self.assertRaisesRegex(ValueError, "Invalid MasterDB table name"):
                fetch_packs(tag, output, None, workers=1)
            self.assertFalse(output.exists())

    def test_cache_requires_verified_file_and_current_master_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            pack = SimpleNamespace(type="Story", fileName="Story.db", fileSize=5,
                                   cryptoKey="key", downloadUrl="https://example.test/story")
            calls = []

            def decoder(item, target, _modules, reuse):
                calls.append(reuse)
                if not reuse:
                    (target / "Story.json").write_text(json.dumps([
                        {"id": "1", "name": "物語"}
                    ], ensure_ascii=False), encoding="utf-8")
                return item.type, "cached" if reuse else 1

            def tag(version):
                return SimpleNamespace(version=version, masterTagPacks=[pack],
                                       SerializeToString=lambda: version.encode())

            (output / "Story.json").write_text("[]", encoding="utf-8")
            self.assertEqual(fetch_packs(tag("1"), output, None, workers=1,
                                         decoder=decoder), (1, 0))
            self.assertEqual(fetch_packs(tag("1"), output, None, workers=1,
                                         decoder=decoder), (1, 1))
            (output / "Story.json").write_text("[]", encoding="utf-8")
            self.assertEqual(fetch_packs(tag("1"), output, None, workers=1,
                                         decoder=decoder), (1, 0))
            self.assertEqual(fetch_packs(tag("2"), output, None, workers=1,
                                         decoder=decoder), (1, 0))
            self.assertEqual(calls, [False, True, False, False])


if __name__ == "__main__":
    unittest.main()
