"""用官方 LDtk 1.5.3 schema 驗證 JSON（FILE-1 的測試共用）。"""

import json
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "tests" / "schema" / "ldtk-1.5.3.schema.json"
TEMPLATES = ROOT / "src" / "ldtk_map_creator" / "core" / "templates"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def _validator(ref: str) -> jsonschema.Draft7Validator:
    schema = dict(load_json(SCHEMA_PATH))
    schema["$ref"] = ref
    return jsonschema.Draft7Validator(schema)


def project_errors(document: Any) -> list[str]:
    return _errors(document, "#/LdtkJsonRoot")


def level_file_errors(document: Any) -> list[str]:
    """`.ldtkl`：去掉 LDtk 自己加的 `__header__` 後，以 `Level` 驗證。"""
    level = dict(document)
    level.pop("__header__", None)
    return _errors(level, "#/otherTypes/Level")


def _errors(document: Any, ref: str) -> list[str]:
    # stub 的 iter_errors 有一個已棄用的 overload 帶 Unknown，只在這一行放寬
    found = _validator(ref).iter_errors(document)  # pyright: ignore[reportUnknownMemberType]
    return [f"{e.json_path}: {e.message}" for e in found]
