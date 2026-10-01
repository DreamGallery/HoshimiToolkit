#!/usr/bin/env python3
"""Repack one locally translated PNG into its original Octo AssetBundle."""

import argparse
from pathlib import Path
import tempfile

from PIL import Image
import UnityPy
import UnityPy.config

from src.config import UNITY_VERSION


def texture_objects(environment, texture_name: str):
    matches = []
    for obj in environment.objects:
        if obj.type.name != "Texture2D":
            continue
        texture = obj.read()
        if texture.m_Name == texture_name:
            matches.append(texture)
    return matches


def repack(source: Path, image_path: Path, output: Path,
           texture_name: str | None = None) -> tuple[int, int]:
    if not source.is_file() or not image_path.is_file():
        raise FileNotFoundError("Original AssetBundle and translated PNG are required")
    name = texture_name or source.name
    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    source_bytes = source.read_bytes()
    original = UnityPy.load(source_bytes)
    matches = texture_objects(original, name)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one Texture2D named {name!r}, found {len(matches)}")
    texture = matches[0]
    with Image.open(image_path) as loaded:
        image = loaded.convert("RGBA")
    if image.size != (texture.m_Width, texture.m_Height):
        raise ValueError(f"PNG size {image.size} differs from original "
                         f"{(texture.m_Width, texture.m_Height)}")
    # RGBA32 is lossless; ASTC recompression visibly blurs small Chinese glyphs.
    texture.set_image(image, target_format=4)
    texture.save()
    packed = original.file.save()
    if len(packed) > 64 * 1024 * 1024:
        raise ValueError("Repacked bundle exceeds the runtime override's 64 MiB limit")
    checked = UnityPy.load(packed)
    result = texture_objects(checked, name)
    if len(result) != 1 or result[0].image.tobytes() != image.tobytes():
        raise ValueError("Repacked texture did not retain the translated PNG pixels")
    if [(obj.type.name, obj.path_id) for obj in original.objects] != [
            (obj.type.name, obj.path_id) for obj in checked.objects]:
        raise ValueError("Repacking changed the AssetBundle object list")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=output.parent,
                                     prefix=".asset-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(packed)
    try:
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return len(source_bytes), len(packed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path,
                        help="Unmodified decoded AssetBundle from cache/asset")
    parser.add_argument("--image", required=True, type=Path,
                        help="Translated PNG with the original dimensions")
    parser.add_argument("--output", type=Path,
                        help="Defaults to cache/local-files/asset/<bundle name>")
    parser.add_argument("--texture-name", help="Defaults to the AssetBundle filename")
    args = parser.parse_args()
    output = args.output or Path("cache/local-files/asset") / args.bundle.name
    original, packed = repack(args.bundle, args.image, output, args.texture_name)
    print(f"Repacked {output} ({original} -> {packed} bytes)")


if __name__ == "__main__":
    main()
