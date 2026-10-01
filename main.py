"""IDOLY PRIDE resource fetcher: on-device cache or Octo API."""

import argparse
import json
import shutil
import time
from pathlib import Path

import UnityPy.config
import proto.octodb_pb2 as octop
from google.protobuf.json_format import ParseDict

from src.config import ASSET_PATH, UPDATE_FLAG, UPDATE_PATH, UNITY_VERSION
from src.file_operation import file_operate
from src.image_process import image_scale
from src.octo_manager import MANIFEST, mark_download_complete, update_from_api, update_from_cache
from src.resource_download import download_resource
import src.rich_console as console


def run_once(args: argparse.Namespace) -> None:
    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    if args.source == "cache":
        raw = Path(args.cache_file).read_bytes()
        database = update_from_cache(raw, args.reset)
    else:
        database = update_from_api(args.reset)

    targeted = bool(args.name or args.prefix)
    full_download = args.download_all or args.init_download
    changed_download = getattr(args, "download_changed", False)
    if database is None:
        console.info("The Octo manifest is up to date")
        if not (full_download or targeted or changed_download):
            return
        if changed_download:
            return
    if full_download or (targeted and not changed_download):
        database = ParseDict(json.loads(MANIFEST.read_text(encoding="utf-8")), octop.Database())
    if targeted:
        selected_names = set(args.name)
        selected_prefixes = tuple(args.prefix)
        filtered = octop.Database()
        filtered.CopyFrom(database)
        del filtered.assetBundleList[:]
        del filtered.resourceList[:]
        for original, dest in ((database.assetBundleList, filtered.assetBundleList),
                               (database.resourceList, filtered.resourceList)):
            dest.extend(item for item in original if item.name in selected_names or
                        item.name.startswith(selected_prefixes))
        database = filtered
    console.info(f"Octo revision {database.revision}: {len(database.assetBundleList)} bundles, "
                 f"{len(database.resourceList)} resources selected")
    if args.manifest_only:
        return

    count = download_resource(database, args.download_type, not args.no_images)
    if not any(count):
        mark_download_complete(args.download_type, database)
        return
    revision_path = Path(UPDATE_PATH) / str(database.revision)
    if not args.no_images and args.download_type in ("ALL", "ab"):
        image_scale(database, str(revision_path / "image"),
                    str(revision_path / "stretch"))
    if UPDATE_FLAG:
        file_operate("copy", str(revision_path), "cache", dirs_exist_ok=True)
    else:
        file_operate("move", str(revision_path), "cache")
        shutil.rmtree(revision_path, ignore_errors=True)
    mark_download_complete(args.download_type, database)
    console.succeed(f"Finished Octo revision {database.revision}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("cache", "api"), default="cache")
    parser.add_argument("--cache-file", default="cache/octocacheevai")
    parser.add_argument("--mode", choices=("once", "loop"), default="once")
    parser.add_argument("--loop-interval", type=int, default=600)
    parser.add_argument("--reset", action="store_true", help="Rebuild the local manifest from scratch")
    parser.add_argument("--init-download", action="store_true", help="Download all assets in the full manifest")
    parser.add_argument("--download-type", choices=("ALL", "ab", "resource"), default="ALL")
    parser.add_argument("--name", action="append", default=[], help="Download an exact manifest filename; repeatable")
    parser.add_argument("--prefix", action="append", default=[], help="Download names with this prefix; repeatable")
    parser.add_argument("--download-all", action="store_true", help="Allow downloading every selected entry")
    parser.add_argument("--download-changed", action="store_true",
                        help="Download unfinished changes; name/prefix may narrow the pending list")
    parser.add_argument("--manifest-only", action="store_true", help="Read the manifest without downloading assets")
    parser.add_argument("--no-images", action="store_true", help="Skip image extraction and scaling")
    args = parser.parse_args()
    if args.loop_interval < 1:
        parser.error("--loop-interval must be positive")
    if args.mode == "loop" and args.source != "api":
        parser.error("--mode loop requires --source api")
    if args.download_changed and (args.download_all or args.init_download):
        parser.error("--download-changed cannot be combined with full download")
    if not args.manifest_only and not (args.name or args.prefix or args.download_all or
                                       args.init_download or args.download_changed):
        parser.error("select --name, --prefix, --download-changed, or --download-all before downloading")
    run_once(args)
    if args.mode == "loop":
        args.reset = False
        args.init_download = False
        while True:
            time.sleep(args.loop_interval)
            run_once(args)


if __name__ == "__main__":
    main()
