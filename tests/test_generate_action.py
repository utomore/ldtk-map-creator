"""GEN-5、GEN-6：照規則生成的操作，與「被直接改過就擋下來」（D-008）。"""

import json
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from PIL import Image

from ldtk_map_creator.core import actions, record
from ldtk_map_creator.core.edit import read_cell
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.render import preview_path, render_level
from ldtk_map_creator.core.rules import Rules
from ldtk_map_creator.core.scene import load_scene
from tests.rule_strategies import placeable_village_rules, to_toml_text

FAST = settings(max_examples=40, deadline=None)
EXAMPLE = Path(__file__).parent / "data" / "village.rules.toml"


def _files(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def _write_rules(r: Rules, path: Path) -> None:
    path.write_text(to_toml_text(r), encoding="utf-8")


@pytest.mark.law("GEN-5")
@pytest.mark.law("SCENE-5")
@FAST
@given(placeable_village_rules())
def test_gen_5_record_matches_disk_and_regenerating_is_a_no_op(r: Rules) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        path = folder / "village.ldtk"
        _write_rules(r, record.rules_path(path))
        actions.generate_scene(path)
        saved = record.read_record(path)
        assert saved is not None
        on_disk = load_scene(path)
        assert saved["content"] == record.content(on_disk)
        with Image.open(preview_path(on_disk, r.level_name)) as image:
            names = record.labels(saved, r.level_name)
            expected = render_level(on_disk, r.level_name, labels=names)
            assert image.tobytes() == expected.tobytes()
        before = _files(folder)
        result = actions.generate_scene(path)
        assert _files(folder) == before
        assert "沒有元素移動" in result.summary


# 一次直接修改：（種類、x、y）；x、y 拿來挑格子或 entity
edits = st.tuples(
    st.sampled_from(["paint", "place", "move", "delete", "level"]),
    st.integers(0, 99),
    st.integers(0, 99),
)


@pytest.mark.law("GEN-6")
@FAST
@given(placeable_village_rules(), edits, st.booleans())
def test_gen_6_direct_edit_blocks_regeneration_until_forced(
    r: Rules, change: tuple[str, int, int], force_twice: bool
) -> None:
    kind, a, b = change
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        path = folder / "village.ldtk"
        _write_rules(r, record.rules_path(path))
        actions.generate_scene(path)
        fresh = _files(folder)
        width, height = r.level_size
        x, y = a % width, b % height
        iids = [
            e["iid"]
            for ly in load_scene(path).level(r.level_name)["layerInstances"] or []
            for e in ly["entityInstances"]
        ]
        if kind == "paint":
            current = read_cell(load_scene(path), r.level_name, x=x, y=y)
            actions.paint_cells(path, r.level_name, cells=[(x, y)], value=(current + 1) % 4)
        elif kind == "place":
            actions.place_entity(path, r.level_name, "Spawn", x=x, y=y)
        elif kind in ("move", "delete"):
            assume(iids)
            iid = iids[a % len(iids)]
            if kind == "delete":
                actions.delete_entity(path, iid)
            else:
                grid = next(
                    e["__grid"]
                    for ly in load_scene(path).level(r.level_name)["layerInstances"] or []
                    for e in ly["entityInstances"]
                    if e["iid"] == iid
                )
                assume(tuple(grid) != (x, y))
                actions.move_entity(path, iid, x=x, y=y)
        else:
            actions.add_level(path, "Extra", width_cells=2, height_cells=2, world_x=4096)

        edited = _files(folder)
        with pytest.raises(SceneError, match="被直接改過") as error:
            actions.generate_scene(path)
        assert _files(folder) == edited
        assert "\n- " in str(error.value)  # 列出差異

        actions.generate_scene(path, force=True)
        if force_twice:
            actions.generate_scene(path)  # 蓋掉之後就不再擋
        after = _files(folder)
        # 蓋掉之後跟剛生成時一樣，連新加的 level 的預覽圖都清掉
        assert after == fresh


def test_example_one_line_change_moves_one_element(tmp_path: Path) -> None:
    """驗收：改一行規則重新生成，只有那個元素移動，摘要也只列它。"""
    path = tmp_path / "village.ldtk"
    rules_file = record.rules_path(path)
    shutil.copy(EXAMPLE, rules_file)
    actions.generate_scene(path)
    text = rules_file.read_text(encoding="utf-8")
    rules_file.write_text(text.replace('side = "east"', 'side = "west"'), encoding="utf-8")
    result = actions.generate_scene(path)
    assert "位置變了：house_2" in result.summary
    assert "新的" not in result.summary and "拿掉了" not in result.summary


def test_example_scene_without_record_is_not_overwritten(tmp_path: Path) -> None:
    """用 M1 的操作建的場景地圖，加了規則檔也不會被直接蓋掉：沒有紀錄就不知道改過什麼。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path)
    shutil.copy(EXAMPLE, record.rules_path(path))
    before = _files(tmp_path)
    with pytest.raises(SceneError, match="沒有上次生成的紀錄"):
        actions.generate_scene(path)
    assert _files(tmp_path) == before
    actions.generate_scene(path, force=True)
    assert record.read_record(path) is not None


def _generated(tmp_path: Path) -> Path:
    path = tmp_path / "village.ldtk"
    shutil.copy(EXAMPLE, record.rules_path(path))
    actions.generate_scene(path)
    return path


def test_example_first_generation_keeps_leftover_level_files(tmp_path: Path) -> None:
    """主檔不在、子資料夾還有手做的 level 檔時，第一次生成也不蓋掉。"""
    path = tmp_path / "village.ldtk"
    shutil.copy(EXAMPLE, record.rules_path(path))
    (tmp_path / "village").mkdir()
    (tmp_path / "village" / "Cave.ldtkl").write_text("{}", encoding="utf-8")
    before = _files(tmp_path)
    with pytest.raises(SceneError, match="不覆蓋"):
        actions.generate_scene(path)
    assert _files(tmp_path) == before


def test_example_ldtk_resave_is_not_an_edit_but_moving_a_level_is(tmp_path: Path) -> None:
    """在 LDtk 打開再存一次（格式、版本號、顏色大小寫變了）不算改過；搬動 level 算。"""
    path = _generated(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["jsonVersion"] = "9.9.9"
    for layer in data["defs"]["layers"]:
        for value in layer["intGridValues"]:
            value["color"] = value["color"].lower()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    actions.generate_scene(path)

    # LDtk 拆開存時，主檔與 .ldtkl 都會寫上新的世界座標
    for file in (path, tmp_path / "village" / "Village.ldtkl"):
        data = json.loads(file.read_text(encoding="utf-8"))
        (data["levels"][0] if "levels" in data else data)["worldX"] = 512
        file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SceneError, match="世界座標"):
        actions.generate_scene(path)


def test_example_force_recovers_from_broken_record(tmp_path: Path) -> None:
    """生成紀錄壞掉時：不帶 force 丟 SceneError，帶 force 照規則重寫。"""
    path = _generated(tmp_path)
    for broken in (
        "{",
        '{"version": 1}',
        '{"version": 1, "elements": {}, "content": {"defs": {}, "levels": {}}}',
    ):
        record.record_path(path).write_text(broken, encoding="utf-8")
        with pytest.raises(SceneError, match="生成紀錄"):
            actions.generate_scene(path)
        actions.generate_scene(path, force=True)
        assert record.read_record(path) is not None


def test_example_unchanged_level_is_not_rewritten(tmp_path: Path) -> None:
    """規則沒變時重新生成，level 檔不重寫（LDtk 不會一直跳「檔案在外面被改過」）。"""
    path = _generated(tmp_path)
    level_file = tmp_path / "village" / "Village.ldtkl"
    before = level_file.stat().st_mtime_ns
    time.sleep(0.05)
    actions.generate_scene(path)
    assert level_file.stat().st_mtime_ns == before


def test_example_resize_reports_size_only() -> None:
    """尺寸變了但格數相同（4x6 變 6x4）時，只報尺寸，不列對不上座標的格子。"""

    def level(w: int, h: int) -> dict[str, Any]:
        layer = {
            "type": "IntGrid",
            "width": w,
            "settings": [0, 0, True, 1],
            "values": [],
            "cells": [0] * (w * h),
            "tiles": [],
        }
        return {
            "size": [w, h],
            "world": [0, 0],
            "fields": [],
            "layers": {"IntGrid": layer},
            "entities": [],
        }

    defs: dict[str, Any] = {"layers": [], "entities": [], "tilesets": []}
    old: dict[str, Any] = {"defs": defs, "levels": {"L": level(4, 6)}}
    new: dict[str, Any] = {"defs": defs, "levels": {"L": level(6, 4)}}
    new["levels"]["L"]["layers"]["IntGrid"]["cells"][0] = 1
    assert record.differences(old, new) == ["L：大小 4x6 變成 6x4"]


def _edit_json(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_example_case_only_rename_keeps_new_preview(tmp_path: Path) -> None:
    """level 只改大小寫（Village → village）時，新的預覽圖不能被當成舊的刪掉。

    Windows 不分大小寫，新舊預覽圖是同一個檔。
    """
    path = _generated(tmp_path)
    rules_file = record.rules_path(path)
    text = rules_file.read_text(encoding="utf-8")
    rules_file.write_text(text.replace('name = "Village"', 'name = "village"'), encoding="utf-8")
    result = actions.generate_scene(path)
    assert all(p.is_file() for p in result.previews)


def test_example_other_previews_are_kept(tmp_path: Path) -> None:
    """預覽圖資料夾裡不是 level 的圖（例如之後的世界總覽）不會被操作刪掉。"""
    path = _generated(tmp_path)
    world = tmp_path / "previews" / "village" / "world.png"
    world.write_bytes(b"png")
    actions.paint_cells(path, "Village", cells=[(0, 0)], value=1)
    actions.generate_scene(path, force=True)
    assert world.is_file()


def test_example_broken_content_asks_for_force(tmp_path: Path) -> None:
    """在 LDtk 刪了還在用的圖層定義：丟 SceneError 並提示 force，不丟 KeyError。"""
    path = _generated(tmp_path)
    _edit_json(path, lambda d: d["defs"]["layers"].pop(0))
    with pytest.raises(SceneError, match="force"):
        actions.generate_scene(path)


def test_example_grid_size_change_is_listed(tmp_path: Path) -> None:
    """在 LDtk 改了 IntGrid 的格子大小：列出「格子大小改了」，不誤報成紀錄壞掉。"""
    path = _generated(tmp_path)
    level_file = tmp_path / "village" / "Village.ldtkl"

    def finer(d: dict[str, Any]) -> None:
        for layer in d["defs"]["layers"]:
            if layer["type"] == "IntGrid":
                layer["gridSize"] = 8

    def more_cells(d: dict[str, Any]) -> None:
        for layer in d["layerInstances"]:
            if layer["__type"] == "IntGrid":
                layer["intGridCsv"] = [0] * (len(layer["intGridCsv"]) * 4)

    _edit_json(path, finer)
    _edit_json(level_file, more_cells)
    with pytest.raises(SceneError, match="格子大小改了"):
        actions.generate_scene(path)


def test_example_nudged_entity_is_an_edit(tmp_path: Path) -> None:
    """在 LDtk 把 entity 在同一格裡挪幾個像素，也算直接改過。"""
    path = _generated(tmp_path)

    def nudge(d: dict[str, Any]) -> None:
        for layer in d["layerInstances"]:
            for entity in layer["entityInstances"]:
                entity["px"][0] += 3

    _edit_json(tmp_path / "village" / "Village.ldtkl", nudge)
    with pytest.raises(SceneError, match="像素位置"):
        actions.generate_scene(path)


def test_example_missing_map_ignores_leftover_record(tmp_path: Path) -> None:
    """地圖不在、只剩壞掉的生成紀錄：沒有東西要保護，照樣生成。"""
    path = tmp_path / "village.ldtk"
    shutil.copy(EXAMPLE, record.rules_path(path))
    record.record_path(path).write_text("{", encoding="utf-8")
    actions.generate_scene(path)
    assert record.read_record(path) is not None


def test_example_force_keeps_single_file_storage(tmp_path: Path) -> None:
    """單檔存的地圖壞掉時帶 force 重寫，仍然存成單檔（D-002）。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path, split=False)
    shutil.copy(EXAMPLE, record.rules_path(path))
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: len(text) // 2], encoding="utf-8")  # 寫到一半當掉的主檔
    actions.generate_scene(path, force=True)
    assert not (tmp_path / "village").exists()
    assert not load_scene(path).split


def test_example_force_keeps_leftover_level_files(tmp_path: Path) -> None:
    """主檔不在、子資料夾有別的 level 檔：帶 force 也不蓋，那些檔不是生成的。"""
    path = tmp_path / "village.ldtk"
    shutil.copy(EXAMPLE, record.rules_path(path))
    (tmp_path / "village").mkdir()
    (tmp_path / "village" / "Cave.ldtkl").write_text("{}", encoding="utf-8")
    with pytest.raises(SceneError, match="不覆蓋"):
        actions.generate_scene(path, force=True)
    assert (tmp_path / "village" / "Cave.ldtkl").is_file()


def test_example_render_level_keeps_names(tmp_path: Path) -> None:
    """render_level 操作重畫從規則生成的 level，也寫上元素名稱，跟生成時的預覽圖一樣。"""
    path = _generated(tmp_path)
    preview = tmp_path / "previews" / "village" / "Village.png"
    generated = preview.read_bytes()
    actions.render_level(path, "Village")
    assert preview.read_bytes() == generated


def test_example_names_only_when_map_matches_record(tmp_path: Path) -> None:
    """直接改過的地圖不寫元素名稱（名稱可能已經不在元素上），並在摘要說明；改回規則就回來。"""
    path = _generated(tmp_path)
    preview = tmp_path / "previews" / "village" / "Village.png"
    result = actions.paint_cells(path, "Village", cells=[(0, 0)], value=1)
    assert "不寫元素名稱" in result.summary
    scene = load_scene(path)
    with Image.open(preview) as image:
        assert image.tobytes() == render_level(scene, "Village").tobytes()
    actions.generate_scene(path, force=True)
    scene = load_scene(path)
    saved = record.read_record(path)
    with Image.open(preview) as image:
        names = record.labels(saved, "Village")
        assert image.tobytes() == render_level(scene, "Village", labels=names).tobytes()


def test_example_no_record_no_names(tmp_path: Path) -> None:
    """沒有生成紀錄的場景地圖（用 M1 的操作建的），就算放了規則檔，預覽圖也不寫名稱。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path)
    shutil.copy(EXAMPLE, record.rules_path(path))
    result = actions.paint_cells(path, "Level_0", cells=[(0, 0)], value=1)
    with Image.open(result.previews[0]) as image:
        assert image.tobytes() == render_level(load_scene(path), "Level_0").tobytes()
    assert "元素名稱" not in result.summary


def test_example_broken_record_says_so(tmp_path: Path) -> None:
    """生成紀錄讀不了時，操作照做，摘要說預覽圖沒寫名稱。"""
    path = _generated(tmp_path)
    record.record_path(path).write_text("{", encoding="utf-8")
    result = actions.render_level(path, "Village")
    assert "生成紀錄讀不了" in result.summary
