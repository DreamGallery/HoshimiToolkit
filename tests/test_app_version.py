import json
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src import app_version as module


def listing(version="6.0.3", package=module.PACKAGE):
    detail = [None] * 141
    detail[77] = [package]
    detail[140] = [[[version]]]
    return "<script>AF_initDataCallback({key: 'ds:5', hash: '1', data:" + json.dumps(
        [None, [None, None, detail]]) + ", sideChannel: {}});</script>"


class VersionTests(unittest.TestCase):
    def test_selects_target_app_and_ignores_descriptions_and_recommendations(self):
        page = '<div>99.0.0</div>' + listing("8.0.0", "other.app") + listing()
        self.assertEqual(module.parse_play_version(page), "6.0.3")

    def test_missing_changed_ambiguous_and_non_json_pages_fail(self):
        for page in ("<html>6.0.3</html>", listing(package="other.app"),
                     listing() + listing("6.1.0"), listing("6.0.3\nkey=value"),
                     listing().replace('data:[', 'data:evil([')):
            with self.subTest(page=page[:50]), self.assertRaises(module.VersionLookupError):
                module.parse_play_version(page)

    def test_override_is_explicit_and_never_queries_network(self):
        with patch.object(module, "fetch_play_version", return_value="6.1.0") as fetch:
            self.assertEqual(module.resolve_version("6.0.3"), "6.0.3")
            fetch.assert_not_called()
            self.assertEqual(module.resolve_version("auto"), "6.1.0")
            self.assertEqual(module.resolve_version(""), "6.1.0")
            for value in ("latest", "6.0.3\noutput=value", "6.0.3-beta"):
                with self.assertRaises(module.VersionLookupError): module.resolve_version(value)

    def test_http_forbidden_is_not_retried_and_does_not_echo_server_data(self):
        opener = SimpleNamespace(open=lambda *a, **kw: self.raise_error(
            HTTPError("https://example.invalid/private", 403, "private", {}, BytesIO())))
        with patch.object(module, "build_opener", return_value=opener), patch.object(module.time, "sleep") as sleep:
            with self.assertRaisesRegex(module.VersionLookupError, r"\(HTTP 403\)$"):
                module.fetch_play_version()
            sleep.assert_not_called()

    def test_transport_failures_are_bounded(self):
        with patch.object(module, "build_opener") as build, patch.object(module.time, "sleep"):
            build.return_value.open.side_effect = URLError("private network detail")
            with self.assertRaisesRegex(module.VersionLookupError, "three transport attempts"):
                module.fetch_play_version()
            self.assertEqual(build.return_value.open.call_count, 3)

    @staticmethod
    def raise_error(error):
        raise error
