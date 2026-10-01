"""Collect official notice metadata and HTML without changing game state."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .master_fetch import atomic_json

ROOT = Path(__file__).resolve().parents[1]
HOST = "stat.game-idolypride.jp"
PATH = re.compile(r"/notice/[0-9a-f]{64}/index\.html\Z")
MAX_HTML = 4 * 1024 * 1024
COMMUNITY_INDEX = "https://idoly-backend.outv.im/api/Notice"
FIELDS = ("id", "title", "listTitle", "linkDetail", "linkType", "bannerAssetId", "startTime")
CATEGORIES = ((1, "notices", "noticeHasNext"),
              (2, "malfunctionNotices", "malfunctionNoticeHasNext"),
              (3, "prNotices", "prNoticeHasNext"))


def notice_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Notice address must be a string")
    address = urlsplit(value)
    if address.scheme or address.netloc:
        if address.scheme != "https" or address.netloc != HOST:
            raise ValueError("Only the official HTTPS notice host is accepted")
    # The live index appends a public cache revision, not an account token.
    # Keep only the canonical page path; reject every other query shape.
    if (address.query and not re.fullmatch(r"_v=[0-9]{8}-[0-9]{6}", address.query)) or address.fragment or not PATH.fullmatch(address.path):
        raise ValueError("Invalid official notice path")
    return address.path


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Notice redirect refused")


def fetch_html(path: str) -> str:
    request = Request("https://" + HOST + notice_path(path),
                      headers={"User-Agent": "HoshimiToolkit-notice/1"})
    with build_opener(NoRedirects()).open(request, timeout=30) as response:
        if response.headers.get_content_type() != "text/html":
            raise ValueError("Official notice did not return HTML")
        payload = response.read(MAX_HTML + 1)
        if len(payload) > MAX_HTML:
            raise ValueError("Notice HTML exceeds size limit")
        return payload.decode(response.headers.get_content_charset() or "utf-8")


class NoticeHTML(HTMLParser):
    """Extract text nodes, preserving inline-node boundaries used by WebView."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    EXCLUDED = {"script", "style", "noscript", "textarea", "input"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.texts = []
        self.title = []
        self.headline = None

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if "title" in self.stack:
            self.title.append(text)
        if "body" not in self.stack or self.EXCLUDED.intersection(self.stack):
            return
        if text not in self.texts:
            self.texts.append(text)
        if self.headline is None and self.stack[-1:] == ["h1"]:
            self.headline = text


def parse_html(html: str) -> dict:
    parser = NoticeHTML()
    parser.feed(html)
    parser.close()
    if not parser.texts:
        raise ValueError("Notice HTML contains no body text")
    return {"title": "".join(parser.title), "headline": parser.headline, "texts": parser.texts}


def sanitize_notices(records: list[dict]) -> list[dict]:
    """Keep static notice metadata only; omit commonResponse and associate tokens."""
    result = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Invalid notice record")
        clean = {}
        for key in FIELDS:
            value = record.get(key)
            if value is not None:
                if not isinstance(value, (str, int)) or isinstance(value, bool):
                    raise ValueError("Invalid notice metadata value")
                clean[key] = value
        if clean:
            result.append(clean)
    return result


def input_paths(data: dict) -> list[str]:
    if not isinstance(data, dict):
        raise ValueError("Input must be a JSON object")
    values = []
    if "pages" in data:
        if not isinstance(data["pages"], dict):
            raise ValueError("pages must be an object")
        values.extend(data["pages"])
    for _, field, _ in CATEGORIES:
        records = data.get(field, [])
        if not isinstance(records, list):
            raise ValueError("Notice lists must be arrays")
        for record in sanitize_notices(records):
            link = record.get("linkDetail", "")
            # Non-WebView notices can legitimately refer to game navigation.
            if isinstance(link, str) and (link.startswith("https://" + HOST + "/") or link.startswith("/notice/")):
                values.append(link)
    if "paths" in data:
        if not isinstance(data["paths"], list):
            raise ValueError("paths must be an array")
        values.extend(data["paths"])
    return list(dict.fromkeys(notice_path(value) for value in values))


def collect_pages(data: dict, fetch=fetch_html) -> dict:
    paths = input_paths(data)
    if not paths:
        raise ValueError("No official notice URLs found in input")
    return {"pages": {path: parse_html(fetch(path)) for path in paths}}


def fetch_community_category(category: str, limit: int) -> list[dict]:
    if category not in {field for _, field, _ in CATEGORIES}:
        raise ValueError("Unknown notice category")
    request = Request(COMMUNITY_INDEX + "?" + urlencode({"limit": limit, "type": category}),
                      headers={"User-Agent": "HoshimiToolkit-notice/1"})
    with build_opener(NoRedirects()).open(request, timeout=30) as response:
        if response.headers.get_content_type() != "application/json":
            raise ValueError("Community notice index did not return JSON")
        payload = response.read(MAX_HTML + 1)
        if len(payload) > MAX_HTML:
            raise ValueError("Community index exceeds size limit")
        result = json.loads(payload)
    if not isinstance(result, list):
        raise ValueError("Community notice category must be an array")
    return result


def collect_community_index(limit: int = 100, fetch=fetch_community_category) -> dict:
    """Anonymous discovery from INFO PRIDE; its cached index is not exhaustive."""
    if not 1 <= limit <= 500:
        raise ValueError("Community index limit must be between 1 and 500")
    output = {field: sanitize_notices(fetch(field, limit)) for _, field, _ in CATEGORIES}
    output["_provenance"] = {
        "source": COMMUNITY_INDEX,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "requested_limit_per_category": limit,
        "returned_per_category": {field: len(output[field]) for _, field, _ in CATEGORIES},
        "completeness": "third_party_snapshot_not_guaranteed_exhaustive",
    }
    return output


def collect_entry_titles(index: dict) -> dict:
    """Preserve list headings separately from the HTML headline and body."""
    if not isinstance(index, dict):
        raise ValueError("Notice index must be an object")
    entries = []
    identities = set()
    for _, category, _ in CATEGORIES:
        records = index.get(category, [])
        if not isinstance(records, list):
            raise ValueError("Notice category must be an array")
        for record in sanitize_notices(records):
            identity = record.get("id")
            if not isinstance(identity, str) or not identity:
                raise ValueError("Notice entry requires a stable string ID")
            if (category, identity) in identities:
                raise ValueError("Duplicate notice entry ID in category")
            identities.add((category, identity))
            entry = {"id": identity, "category": category}
            for field in ("title", "listTitle", "linkDetail"):
                if field in record:
                    if not isinstance(record[field], str):
                        raise ValueError("Notice entry text must be a string")
                    entry[field] = record[field]
            payload = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            entry["source_sha256"] = hashlib.sha256(payload).hexdigest()
            entry["field_sha256"] = {field: hashlib.sha256(entry[field].encode("utf-8")).hexdigest()
                                     for field in ("title", "listTitle") if field in entry}
            entries.append(entry)
    return {"version": 1, "scope": "notice_entry_titles_separate_from_html",
            "index_sha256": hashlib.sha256(json.dumps(index, ensure_ascii=False, sort_keys=True,
                                                      separators=(",", ":")).encode("utf-8")).hexdigest(),
            "entries": entries}


def paginate_notices(first: dict, fetch_page, max_pages=100) -> dict:
    output = {}
    for category, field, flag in CATEGORIES:
        records = sanitize_notices(first.get(field, []))
        more = first.get(flag, False)
        seen = {record.get("id") or json.dumps(record, sort_keys=True) for record in records}
        for _ in range(max_pages):
            if not more:
                break
            page = fetch_page(category, len(records))
            batch = sanitize_notices(page.get("notices", []))
            identities = {record.get("id") or json.dumps(record, sort_keys=True) for record in batch}
            if not batch or len(identities) != len(batch) or identities.intersection(seen):
                raise ValueError("Notice pagination made no progress or repeated records")
            records.extend(batch)
            seen.update(identities)
            more = page.get("hasNext", False)
        if more:
            raise ValueError("Notice pagination exceeded configured page limit")
        output[field] = records
    return output


def select_notice_scope(first: dict, fetch_page, max_pages: int = 1000, *, archive: bool = False) -> dict:
    """Default to Notice.List's first batches, never infer expiry from dates."""
    result = (paginate_notices(first, fetch_page, max_pages) if archive else
              {field: sanitize_notices(first.get(field, [])) for _, field, _ in CATEGORIES})
    result["_pagination"] = {"scope": "archive" if archive else "current_window",
        "first_page_has_next": {field: bool(first.get(flag, False)) for _, field, flag in CATEGORIES},
        "exhaustive": archive}
    return result


def load_session(path: Path) -> dict:
    # Session secrets always stay under ignored cache, never in CLI arguments.
    if not path.resolve().is_relative_to((ROOT / "cache").resolve()):
        raise ValueError("Session JSON must reside inside HoshimiToolkit/cache")
    data = json.loads(path.read_text(encoding="utf-8"))
    metadata = data.get("metadata")
    allowed = {"content-type", "x-app-version", "x-seed-id", "x-auth-token", "x-master-version", "x-platform", "x-device-id", "x-device-type", "x-os-version", "x-language", "x-app-id", "x-device", "x-ad-id", "x-adjust-adid", "x-ise", "x-isda", "x-isop", "x-isr", "x-s", "grpc-timeout"}
    if not isinstance(metadata, dict) or not metadata or any(
            key not in allowed or not isinstance(value, str) or "\n" in value or "\r" in value
            for key, value in metadata.items()):
        raise ValueError("Invalid session metadata")
    if not metadata.get("x-app-version"):
        raise ValueError("Session requires x-app-version")
    return metadata


def fetch_index(solis_dir: Path, session: Path, max_pages: int, *, archive: bool = False) -> dict:
    metadata = load_session(session)
    sys.path.insert(0, str(solis_dir.resolve()))
    grpc = importlib.import_module("grpc")
    api = importlib.import_module("papi_pb2")
    stubs = importlib.import_module("papi_grpc")
    from google.protobuf.empty_pb2 import Empty
    from google.protobuf.json_format import MessageToDict
    roots = (solis_dir / "iprroots.pem").read_bytes()
    channel = grpc.secure_channel("api.game-idolypride.jp:443",
                                  grpc.ssl_channel_credentials(root_certificates=roots),
                                  options=[("grpc.primary_user_agent", "grpc-csharp/2.37.0-dev grpc-c/15.0.0 (android; chttp2)")])
    def call(method, request):
        response, context = method.with_call(request, metadata=list(metadata.items()), timeout=30)
        for key, value in [*(context.initial_metadata() or []), *(context.trailing_metadata() or [])]:
            if key == "x-error-code" and str(value) not in ("", "0"):
                raise RuntimeError("Game rejected notice request (session or protocol may be stale)")
        return MessageToDict(response, preserving_proto_field_name=True)
    try:
        stub = stubs.NoticeStub(channel)
        return select_notice_scope(call(stub.List, Empty()),
                                lambda category, offset: call(stub.FetchList, api.NoticeFetchRequest(
                                    noticeCategoryType=category, offset=offset)), max_pages, archive=archive)
    finally:
        channel.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pages = commands.add_parser("pages", help="Fetch official HTML from source JSON or a Notice.List export")
    pages.add_argument("--input", type=Path, required=True)
    pages.add_argument("--output", type=Path, default=ROOT / "cache/notice/source.json")
    index = commands.add_parser("index", help="Read Notice.List/FetchList with existing local session metadata")
    index.add_argument("--solis-dir", type=Path, required=True)
    index.add_argument("--session", type=Path, default=ROOT / "cache/notice/session.json")
    index.add_argument("--max-pages", type=int, default=1000)
    index.add_argument("--archive", action="store_true", help="Explicitly include FetchList history beyond the first List batches")
    index.add_argument("--output", type=Path)
    community = commands.add_parser("community-index", help="Discover current notice URLs anonymously through INFO PRIDE")
    community.add_argument("--limit", type=int, default=100)
    community.add_argument("--output", type=Path, default=ROOT / "cache/notice/community-index.json")
    titles = commands.add_parser("entry-titles", help="Export title/listTitle independently of notice HTML")
    titles.add_argument("--input", type=Path, required=True)
    titles.add_argument("--output", type=Path, default=ROOT / "cache/notice/entry-titles.json")
    official = commands.add_parser("official-index", help="Fetch the current List window with a dedicated cached collector account")
    official.add_argument("--solis-dir", type=Path, required=True)
    official.add_argument("--app-version", required=True)
    official.add_argument("--account", type=Path, default=ROOT / "cache/notice/collector-account.json")
    official.add_argument("--create-account", action="store_true", help="Explicitly allow one-time dedicated guest account creation")
    official.add_argument("--initialize-day", action="store_true", help="Allow dedicated collector Home.Login on DateChanged; may grant its automatic login rewards")
    official.add_argument("--max-pages", type=int, default=1000)
    official.add_argument("--archive", action="store_true", help="Explicitly include all FetchList history, not only the current window")
    official.add_argument("--output", type=Path)
    archive = commands.add_parser("archive-pages", help="Download official notice history with bounded workers and per-page resume cache")
    archive.add_argument("--input", type=Path, required=True)
    archive.add_argument("--cache-dir", type=Path, default=ROOT / "cache/notice/page-cache")
    archive.add_argument("--output", type=Path, default=ROOT / "cache/notice/archive-source.json")
    archive.add_argument("--report", type=Path, default=ROOT / "cache/notice/archive-report.json")
    archive.add_argument("--workers", type=int, default=4)
    archive.add_argument("--refresh", action="store_true", help="Re-fetch cached pages to check upstream edits")
    args = parser.parse_args(argv)
    try:
        if args.command == "archive-pages":
            from .notice_archive import archive_pages
            summary = archive_pages(json.loads(args.input.read_text(encoding="utf-8")),
                args.cache_dir, args.output, args.report, workers=args.workers, refresh=args.refresh,
                progress=lambda counts: print(json.dumps(counts, sort_keys=True), flush=True))
            print(f"Saved {summary['downloaded'] + summary['cached']} pages; {summary['failed']} failed; report: {args.report}")
            return 2 if summary["failed"] else 0
        elif args.command == "pages":
            result = collect_pages(json.loads(args.input.read_text(encoding="utf-8")))
            count = len(result["pages"])
        elif args.command == "community-index":
            result = collect_community_index(args.limit)
            count = sum(len(result[field]) for _, field, _ in CATEGORIES)
        elif args.command == "entry-titles":
            result = collect_entry_titles(json.loads(args.input.read_text(encoding="utf-8")))
            count = len(result["entries"])
        elif args.command == "official-index":
            from .notice_official import fetch_official_index
            result = fetch_official_index(args.solis_dir, args.app_version, args.account,
                                          args.create_account, args.max_pages, args.initialize_day, archive=args.archive)
            if args.output is None:
                args.output = ROOT / ("cache/notice/archive-index.json" if args.archive else "cache/notice/official-index.json")
            count = sum(len(result[field]) for _, field, _ in CATEGORIES)
        else:
            if args.max_pages < 1:
                raise ValueError("max-pages must be positive")
            result = fetch_index(args.solis_dir, args.session, args.max_pages, archive=args.archive)
            if args.output is None:
                args.output = ROOT / ("cache/notice/session-archive-index.json" if args.archive else "cache/notice/index.json")
            count = sum(len(result[field]) for _, field, _ in CATEGORIES)
        atomic_json(args.output, result)
        print(f"Saved {count} {args.command} entries to {args.output}")
        return 0
    except Exception as error:
        # Network exceptions can embed request metadata: don't print their values.
        if args.command == "official-index":
            from .notice_official import CollectorError
            if isinstance(error, CollectorError):
                print("Notice collection stopped: " + str(error), file=sys.stderr)
                return 1
        print(f"Notice collection failed ({type(error).__name__}); check input, session and network", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
