import sys
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED
import src.rich_console as console
from src.config import UNITY_SIGNATURE, UPDATE_PATH
from src.image_process import unpack_to_image
from src.decrypt import crypt_by_string
from src.warp_request import send_request
from src.file_operation import file_store


_asset_count = 0
_resource_count = 0
_current_count = 0
lock = threading.Lock()


def one_task(item: dict, _type: str, url_format: str, dest_path: str):
    global _current_count
    url = (
        url_format.replace("{v}", str(item["uploadVersionId"]))
        .replace("{type}", _type)
        .replace("{o}", item["objectName"])
        .replace("{g}", str(item["generation"]))
    )
    obj = send_request(url, verify=True).content
    if obj.__len__() == 0:
        console.info(f"Empty object '{item['name']}', skipping.")
        return

    if _type == "assetbundle":
        try:
            if obj[0:5] != UNITY_SIGNATURE:
                asset_bytes = crypt_by_string(obj, item["name"], 0, 0, 256)
            else:
                asset_bytes = obj
            file_store(asset_bytes, item["name"], dest_path, _type)
            if asset_bytes[0:5] != UNITY_SIGNATURE:
                console.error(f"'{item['name']}' '{item['md5']}' is not a unity asset.")
                raise
            # Converts an image asset to png, does nothing if asset is not texture2d/sprite
            unpack_to_image(asset_bytes, dest_path)
            lock.acquire()
            _current_count += 1
            console.succeed(
                f"({_current_count}/{_asset_count}) AssetBundle '{item['name']}' has been successfully deobfuscated."
            )
            lock.release()
        except:
            lock.acquire()
            _current_count += 1
            console.error(f"{_current_count}/{_asset_count}) Failed to deobfuscate '{item['name']}'.")
            console.error(sys.exc_info())
            lock.release()
    elif _type == "resources":
        file_store(obj, item["name"], dest_path, _type)
        lock.acquire()
        _current_count += 1
        console.succeed(
            f"{_current_count}/{_resource_count}) Resource '{item['name']}' has been successfully renamed."
        )
        lock.release()


def download_resource() -> int:
    global _asset_count
    global _resource_count
    global _current_count

    with open(f"cache/OctoDiff.json", "r", encoding="utf8") as of:
        manifest = json.load(of)

    revision = manifest["revision"]
    urlFormat = manifest["urlFormat"]

    _asset_count = len(manifest["assetBundleList"])
    _resource_count = len(manifest["resourceList"])

    # set update path
    asset_store_path = f"{UPDATE_PATH}/{revision}/asset"
    resource_store_path = f"{UPDATE_PATH}/{revision}/resource"
    Path(asset_store_path).mkdir(parents=True, exist_ok=True)
    Path(resource_store_path).mkdir(parents=True, exist_ok=True)

    executor = ThreadPoolExecutor(max_workers=20)

    # Asset download/update
    _current_count = 0
    asset_tasks = [
        executor.submit(one_task, item, "assetbundle", urlFormat, asset_store_path)
        for item in manifest["assetBundleList"]
    ]
    wait(asset_tasks, return_when=ALL_COMPLETED)
    console.succeed("All AssetBundle tasks has been successfully processed.")

    # Resource download/update
    _current_count = 0
    resource_tasks = [
        executor.submit(one_task, item, "resources", urlFormat, resource_store_path)
        for item in manifest["resourceList"]
    ]
    wait(resource_tasks, return_when=ALL_COMPLETED)
    console.succeed("All resource tasks has been successfully processed.")

    return revision
