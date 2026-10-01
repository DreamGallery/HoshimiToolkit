#!/usr/bin/env python3
"""Export IDOLY PRIDE adventure text to CSV and merge translations back."""

import argparse
import json
from pathlib import Path

from src.adv_csv import coverage_file, export_file, merge_file, save_patch


def relative_names(root: Path, names: list[str], prefixes: list[str], suffix: str) -> list[Path]:
    if names:
        selected = [Path(name) for name in names]
        if any(path.is_absolute() or ".." in path.parts for path in selected):
            raise ValueError("--file must be a path below the input directory")
        return selected
    return sorted(path.relative_to(root) for path in root.rglob(f"adv_*{suffix}")
                  if not prefixes or path.name.startswith(tuple(prefixes)))


def load_name_glossary(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    with path.open(encoding="utf-8") as stream:
        mapping = json.load(stream)
    if not isinstance(mapping, dict) or any(
        not isinstance(source, str) or not isinstance(value, str) or not value
        for source, value in mapping.items()
    ):
        raise ValueError("Name glossary must map original names to nonempty translations")
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Create CSV files for translation")
    merge = commands.add_parser("merge", help="Apply translated CSV to original TXT")
    patch = commands.add_parser("patch", help="Save translated fields without original text")
    status = commands.add_parser("status", help="Show translation coverage from patches and names")
    for command in (export, merge, patch, status):
        command.add_argument("--source-dir", type=Path,
                             default=Path("cache/resource/adventure"))
        command.add_argument("--csv-dir", type=Path, default=Path("cache/csv"))
        command.add_argument("--file", action="append", default=[],
                             help="Relative adventure TXT filename; repeatable")
        command.add_argument("--prefix", action="append", default=[],
                             help="Process downloaded TXT files with this filename prefix")
    merge.add_argument("--output-dir", type=Path,
                       default=Path("cache/local-files/resource"))
    export.add_argument("--patch-dir", type=Path, default=None,
                        help="Apply matching sparse JSON patches while exporting CSV")
    for command in (merge, status):
        command.add_argument("--name-glossary", type=Path,
                             help="Apply shared speaker-name translations when merging TXT")
    patch.add_argument("--patch-dir", type=Path,
                       default=Path("cache/patches"))
    status.add_argument("--patch-dir", type=Path,
                        default=Path("cache/patches"))
    args = parser.parse_args()
    if args.file and args.prefix:
        parser.error("choose --file or --prefix, not both")
    names = relative_names(args.source_dir, args.file, args.prefix, ".txt")
    name_glossary = load_name_glossary(getattr(args, "name_glossary", None))
    count = 0
    text_translated = text_total = names_translated = names_total = 0
    for name in names:
        source = args.source_dir / name
        translation = args.csv_dir / name.with_suffix(".csv")
        if args.command == "status":
            patch_file = args.patch_dir / name.with_suffix(".json")
            coverage = coverage_file(source, patch_file if patch_file.is_file() else None,
                                     name_glossary)
            text_translated += coverage.text_translated
            text_total += coverage.text_total
            names_translated += coverage.names_translated
            names_total += coverage.names_total
            print(f"{name}: text {coverage.text_translated}/{coverage.text_total}, "
                  f"names {coverage.names_translated}/{coverage.names_total}")
        elif args.command == "export":
            patch_file = (args.patch_dir / name.with_suffix(".json")
                          if args.patch_dir else None)
            count += export_file(source, translation,
                                 patch_file if patch_file and patch_file.is_file() else None)
            print(f"CSV: {translation}")
        elif args.command == "patch":
            if not translation.is_file():
                continue
            output = args.patch_dir / name.with_suffix(".json")
            changes = save_patch(source, translation, output)
            count += changes
            print(f"Patch: {output} ({changes} translations)")
        else:
            if not translation.is_file():
                continue
            output = args.output_dir / name
            changes = merge_file(source, translation, output, name_glossary)
            count += changes
            print(f"TXT: {output} ({changes} replacements)")
    if args.command == "status":
        print(f"{len(names)} scripts; text {text_translated}/{text_total}, "
              f"names {names_translated}/{names_total}")
        return
    unit = "fields" if args.command == "export" else (
        "translations" if args.command == "patch" else "replacements")
    print(f"{len(names)} scripts; {count} {unit}")


if __name__ == "__main__":
    main()
