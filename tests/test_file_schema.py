"""FILE-1：範本通過官方 LDtk 1.5.3 schema。"""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "tests" / "schema" / "ldtk-1.5.3.schema.json"
TEMPLATES = ROOT / "src" / "ldtk_map_creator" / "core" / "templates"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _errors(document: Any, ref: str) -> list[str]:
    schema = dict(_load(SCHEMA_PATH))
    schema["$ref"] = ref
    validator = jsonschema.Draft7Validator(schema)
    # stub 的 iter_errors 有一個已棄用的 overload 帶 Unknown，只在這一行放寬
    found = validator.iter_errors(document)  # pyright: ignore[reportUnknownMemberType]
    return [f"{e.json_path}: {e.message}" for e in found]


@pytest.mark.law("FILE-1")
@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*.ldtk")), ids=lambda p: p.name)
def test_file_1_template_project_matches_schema(path: Path) -> None:
    assert _errors(_load(path), "#/LdtkJsonRoot") == []


@pytest.mark.law("FILE-1")
@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*/*.ldtkl")), ids=lambda p: p.name)
def test_file_1_template_level_matches_schema(path: Path) -> None:
    level = _load(path)
    level.pop("__header__", None)
    assert _errors(level, "#/otherTypes/Level") == []
