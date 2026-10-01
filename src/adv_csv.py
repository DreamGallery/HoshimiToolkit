"""Lossless CSV round trip for IDOLY PRIDE adventure scripts."""

from __future__ import annotations

# The localify project checks this before using a separately cloned toolkit.
WORKFLOW_API_VERSION = 1

import csv
from collections import Counter
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


ASSIGNMENT = re.compile(r"(?:(?<=\[)|(?<= ))([A-Za-z_][A-Za-z_0-9]*)=")
TAG = re.compile(r"^\[([A-Za-z_][A-Za-z_0-9]*)\b")
FIELDS = {
    "message": {"text", "name"},
    "narration": {"text"},
    "title": {"title"},
    "choicegroup": {"text"},
    "choice": {"text"},
}
PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z_0-9]*(?::[^{}]+)?\}|\{\d+(?::[^{}]+)?\}|<[^>]+>")


@dataclass(frozen=True)
class Field:
    identifier: str
    key: str
    category: str
    name: str
    source: str
    start: int
    end: int


@dataclass(frozen=True)
class Coverage:
    text_translated: int
    text_total: int
    names_translated: int
    names_total: int


def _value_end(line: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            index += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            if depth == 0:
                return index
            depth -= 1
        elif char == " " and depth == 0 and ASSIGNMENT.match(line, index + 1):
            return index
        index += 1
    raise ValueError("Unclosed adventure command")


def fields(script: str) -> list[Field]:
    result = []
    offset = 0
    for line_number, line in enumerate(script.splitlines(keepends=True), 1):
        command = line.rstrip("\r\n")
        match = TAG.match(command)
        if not match:
            offset += len(line)
            continue
        tag = match.group(1)
        wanted = FIELDS.get(tag, set())
        if not wanted:
            offset += len(line)
            continue
        assignments = list(ASSIGNMENT.finditer(command))
        name = ""
        for assignment in assignments:
            if assignment.group(1) == "name":
                name = command[assignment.end():_value_end(command, assignment.end())]
                break
        counts: dict[str, int] = {}
        for assignment in assignments:
            key = assignment.group(1)
            if key not in wanted:
                continue
            start = assignment.end()
            end = _value_end(command, start)
            value = command[start:end]
            if not value:
                continue
            counts[key] = counts.get(key, 0) + 1
            category = ("choice" if tag in {"choice", "choicegroup"} else
                        "narration" if tag == "narration" else key)
            result.append(Field(f"{line_number}:{key}:{counts[key]}", key, category, name,
                                value, offset + start, offset + end))
        offset += len(line)
    return result


def csv_identifier(item: Field) -> str:
    """Show the script command type while retaining the line and field ordinal."""
    line, _, ordinal = item.identifier.split(":")
    return f"{line}:{item.category}:{ordinal}"


def matches_csv_identifier(item: Field, identifier: str) -> bool:
    # Earlier CSVs used :text: for narration and choices. Both spellings locate
    # the same source field; patches continue to use the legacy stable key.
    return identifier in {item.identifier, csv_identifier(item)}


def validate_translation(item: Field, value: str) -> None:
    if not isinstance(value, str) or any(char in value for char in ("\r", "\n", "[", "]")):
        raise ValueError(f"Unsafe script character in {item.identifier}")
    if Counter(PLACEHOLDER.findall(item.source)) != Counter(PLACEHOLDER.findall(value)):
        raise ValueError(f"Placeholders differ from source in {item.identifier}")
    if item.source.count(r"\n") != value.count(r"\n"):
        raise ValueError(f"Visible line breaks differ from source in {item.identifier}")


def _merge_user_honorific(item: Field, value: str,
                          honorifics: dict[str, str]) -> str:
    """Resolve an untranslated player-name suffix after the CSV is edited.

    Other character names stay exact-field matches: replacing them as arbitrary
    substrings could change dialogue that happens to contain the same characters.
    """
    for original, translated in honorifics.items():
        if original in item.source and original in value:
            value = value.replace(original, translated)
    return value


def _load_patch(items: list[Field], checksum: str, patch: Path | None) -> dict[str, str]:
    if patch is None:
        return {}
    with patch.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("Translation patch must be a JSON object")
    if data.get("source_sha256") != checksum:
        raise ValueError("Translation patch belongs to a different script or revision")
    translations = data.get("translations", {})
    if not isinstance(translations, dict) or set(translations) - {
            item.identifier for item in items if item.key != "name"}:
        raise ValueError("Translation patch contains unknown or speaker-name fields; "
                         "move speaker translations to the name glossary")
    if any(not isinstance(value, str) for value in translations.values()):
        raise ValueError("Translation patch values must be strings")
    return translations


def _read_csv(path: Path) -> list[dict[str, str]]:
    columns = {"id", "name", "text", "trans"}
    with path.open("r", encoding="utf-8", newline="") as stream:
        try:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames != ["id", "name", "text", "trans"]:
                raise ValueError(f"{path}: CSV columns must be id,name,text,trans")
            rows = list(reader)
        except csv.Error as error:
            raise ValueError(f"{path}: invalid CSV: {error}") from error
    if any(set(row) != columns or any(not isinstance(value, str)
                                      for value in row.values()) for row in rows):
        raise ValueError(f"{path}: CSV has missing or extra cells")
    return rows


def coverage_file(source: Path, patch: Path | None = None,
                  name_glossary: dict[str, str] | None = None) -> Coverage:
    with source.open("r", encoding="utf-8", newline="") as stream:
        script = stream.read()
    items = fields(script)
    checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
    translations = _load_patch(items, checksum, patch)
    text_total = text_translated = names_total = names_translated = 0
    for item in items:
        value = translations.get(item.identifier, "")
        if not value or value == item.source:
            value = (name_glossary or {}).get(item.source, "") or value
        if value:
            validate_translation(item, value)
        translated = bool(value and value != item.source)
        if item.key == "name":
            names_total += 1
            names_translated += translated
        else:
            text_total += 1
            text_translated += translated
    return Coverage(text_translated, text_total, names_translated, names_total)


def export_file(source: Path, output: Path, patch: Path | None = None,
                name_glossary: dict[str, str] | None = None) -> int:
    with source.open("r", encoding="utf-8", newline="") as stream:
        script = stream.read()
    items = [item for item in fields(script) if item.key != "name"]
    checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
    translations = _load_patch(items, checksum, patch)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "name", "text", "trans"],
                                lineterminator="\n")
        writer.writeheader()
        for field in items:
            writer.writerow({"id": csv_identifier(field), "name": field.name,
                             "text": field.source,
                             "trans": translations.get(field.identifier, "")})
        writer.writerow({"id": "info", "name": source.name,
                         "text": checksum, "trans": ""})
        writer.writerow({"id": "译者", "name": "", "text": "", "trans": ""})
    return len(items)


def rebase_file(source: Path, old_csv: Path, output: Path) -> tuple[int, list[dict[str, str]]]:
    """Carry translations to a new script only when the Japanese text still matches.

    Exact field IDs take priority. A changed line number may still be matched if
    the field type, speaker and original text occur exactly once on each side.
    Translations that cannot be matched are reported, never silently reused.
    """
    old_rows = _read_csv(old_csv)
    if len(old_rows) < 2 or old_rows[-2]["id"] != "info" or old_rows[-1]["id"] != "译者":
        raise ValueError(f"{old_csv}: missing CSV metadata rows")
    old_fields = old_rows[:-2]
    if len({row["id"] for row in old_fields}) != len(old_fields):
        raise ValueError(f"{old_csv}: duplicate field IDs")
    def identity(row: dict[str, str]) -> tuple[str, str, str]:
        parts = row["id"].split(":")
        if len(parts) != 3 or parts[1] not in {"text", "title", "choice", "narration"}:
            raise ValueError(f"{old_csv}: invalid field ID {row['id']!r}")
        return parts[1], row["name"], row["text"]

    for row in old_fields:
        identity(row)
    export_file(source, output)
    with output.open("r", encoding="utf-8", newline="") as stream:
        new_rows = list(csv.DictReader(stream))
    with source.open("r", encoding="utf-8", newline="") as stream:
        new_fields = {csv_identifier(item): item for item in fields(stream.read())
                      if item.key != "name"}
    old_by_id = {row["id"]: index for index, row in enumerate(old_fields)}
    used = set()
    matched_new = set()
    for new_index, row in enumerate(new_rows[:-2]):
        item = new_fields[row["id"]]
        index = next((old_by_id[key] for key in (row["id"], item.identifier)
                      if key in old_by_id), None)
        if index is not None and identity(old_fields[index]) == identity(row):
            row["trans"] = old_fields[index]["trans"]
            used.add(index)
            matched_new.add(new_index)
    remaining_old = {}
    remaining_new = {}
    for index, row in enumerate(old_fields):
        if index not in used:
            remaining_old.setdefault(identity(row), []).append(index)
    for index, row in enumerate(new_rows[:-2]):
        if index not in matched_new:
            remaining_new.setdefault(identity(row), []).append(index)
    for key, old_indexes in remaining_old.items():
        new_indexes = remaining_new.get(key, [])
        if len(old_indexes) == len(new_indexes) == 1:
            old_index, new_index = old_indexes[0], new_indexes[0]
            new_rows[new_index]["trans"] = old_fields[old_index]["trans"]
            used.add(old_index)
            matched_new.add(new_index)
    # Old :text: rows may have come from a narration or choice command. Only
    # permit this looser match when the source and speaker identify one row.
    for old_index, old_row in enumerate(old_fields):
        if old_index in used or identity(old_row)[0] != "text":
            continue
        candidates = [index for index, row in enumerate(new_rows[:-2])
                      if index not in matched_new and identity(row)[0] in
                      {"choice", "narration"} and
                      identity(row)[1:] == identity(old_row)[1:]]
        if len(candidates) == 1 and sum(
                identity(other)[0] == "text" and
                identity(other)[1:] == identity(old_row)[1:]
                for index, other in enumerate(old_fields) if index not in used) == 1:
            new_index = candidates[0]
            new_rows[new_index]["trans"] = old_row["trans"]
            used.add(old_index)
            matched_new.add(new_index)
    for row in new_rows[:-2]:
        if row["trans"]:
            validate_translation(new_fields[row["id"]], row["trans"])
    new_rows[-1]["trans"] = old_rows[-1]["trans"]
    lost = [{"id": row["id"], "source": row["text"], "translation": row["trans"]}
            for index, row in enumerate(old_fields)
            if index not in used and row["trans"] and row["trans"] != row["text"]]
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "name", "text", "trans"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(new_rows)
    return sum(bool(row["trans"]) for row in new_rows[:-2]), lost


def save_patch(source: Path, translation: Path, output: Path,
               name_glossary: dict[str, str] | None = None) -> int:
    with source.open("r", encoding="utf-8", newline="") as stream:
        script = stream.read()
    items = [item for item in fields(script) if item.key != "name"]
    checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
    rows = _read_csv(translation)
    if len(rows) != len(items) + 2 or (rows[-2]["id"], rows[-2]["name"],
                                        rows[-2]["text"]) != ("info", source.name, checksum):
        raise ValueError("CSV belongs to a different script or revision")
    if rows[-1]["id"] != "译者":
        raise ValueError("CSV translator footer is missing")
    translations = {}
    for item, row in zip(items, rows[:-2]):
        if not matches_csv_identifier(item, row["id"]) or row["text"] != item.source:
            raise ValueError(f"CSV source mismatch at {item.identifier}")
        if row["trans"]:
            validate_translation(item, row["trans"])
        if row["trans"] and row["trans"] != item.source:
            translations[item.identifier] = row["trans"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        json.dump({"source_sha256": checksum, "translations": translations}, stream,
                  ensure_ascii=False, indent=2)
        stream.write("\n")
    return len(translations)


def merge_file(source: Path, translation: Path, output: Path,
               name_glossary: dict[str, str] | None = None) -> int:
    with source.open("r", encoding="utf-8", newline="") as stream:
        script = stream.read()
    items = fields(script)
    text_items = [item for item in items if item.key != "name"]
    rows = _read_csv(translation)
    if len(rows) != len(text_items) + 2:
        raise ValueError("CSV row count differs from the source script")
    info, translator = rows[-2:]
    checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
    if (info["id"], info["name"], info["text"]) != ("info", source.name, checksum):
        raise ValueError("CSV belongs to a different script or revision")
    if translator["id"] != "译者":
        raise ValueError("CSV translator footer is missing")
    honorifics = {source: target for source, target in (name_glossary or {}).items()
                  if source.startswith("{user}") and source != "{user}" and
                  source != target}
    changes = []
    for item, row in zip(text_items, rows[:-2]):
        if not matches_csv_identifier(item, row["id"]) or row["text"] != item.source:
            raise ValueError(f"CSV source mismatch at {item.identifier}")
        value = row["trans"]
        if not value or value == item.source:
            value = (name_glossary or {}).get(item.source, "") or value
        if not value or value == item.source:
            continue
        value = _merge_user_honorific(item, value, honorifics)
        validate_translation(item, value)
        value = re.sub(r"(?<!\\)=", r"\\=", value)
        changes.append((item.start, item.end, value))
    for item in items:
        if item.key != "name":
            continue
        value = (name_glossary or {}).get(item.source, "")
        if not value or value == item.source:
            continue
        validate_translation(item, value)
        value = re.sub(r"(?<!\\)=", r"\\=", value)
        changes.append((item.start, item.end, value))
    changes.sort(key=lambda change: change[0])
    for start, end, value in reversed(changes):
        script = script[:start] + value + script[end:]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="",
                                     dir=output.parent, prefix=".adv-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(script)
    try:
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return len(changes)
