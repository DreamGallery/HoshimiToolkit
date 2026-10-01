"""Read IDOLY PRIDE Octo manifests from a device cache or the Octo API."""

import json
import os
from pathlib import Path

import proto.octodb_pb2 as octop
from google.protobuf.json_format import MessageToDict

from src.decrypt import decrypt_database_from_api, decrypt_octo_database
from src.warp_request import request_update


CACHE = Path("cache")
MANIFEST = CACHE / "OctoManifest.json"
DIFF = CACHE / "OctoDiff.json"
PENDING = CACHE / "OctoPending.json"


def _as_dict(database: octop.Database) -> dict:
    return MessageToDict(database, use_integers_for_enums=True,
                         always_print_fields_with_no_presence=True)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _read_local() -> dict | None:
    if not MANIFEST.exists():
        return None
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _check_database(database: octop.Database) -> None:
    if database.revision <= 0 or not database.urlFormat:
        raise ValueError("Octo manifest has no revision or download URL")


def _content_identity(item: dict | None) -> tuple | None:
    if item is None:
        return None
    return tuple(item.get(key) for key in
                 ("objectName", "size", "md5", "generation", "uploadVersionId"))


def _difference(current: dict, previous: dict | None) -> dict:
    """Keep complete metadata while selecting changed or missing assets."""
    result = {key: value for key, value in current.items()
              if key not in ("assetBundleList", "resourceList")}
    for list_name in ("assetBundleList", "resourceList"):
        old = {item["name"]: item for item in previous.get(list_name, [])} if previous else {}
        result[list_name] = [item for item in current.get(list_name, [])
                             if _content_identity(old.get(item["name"])) != _content_identity(item)]
    return result


def _merge_pending(current: dict, diff: dict, pending: dict | None) -> dict:
    """Carry unfinished entries forward only while their current content matches."""
    result = {key: value for key, value in current.items()
              if key not in ("assetBundleList", "resourceList")}
    for list_name in ("assetBundleList", "resourceList"):
        current_items = {item["name"]: item for item in current.get(list_name, [])}
        selected = {item["name"] for item in diff.get(list_name, [])}
        if pending:
            for item in pending.get(list_name, []):
                latest = current_items.get(item["name"])
                if latest and _content_identity(latest) == _content_identity(item):
                    selected.add(item["name"])
        result[list_name] = [item for item in current.get(list_name, [])
                             if item["name"] in selected]
    return result


def _store(current: dict, diff: dict, keep_pending: bool = True) -> octop.Database:
    # Write the diff first. The full manifest is the commit point for a revision.
    pending = None
    if keep_pending and PENDING.exists():
        pending = json.loads(PENDING.read_text(encoding="utf-8"))
    queued = _merge_pending(current, diff, pending)
    _write_json(DIFF, diff)
    _write_json(PENDING, queued)
    _write_json(MANIFEST, current)
    from google.protobuf.json_format import ParseDict
    return ParseDict(queued, octop.Database())


def _pending() -> octop.Database | None:
    if not PENDING.exists():
        return None
    from google.protobuf.json_format import ParseDict
    return ParseDict(json.loads(PENDING.read_text(encoding="utf-8")), octop.Database())


def mark_download_complete(download_type: str,
                           completed: octop.Database | None = None) -> None:
    if not PENDING.exists():
        return
    pending = json.loads(PENDING.read_text(encoding="utf-8"))
    selected = _as_dict(completed) if completed is not None else None
    for list_name, choices in (("assetBundleList", ("ALL", "ab")),
                               ("resourceList", ("ALL", "resource"))):
        if download_type not in choices:
            continue
        if selected is None:
            pending[list_name] = []
            continue
        finished = {item["name"]: _content_identity(item)
                    for item in selected.get(list_name, [])}
        pending[list_name] = [item for item in pending.get(list_name, [])
                              if finished.get(item["name"]) != _content_identity(item)]
    if pending.get("assetBundleList") or pending.get("resourceList"):
        _write_json(PENDING, pending)
    else:
        PENDING.unlink()


def update_from_cache(raw_cache: bytes, reset: bool = False) -> octop.Database | None:
    database = decrypt_octo_database(raw_cache)
    _check_database(database)
    current = _as_dict(database)
    previous = None if reset else _read_local()
    if previous == current:
        return _pending()
    return _store(current, _difference(current, previous), not reset)


def update_from_api(reset: bool = False) -> octop.Database | None:
    previous = None if reset else _read_local()
    old_revision = int(previous.get("revision", 0)) if previous else 0
    response = decrypt_database_from_api(request_update(old_revision))
    _check_database(response)
    if response.revision == old_revision and not reset:
        return _pending()
    if response.revision < old_revision:
        raise ValueError("Octo API returned an older revision")

    # A nonzero request returns only changes. Fetch the complete manifest before
    # updating local state, so the next run has a reliable base revision.
    full = response if old_revision == 0 else decrypt_database_from_api(request_update(0))
    _check_database(full)
    if full.revision != response.revision:
        raise ValueError("Octo API revision changed during manifest download; retry")
    current = _as_dict(full)
    if previous is None:
        diff = current
    else:
        # Compare complete entries as well: an API diff can omit files which
        # changed state or were removed between requests.
        diff = _difference(current, previous)
    return _store(current, diff, not reset)
