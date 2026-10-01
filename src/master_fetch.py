#!/usr/bin/env python3
"""Fetch IDOLY PRIDE MasterDB using a local SolisClient checkout and auth XML.

Requires SolisClient's generated protobuf modules, grpcio, pycryptodome,
requests, protobuf and a Python SQLCipher binding. Credentials are read only
in memory and are never written to the output directory.
"""

from __future__ import annotations

import argparse
import configparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent.parent
MASTER_FETCH_API_VERSION = 1
STATE_FILE = "master-fetch-state.json"
MARKER_FILE = ".master-fetch-in-progress.json"
TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")


def firebase_api_key(settings_path: Path | None = None,
                     ini_path: Path | None = None) -> str:
    """Use local Firebase settings before the optional config.ini fallback."""
    key = os.environ.get("IDOLY_FIREBASE_API_KEY")
    if key is None:
        path = settings_path or Path(os.environ.get(
            "IDOLY_FIREBASE_SETTINGS_FILE", ROOT / "cache" / "firebase-settings.json"))
        if path.is_file():
            settings = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError(f"Firebase settings must be a JSON object: {path}")
            key = settings.get("api_key")
        if key is None:
            config = configparser.ConfigParser()
            config.read(ini_path or os.environ.get("IDOLY_TOOLKIT_CONFIG", ROOT / "config.ini"), encoding="utf-8")
            key = config.get("Firebase settings", "API_KEY", fallback="")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Firebase API key is missing; set IDOLY_FIREBASE_API_KEY, "
                         "cache/firebase-settings.json or [Firebase settings] API_KEY")
    return key.strip()


def atomic_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".master-fetch-",
                                     delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(contents)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: dict) -> None:
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2,
                                   sort_keys=True) + "\n").encode("utf-8"))


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pack_fingerprint(pack, version: str) -> str:
    # Include the version so an unchanged URL or file name cannot silently
    # reuse an older game's table. Store only the digest, never the key or URL.
    identity = [version, pack.type, pack.fileName, pack.fileSize,
                pack.cryptoKey, pack.downloadUrl]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def load_modules(solis_dir: Path):
    sys.path.insert(0, str(solis_dir.resolve()))
    import grpc
    import requests
    import papi_grpc
    import papi_pb2
    import pmaster_pb2
    import ptransaction_pb2
    from google.protobuf import empty_pb2
    from google.protobuf.json_format import MessageToDict
    from sqlcipher3 import dbapi2 as sqlcipher
    return (grpc, requests, papi_grpc, papi_pb2, pmaster_pb2,
            ptransaction_pb2, empty_pb2, MessageToDict, sqlcipher)


def token_from_xml(path: Path, requests, config):
    record = None
    for node in ElementTree.parse(path).getroot():
        if "GET_TOKEN_RESPONSE" in node.attrib.get("name", ""):
            record = json.loads(node.text)
            break
    if record is None:
        raise ValueError("Firebase token record not found in auth XML")
    if record["issued_at"] / 1000 + int(record["expires_in"]) > time.time() + 60:
        return record["access_token"]
    response = requests.post(
        "https://securetoken.googleapis.com/v1/token",
        params={"key": firebase_api_key()},
        data=json.dumps({"grantType": "refresh_token",
                         "refreshToken": record["refresh_token"]}),
        headers=config["firebase"], timeout=30)
    if not response.ok:
        raise RuntimeError(f"Firebase refresh returned HTTP {response.status_code}")
    return response.json()["id_token"]


def get_master_tag(solis_dir, auth_xml, app_version, modules):
    grpc, requests, papi_grpc, api, _, _, empty, _, _ = modules
    config = json.loads((solis_dir / "client_config.json").read_text(encoding="utf-8"))
    headers = dict(config["qseed"])
    headers["X-AppVersion"] = app_version
    qseed = requests.get("https://id.qseed.jp/getId", headers=headers, timeout=30)
    qseed.raise_for_status()
    metadata = dict(config["grpc"])
    metadata["x-app-version"] = app_version
    metadata["x-seed-id"] = qseed.text
    id_token = token_from_xml(auth_xml, requests, config)
    roots = (solis_dir / "iprroots.pem").read_bytes()
    channel = grpc.secure_channel(
        "api.game-idolypride.jp:443",
        grpc.ssl_channel_credentials(root_certificates=roots),
        options=[("grpc.primary_user_agent",
                  "grpc-csharp/2.37.0-dev grpc-c/15.0.0 (android; chttp2)")])

    def checked(method, request, headers):
        response, call = method.with_call(request, metadata=list(headers.items()), timeout=30)
        for key, value in call.initial_metadata():
            if key == "x-error-code":
                raise RuntimeError(f"Game API error code: {value}")
        return response

    try:
        system = papi_grpc.SystemStub(channel)
        checked(system.Check, api.SystemCheckRequest(), {
            "x-app-version": app_version,
            "content-type": metadata["content-type"]})
        checked(system.Check, api.SystemCheckRequest(firebaseIDToken=id_token), metadata)
        auth = papi_grpc.AuthStub(channel)
        login = checked(auth.Login, api.AuthLoginRequest(firebaseIDToken=id_token),
                        metadata)
        metadata["x-auth-token"] = login.gameAuthToken
        master = papi_grpc.MasterStub(channel)
        return checked(master.Get, empty.Empty(), metadata).masterTag
    finally:
        channel.close()


def decode_pack(pack, output_dir, modules, reuse: bool = False):
    _, requests, _, _, pmaster, _, _, as_dict, sqlcipher = modules
    name = pack.type
    if not isinstance(name, str) or not TABLE_NAME.fullmatch(name):
        raise ValueError(f"Invalid MasterDB table name: {name!r}")
    output = output_dir / f"{name}.json"
    if reuse:
        return name, "cached"
    response = requests.get(pack.downloadUrl, timeout=60)
    response.raise_for_status()
    if len(response.content) != pack.fileSize:
        raise ValueError(f"{name}: download length differs from MasterTag")
    encrypted = output_dir / f".{name}.db"
    temporary = output_dir / f".{name}.json.tmp"
    encrypted.write_bytes(response.content)
    connection = None
    try:
        connection = sqlcipher.connect(str(encrypted))
        connection.execute('pragma key="x\'%s\'"' % pack.cryptoKey)
        rows = connection.execute(f"select data from {name}")
        cls = getattr(pmaster, name)
        count = 0
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write("[\n")
            for row in rows:
                if count:
                    stream.write(",\n")
                json.dump(as_dict(cls.FromString(row[0]), use_integers_for_enums=True,
                                  always_print_fields_with_no_presence=True),
                          stream, ensure_ascii=False, separators=(",", ":"))
                count += 1
            stream.write("\n]\n")
        temporary.replace(output)
        return name, count
    finally:
        if connection is not None:
            connection.close()
        encrypted.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def fetch_packs(tag, output_dir: Path, modules, *, workers: int = 12,
                selected: set[str] | None = None, decoder=decode_pack) -> tuple[int, int]:
    packs = [pack for pack in tag.masterTagPacks
             if selected is None or pack.type in selected]
    invalid = [pack.type for pack in packs if not isinstance(pack.type, str)
               or not TABLE_NAME.fullmatch(pack.type)]
    if invalid:
        raise ValueError(f"Invalid MasterDB table name in MasterTag: {invalid[0]!r}")
    if selected is not None and len(packs) != len(selected):
        raise ValueError("some selected tables are missing from MasterTag")
    if len({pack.type for pack in packs}) != len(packs):
        raise ValueError("MasterTag contains duplicate table names")
    output_dir.mkdir(parents=True, exist_ok=True)
    marker = output_dir / MARKER_FILE
    if marker.exists() and selected is not None:
        raise ValueError("A previous MasterDB fetch was interrupted; retry the full fetch")
    state_path = output_dir / STATE_FILE
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    if not isinstance(state, dict) or not isinstance(state.get("tables", {}), dict):
        raise ValueError(f"{state_path}: invalid fetch state")
    prior_tables = state.get("tables", {})
    fingerprints = {pack.type: pack_fingerprint(pack, tag.version) for pack in packs}
    reusable = {}
    for pack in packs:
        output = output_dir / f"{pack.type}.json"
        record = prior_tables.get(pack.type)
        reusable[pack.type] = (isinstance(record, dict) and
                               record.get("pack") == fingerprints[pack.type] and
                               output.is_file() and record.get("sha256") == file_digest(output))
    atomic_json(marker, {"version": tag.version, "full": selected is None})
    failures = []
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(decoder, pack, output_dir, modules,
                                   reusable[pack.type]): pack.type for pack in packs}
        for future in as_completed(futures):
            name = futures[future]
            try:
                _, count = future.result()
                output = output_dir / f"{name}.json"
                results[name] = {"pack": fingerprints[name], "sha256": file_digest(output)}
                print(f"{name}: {count}", flush=True)
            except Exception as error:
                failures.append(name)
                print(f"{name}: FAILED ({type(error).__name__})", flush=True)
    updated = dict(prior_tables)
    updated.update(results)
    state = {"version": tag.version,
             "complete_version": state.get("complete_version"),
             "tables": updated}
    if not failures and selected is None:
        expected = {f"{pack.type}.json" for pack in packs}
        stale = [path for path in output_dir.glob("*.json")
                 if path.name not in expected and
                 path.name not in {STATE_FILE, MARKER_FILE}]
        if stale:
            archive = output_dir / ".retired" / (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") +
                uuid.uuid4().hex[:8])
            archive.mkdir(parents=True)
            for path in stale:
                os.replace(path, archive / path.name)
        state["tables"] = {name: updated[name] for name in fingerprints}
        state["complete_version"] = tag.version
    atomic_json(state_path, state)
    if failures:
        raise RuntimeError(f"MasterDB fetch failed for {len(failures)} tables: {', '.join(sorted(failures)[:5])}")
    atomic_bytes(output_dir / "master-tag.pb", tag.SerializeToString())
    marker.unlink()
    return len(packs), sum(1 for value in reusable.values() if value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solis-dir", type=Path, required=True,
                        help="Local checkout of vilebbit/SolisClient")
    parser.add_argument("--auth-xml", type=Path,
                        help="Temporary copy of game's Firebase auth shared preference")
    parser.add_argument("--app-version", help="Installed game's versionName")
    parser.add_argument("--master-tag", type=Path,
                        help="Reuse a previously fetched MasterTag protobuf")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "cache/masterdata")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--table", action="append", default=[],
                        help="Download only this MasterDB table; repeatable")
    args = parser.parse_args()
    if not 1 <= args.workers <= 32:
        parser.error("workers must be between 1 and 32")
    if args.master_tag is None and (args.auth_xml is None or not args.app_version):
        parser.error("provide --master-tag or both --auth-xml and --app-version")
    modules = load_modules(args.solis_dir)
    transaction = modules[5]
    if args.master_tag:
        tag = transaction.MasterTag.FromString(args.master_tag.read_bytes())
    else:
        tag = get_master_tag(args.solis_dir, args.auth_xml, args.app_version, modules)
    count, cached = fetch_packs(tag, args.output_dir, modules, workers=args.workers,
                                selected=set(args.table) if args.table else None)
    print(f"MasterTag version {tag.version}; {count} tables ready, {cached} reused")


if __name__ == "__main__":
    main()
