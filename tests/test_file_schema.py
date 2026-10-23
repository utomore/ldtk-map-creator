"""FILE-1：範本通過官方 LDtk 1.5.3 schema。"""

from pathlib import Path

import pytest

from tests.ldtk_schema import TEMPLATES, level_file_errors, load_json, project_errors


@pytest.mark.law("FILE-1")
@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*.ldtk")), ids=lambda p: p.name)
def test_file_1_template_project_matches_schema(path: Path) -> None:
    assert project_errors(load_json(path)) == []


@pytest.mark.law("FILE-1")
@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*/*.ldtkl")), ids=lambda p: p.name)
def test_file_1_template_level_matches_schema(path: Path) -> None:
    assert level_file_errors(load_json(path)) == []
