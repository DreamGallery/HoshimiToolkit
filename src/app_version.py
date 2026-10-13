"""Resolve the Japanese Android client version from Google's public Play listing."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

PACKAGE = "game.qualiarts.idolypride"
PLAY_URL = f"https://play.google.com/store/apps/details?id={PACKAGE}&hl=ja&gl=JP"
MAX_PAGE_BYTES = 4 * 1024 * 1024


class VersionLookupError(ValueError):
    pass


def validate_version(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,5}){1,3}", value):
        raise VersionLookupError("Invalid Android client version; expected a numeric version such as 6.0.3")
    return value


class PlayScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = True
            self.parts = []

    def handle_endtag(self, tag):
        if tag == "script":
            if self.active:
                self.scripts.append("".join(self.parts))
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def parse_play_version(page: str) -> str:
    parser = PlayScripts()
    parser.feed(page)
    versions = set()
    for script in parser.scripts:
        match = re.match(r"\s*AF_initDataCallback\(\{key:\s*['\"]ds:[0-9]+['\"],"
                         r"\s*hash:\s*['\"][^'\"]+['\"],\s*data:\s*", script)
        if not match:
            continue
        # Read JSON only. Never evaluate the surrounding JavaScript or search
        # arbitrary version-like strings in descriptions/recommended apps.
        try:
            data, _ = json.JSONDecoder().raw_decode(script[match.end():])
            detail = data[1][2]
            if detail[77][0] != PACKAGE:
                continue
            versions.add(validate_version(detail[140][0][0][0]))
        except (ValueError, IndexError, KeyError, TypeError):
            continue
    if len(versions) != 1:
        raise VersionLookupError("Google Play did not return one unambiguous client version; check its page structure")
    return versions.pop()


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_play_version() -> str:
    request = Request(PLAY_URL, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ja"})
    opener = build_opener(NoRedirects())
    for attempt in range(3):
        try:
            with opener.open(request, timeout=20) as response:
                if response.headers.get_content_type() != "text/html":
                    raise VersionLookupError("Google Play returned unexpected content instead of HTML")
                data = response.read(MAX_PAGE_BYTES + 1)
            if len(data) > MAX_PAGE_BYTES:
                raise VersionLookupError("Google Play response exceeds the size limit")
            return parse_play_version(data.decode("utf-8"))
        except HTTPError as error:
            status = error.code
            error.close()
            if status not in (429, 500, 502, 503, 504) or attempt == 2:
                raise VersionLookupError(f"Google Play version request failed (HTTP {status})") from None
        except (URLError, TimeoutError, OSError):
            if attempt == 2:
                raise VersionLookupError("Google Play version request failed after three transport attempts") from None
        except UnicodeError:
            raise VersionLookupError("Google Play returned invalid text encoding") from None
        time.sleep(attempt + 1)
    raise VersionLookupError("Google Play version request failed")


def resolve_version(override: str = "auto") -> str:
    value = override.strip()
    return fetch_play_version() if value in ("", "auto") else validate_version(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--override", default="auto", help="Explicit version; empty or auto reads Google Play")
    args = parser.parse_args(argv)
    try:
        print(resolve_version(args.override))
    except VersionLookupError as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
