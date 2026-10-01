"""Collect public help text through the anonymous official Master API."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys

from .master_fetch import atomic_json

ROOT = Path(__file__).resolve().parents[1]
HELP_TYPES = (1, 2, 3)


def collect_help(fetch) -> dict:
    categories = []
    ids = set()
    content_ids = set()
    counts = {}
    for help_type in HELP_TYPES:
        records = fetch(help_type)
        if not isinstance(records, list):
            raise ValueError("Help categories must be an array")
        counts[str(help_type)] = len(records)
        for record in records:
            if (not isinstance(record, dict) or not isinstance(record.get("id"), str)
                    or not record["id"] or record["id"] in ids
                    or record.get("type") != help_type
                    or not isinstance(record.get("title"), str)
                    or not isinstance(record.get("contents"), list)):
                raise ValueError("Invalid or duplicate help category")
            ids.add(record["id"])
            for content in record["contents"]:
                if (not isinstance(content, dict)
                        or not isinstance(content.get("helpContentId"), str)
                        or not content["helpContentId"]
                        or content["helpContentId"] in content_ids
                        or not isinstance(content.get("title"), str)
                        or not isinstance(content.get("text"), str)):
                    raise ValueError("Invalid or duplicate help content")
                content_ids.add(content["helpContentId"])
            categories.append(record)
    if not categories:
        raise ValueError("Official help API returned no categories")
    payload = json.dumps(categories, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"schema_version": 1,
            "source_api": "https://api.game-idolypride.jp/api.Master/GetHelpCategory",
            "access": "anonymous", "help_types": list(HELP_TYPES),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "category_counts": counts, "content_count": len(content_ids),
            "categories": categories}


def fetch_help(solis_dir: Path, app_version: str) -> dict:
    if not app_version or any(c in app_version for c in "\r\n"):
        raise ValueError("Current app version is required")
    sys.path.insert(0, str(solis_dir.resolve()))
    grpc = importlib.import_module("grpc")
    api = importlib.import_module("papi_pb2")
    stubs = importlib.import_module("papi_grpc")
    as_dict = importlib.import_module("google.protobuf.json_format").MessageToDict
    channel = grpc.secure_channel("api.game-idolypride.jp:443",
        grpc.ssl_channel_credentials(root_certificates=(solis_dir / "iprroots.pem").read_bytes()),
        options=[("grpc.primary_user_agent", "grpc-csharp/2.37.0-dev grpc-c/15.0.0 (android; chttp2)")])
    stub = stubs.MasterStub(channel)

    def fetch(number):
        response, context = stub.GetHelpCategory.with_call(
            api.MasterGetHelpCategoryRequest(helpType=number),
            metadata=[("x-app-version", app_version), ("content-type", "application/grpc")], timeout=30)
        for key, value in [*(context.initial_metadata() or []), *(context.trailing_metadata() or [])]:
            if key == "x-error-code" and str(value) not in ("", "0"):
                raise RuntimeError("Official help API returned an application error")
        return as_dict(response, use_integers_for_enums=True,
                       always_print_fields_with_no_presence=True).get("helpCategories", [])

    try:
        result = collect_help(fetch)
        result["app_version"] = app_version
        return result
    finally:
        channel.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solis-dir", type=Path, required=True)
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "cache/help/source.json")
    parser.add_argument("--master-output", type=Path, default=ROOT / "cache/help/HelpCategory.json")
    args = parser.parse_args(argv)
    try:
        if args.output.resolve() == args.master_output.resolve():
            raise ValueError("Source and MasterDB output must be different paths")
        result = fetch_help(args.solis_dir, args.app_version)
        atomic_json(args.output, result)
        atomic_json(args.master_output, result["categories"])
        print(f"Saved {len(result['categories'])} categories and {result['content_count']} help contents")
        return 0
    except Exception as error:
        print(f"Official help fetch failed ({type(error).__name__})", file=sys.stderr)
        return 1
