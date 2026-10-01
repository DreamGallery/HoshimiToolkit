"""Download and unpack entries selected by an IDOLY PRIDE Octo manifest."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path

import proto.octodb_pb2 as octop
import src.rich_console as console

from src.config import ASSET_PATH, RESOURCE_PATH, UNITY_SIGNATURE, UPDATE_PATH
from src.decrypt import crypt_by_string
from src.file_operation import classified_path, file_store
from src.image_process import unpack_to_image
from src.warp_request import send_request


def object_url(template: str, item: octop.Data, kind: str) -> str:
    # IDOLY PRIDE's URL template requires all four fields.
    url = (template.replace("{v}", str(item.uploadVersionId))
           .replace("{type}", kind)
           .replace("{o}", item.objectName)
           .replace("{g}", str(item.generation)))
    if "{" in url or "}" in url or not url.startswith(("https://", "http://")):
        raise ValueError(f"Unusable Octo URL template for {item.name}")
    return url


def one_task(item: octop.Data, kind: str, url_format: str, dest_path: Path,
             extract_images: bool = True) -> str:
    if not item.name or Path(item.name).name != item.name:
        raise ValueError(f"Unsafe Octo filename: {item.name!r}")
    response = send_request(object_url(url_format, item, kind))
    obj = response.content
    if not obj:
        raise ValueError(f"Empty Octo object: {item.name}")
    if item.size and len(obj) != item.size:
        raise ValueError(f"Octo size mismatch for {item.name}: {len(obj)} != {item.size}")
    if item.md5 and hashlib.md5(obj).hexdigest().lower() != item.md5.lower():
        raise ValueError(f"Octo MD5 mismatch for {item.name}")
    if kind == "assetbundle":
        asset_bytes = obj if obj.startswith(UNITY_SIGNATURE) else crypt_by_string(obj, item.name, 0, 0, min(256, len(obj)))
        if not asset_bytes.startswith(UNITY_SIGNATURE):
            raise ValueError(f"Deobfuscated object is not a Unity asset: {item.name}")
        file_store(asset_bytes, item.name, str(dest_path), kind)
        if extract_images:
            try:
                unpack_to_image(asset_bytes, str(dest_path))
            except Exception as exc:
                console.warning(f"Could not extract images from {item.name}: {exc}")
    else:
        file_store(obj, item.name, str(dest_path), kind)
    return item.name


def _verified_local_resource(item: octop.Data, root: str | Path) -> bool:
    if not item.name or Path(item.name).name != item.name or not item.md5 or not item.size:
        return False
    path = classified_path(item.name, root, "resources")
    if not path.is_file() or path.stat().st_size != item.size:
        return False
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == item.md5.lower()


def download_resource(database: octop.Database, download_type: str = "ALL",
                      extract_images: bool = True) -> tuple[int, int]:
    if download_type not in ("ALL", "ab", "resource"):
        raise ValueError(f"Invalid download type: {download_type}")
    revision = database.revision
    asset_path = Path(UPDATE_PATH) / str(revision) / Path(ASSET_PATH).name
    resource_path = Path(UPDATE_PATH) / str(revision) / Path(RESOURCE_PATH).name
    selected = []
    if download_type in ("ALL", "ab"):
        selected.extend((item, "assetbundle", asset_path) for item in database.assetBundleList)
    if download_type in ("ALL", "resource"):
        selected.extend((item, "resources", resource_path) for item in database.resourceList)
    failures = []
    asset_count = resource_count = 0
    reused_final = reused_staged = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = {}
        for item, kind, path in selected:
            if kind == "resources":
                if _verified_local_resource(item, RESOURCE_PATH):
                    reused_final += 1
                    continue
                if _verified_local_resource(item, path):
                    reused_staged += 1
                    resource_count += 1
                    continue
            tasks[pool.submit(one_task, item, kind, database.urlFormat, path,
                              extract_images)] = kind
        for future in as_completed(tasks):
            try:
                name = future.result()
                if tasks[future] == "assetbundle":
                    asset_count += 1
                else:
                    resource_count += 1
                console.succeed(f"Downloaded {name}")
            except Exception as exc:
                failures.append(str(exc))
                console.error(str(exc))
    if reused_final or reused_staged:
        console.info(f"Reused {reused_final} final and {reused_staged} staged "
                     "verified local resources")
    if failures:
        raise RuntimeError(f"{len(failures)} of {len(selected)} Octo downloads failed")
    return asset_count, resource_count
