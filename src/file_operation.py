import os
import shutil
import tempfile
from pathlib import Path
from src.config import CLASSIFY


def classified_path(file_name: str, root_path: str | Path, _type: str) -> Path:
    rules = CLASSIFY[_type]
    if _type == "assetbundle" and "shader" in file_name:
        sub_path = rules["shader"]
    else:
        sub_path = rules["other"]
        parts = file_name.split("_", 2)
        candidates = (["_".join(parts[:2])] if len(parts) > 1 else []) + parts[:2]
        for prefix in candidates:
            if prefix in rules:
                sub_path = rules[prefix]
                break

    return Path(root_path) / sub_path / file_name


def file_store(data_bytes: bytes, file_name: str, root_path: str, _type: str):
    target = classified_path(file_name, root_path, _type)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".octo-", delete=False) as fp:
        temporary = fp.name
        fp.write(data_bytes)
    try:
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def file_operate(mode: str, source_path: str, dest_path: str, **kwargs):
    if mode == "copy" and kwargs != {"dirs_exist_ok": True}:
        shutil.copytree(source_path, dest_path, **kwargs)
        return
    if mode not in {"copy", "move"}:
        raise ValueError(f"Unsupported file operation: {mode}")
    source_root = Path(source_path)
    destination_root = Path(dest_path)
    if not source_root.is_dir():
        raise FileNotFoundError(source_root)
    for root, _, files in os.walk(source_root):
        dest_dir = destination_root / Path(root).relative_to(source_root)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for file in files:
            source = Path(root) / file
            target = dest_dir / file
            if target.exists() and os.path.samefile(source, target):
                continue
            if mode == "move":
                # Both paths are below cache/. Replacing in one filesystem
                # keeps the previous resource available if this step fails.
                os.replace(source, target)
                continue
            with tempfile.NamedTemporaryFile(dir=dest_dir, prefix=".octo-copy-",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    with source.open("rb") as original:
                        shutil.copyfileobj(original, stream)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
