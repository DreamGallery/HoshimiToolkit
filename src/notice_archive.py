"""Bounded, resumable collection of public official notice HTML."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError

from .master_fetch import atomic_json
from .notice_fetch import ROOT, fetch_html, input_paths, parse_html


def page_digest(page: dict) -> str:
    return hashlib.sha256(json.dumps(page, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def valid_cache(record: dict, path: str) -> bool:
    if not isinstance(record, dict) or record.get("path") != path:
        return False
    page = record.get("page")
    if not isinstance(page, dict) or not isinstance(page.get("title"), str):
        return False
    if page.get("headline") is not None and not isinstance(page["headline"], str):
        return False
    texts = page.get("texts")
    return (isinstance(texts, list) and bool(texts) and all(isinstance(t, str) for t in texts)
            and record.get("page_sha256") == page_digest(page))


def collect_one(path: str, directory: Path, refresh: bool, fetch, sleep=time.sleep) -> dict:
    cached = directory / (path.split("/")[2] + ".json")
    if cached.is_file() and not refresh:
        try:
            record = json.loads(cached.read_text(encoding="utf-8"))
            if valid_cache(record, path):
                return {"path": path, "status": "cached", "page": record["page"]}
        except (OSError, ValueError):
            pass  # A damaged entry is refetched, never trusted silently.
    for attempt in range(1, 4):
        try:
            html = fetch(path)
            page = parse_html(html)
            record = {"version": 1, "path": path, "fetched_at": datetime.now(timezone.utc).isoformat(),
                      "html_utf8_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                      "page_sha256": page_digest(page), "page": page}
            atomic_json(cached, record)
            return {"path": path, "status": "downloaded", "page": page}
        except Exception as error:
            code = error.code if isinstance(error, HTTPError) else None
            transient = (isinstance(error, HTTPError) and (code == 429 or 500 <= code < 600)) or (
                not isinstance(error, HTTPError) and isinstance(error, (URLError, TimeoutError, ConnectionError)))
            if transient and attempt < 3:
                sleep(attempt)
                continue
            failure = {"path": path, "status": "failed", "error_type": type(error).__name__, "attempts": attempt}
            if code is not None:
                failure["http_status"] = code
            return failure


def archive_pages(data: dict, cache_dir: Path, output: Path, report_path: Path,
                  *, workers: int = 4, refresh: bool = False, fetch=fetch_html, progress=None) -> dict:
    if not 1 <= workers <= 8:
        raise ValueError("Notice workers must be between 1 and 8")
    if not cache_dir.resolve().is_relative_to((ROOT / "cache").resolve()):
        raise ValueError("Notice archive cache must remain under Toolkit/cache")
    paths = input_paths(data)
    if not paths:
        raise ValueError("No official notice pages found")
    cache_dir.mkdir(parents=True, exist_ok=True)
    pages, failed = {}, []
    counts = {"requested": len(paths), "downloaded": 0, "cached": 0, "failed": 0}

    def checkpoint():
        report = {"version": 1, "source": "official_notice_static_html",
                  "updated_at": datetime.now(timezone.utc).isoformat(), **counts,
                  "complete": sum(counts[k] for k in ("downloaded", "cached", "failed")) == len(paths) and not failed,
                  "failures": sorted(failed, key=lambda item: item["path"])}
        atomic_json(output, {"pages": dict(sorted(pages.items()))})
        atomic_json(report_path, report)
        return report

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(collect_one, path, cache_dir, refresh, fetch) for path in paths]
        for number, future in enumerate(as_completed(futures), 1):
            result = future.result()
            counts[result["status"]] += 1
            if result["status"] == "failed":
                failed.append(result)
            else:
                pages[result["path"]] = result["page"]
            if number % 50 == 0 or number == len(paths):
                checkpoint()
                if progress:
                    progress(dict(counts))
    return checkpoint()
