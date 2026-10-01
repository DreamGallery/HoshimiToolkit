"""Anonymous, read-only legal document collection with lossless line segments."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys

from .master_fetch import atomic_json

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {1: "terms", 2: "privacy", 3: "fund_settlement", 4: "commercial_transaction",
              5: "service_guideline", 6: "license", 7: "external_transmission"}


def split_segments(source: str) -> list[dict]:
    if not source:
        return []
    parts = re.split(r"(\r\n|\r|\n)", source)
    return [{"id": f"line:{i // 2}", "source": parts[i],
             "line_ending": parts[i + 1] if i + 1 < len(parts) else ""}
            for i in range(0, len(parts), 2) if i < len(parts) - 1 or parts[i]]


def collect_rules(fetch) -> dict:
    rules = {}
    for number, category in CATEGORIES.items():
        source = fetch(number)
        if not isinstance(source, str) or len(source.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("Invalid or oversized official rule text")
        segments = split_segments(source)
        assert "".join(s["source"] + s["line_ending"] for s in segments) == source
        rules[str(number)] = {"rule_type": number, "category": category, "source": source,
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "translate": number != 6 and bool(source), "segments": segments}
    return {"schema_version": 1, "source_api": "https://api.game-idolypride.jp/api.Master/Rule",
            "access": "anonymous", "fetched_at": datetime.now(timezone.utc).isoformat(), "rules": rules}


def apply_runtime_fallback(result: dict, fallback: dict) -> dict:
    """Fill an empty commercial-transaction response using a reviewed game capture.

    A nonempty official response always wins, so changed source text reaches
    the translation checksum gate rather than silently retaining an old body.
    """
    import copy
    if fallback.get("schema_version") != 1 or set(fallback.get("rules", {})) != {"4"}:
        raise ValueError("Runtime fallback must contain only commercial-transaction rule 4")
    entry = fallback["rules"]["4"]
    source = entry.get("source")
    if (not isinstance(source, str) or not source.strip() or "\0" in source or
            len(source.encode("utf-8")) > 2 * 1024 * 1024 or
            hashlib.sha256(source.encode("utf-8")).hexdigest() != entry.get("source_sha256") or
            not isinstance(entry.get("provenance"), dict) or
            entry["provenance"].get("kind") != "game_runtime_capture"):
        raise ValueError("Invalid runtime legal source or provenance")
    output = copy.deepcopy(result)
    if not output["rules"]["4"]["source"]:
        output["rules"]["4"].update(source=source, source_sha256=entry["source_sha256"],
            segments=split_segments(source), translate=True, provenance=entry["provenance"])
        output["rules"]["4"]["provenance"]["reason"] = "anonymous_api_returned_empty"
        output["access"] = "anonymous_with_runtime_fallback"
    return output


def fetch_rules(solis_dir: Path, app_version: str) -> dict:
    sys.path.insert(0, str(solis_dir.resolve()))
    grpc = importlib.import_module("grpc")
    api = importlib.import_module("papi_pb2")
    stubs = importlib.import_module("papi_grpc")
    if not app_version or any(c in app_version for c in "\r\n"):
        raise ValueError("Current app version is required")
    channel = grpc.secure_channel("api.game-idolypride.jp:443",
        grpc.ssl_channel_credentials(root_certificates=(solis_dir / "iprroots.pem").read_bytes()),
        options=[("grpc.primary_user_agent", "grpc-csharp/2.37.0-dev grpc-c/15.0.0 (android; chttp2)")])
    stub = stubs.MasterStub(channel)
    def fetch(number):
        response, context = stub.Rule.with_call(api.MasterRuleRequest(ruleType=number),
            metadata=[("x-app-version", app_version), ("content-type", "application/grpc")], timeout=30)
        for key, value in [*(context.initial_metadata() or []), *(context.trailing_metadata() or [])]:
            if key == "x-error-code" and str(value) not in ("", "0"):
                raise RuntimeError("Official rule API returned an application error")
        return response.text
    try:
        return collect_rules(fetch)
    finally:
        channel.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solis-dir", type=Path, required=True)
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "cache/legal/source.json")
    parser.add_argument("--runtime-fallback", type=Path,
                        help="Reviewed rule-4 runtime capture, used only if the API body is empty")
    args = parser.parse_args(argv)
    try:
        result = fetch_rules(args.solis_dir, args.app_version)
        if args.runtime_fallback:
            result = apply_runtime_fallback(result, json.loads(args.runtime_fallback.read_text(encoding="utf-8")))
        atomic_json(args.output, result)
        print(f"Saved {len(result['rules'])} anonymous official rules to {args.output}")
        return 0
    except Exception as error:
        print(f"Official rule fetch failed ({type(error).__name__}); existing output retained", file=sys.stderr)
        return 1
