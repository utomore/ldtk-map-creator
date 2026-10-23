"""FILE-1～FILE-5：場景地圖的讀寫。"""

import copy
import json
import math
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.scene import add_level, load_scene, new_scene, save_scene
from tests.ldtk_schema import TEMPLATES, level_file_errors, load_json, project_errors
from tests.strategies import SceneSpec, build_scene, scene_specs

FAST = settings(max_examples=40, deadline=None)


def _iids(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():  # pyright: ignore[reportUnknownVariableType]
            if key == "iid" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_iids(value))
    elif isinstance(node, list):
        for item in node:  # pyright: ignore[reportUnknownVariableType]
            found.extend(_iids(item))
    return found


@pytest.mark.law("FILE-1")
@FAST
@given(scene_specs)
def test_file_1_written_scene_matches_schema(spec: SceneSpec) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        written = save_scene(build_scene(spec, Path(tmp)))
        for path in written:
            data = load_json(path)
            errors = project_errors(data) if path.suffix == ".ldtk" else level_file_errors(data)
            assert errors == [], path.name


@pytest.mark.law("FILE-2")
@FAST
@given(scene_specs)
def test_file_2_uids_and_iids_unique(spec: SceneSpec) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        scene = build_scene(spec, Path(tmp))
        save_scene(scene)
        project = load_scene(scene.path).project
        defs = project["defs"]
        uids = [d["uid"] for d in defs["layers"]] + [d["uid"] for d in defs["entities"]]
        uids += [lv["uid"] for lv in project["levels"]]
        assert len(uids) == len(set(uids))
        assert all(uid < project["nextUid"] for uid in uids)
        iids = [*_iids(project), project["dummyWorldIid"]]
        assert [i for i, n in Counter(iids).items() if n > 1] == []


@pytest.mark.law("FILE-3")
@FAST
@given(scene_specs)
def test_file_3_roundtrip_is_stable(spec: SceneSpec) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        scene = build_scene(spec, Path(tmp))
        save_scene(scene)
        first = load_scene(scene.path)
        save_scene(first)
        assert load_scene(scene.path).project == first.project == scene.project


@pytest.mark.law("FILE-3")
@pytest.mark.parametrize("name", ["single.ldtk", "split.ldtk"])
def test_file_3_template_roundtrip_keeps_every_field(name: str, tmp_path: Path) -> None:
    shutil.copytree(TEMPLATES, tmp_path, dirs_exist_ok=True)
    before = load_scene(tmp_path / name)
    save_scene(before)
    assert load_scene(tmp_path / name).project == before.project


@pytest.mark.law("FILE-4")
@FAST
@given(scene_specs)
def test_file_4_intgrid_size_matches_level(spec: SceneSpec) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        scene = build_scene(spec, Path(tmp))
        save_scene(scene)
        for level in load_scene(scene.path).project["levels"]:
            for layer in level["layerInstances"] or []:
                if layer["__type"] != "IntGrid":
                    continue
                grid = layer["__gridSize"]
                assert layer["__cWid"] == math.ceil(level["pxWid"] / grid)
                assert layer["__cHei"] == math.ceil(level["pxHei"] / grid)
                assert len(layer["intGridCsv"]) == layer["__cWid"] * layer["__cHei"]


@pytest.mark.law("FILE-5")
@FAST
@given(scene_specs)
def test_file_5_split_layout(spec: SceneSpec) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "scene").mkdir()
        (folder / "scene" / "Gone.ldtkl").write_text("{}", encoding="utf-8")
        scene = build_scene(spec, folder)
        save_scene(scene)
        main = load_json(scene.path)
        actual = {f"scene/{p.name}" for p in folder.glob("scene/*.ldtkl")}
        if not spec.split:
            assert actual == set()
            return
        expected = {f"scene/{lv['identifier']}.ldtkl" for lv in main["levels"]}
        for level in main["levels"]:
            assert level["layerInstances"] is None
            assert (folder / level["externalRelPath"]).is_file()
        assert actual == expected


def test_example_new_scene_refuses_to_overwrite(tmp_path: Path) -> None:
    """邊界：已經存在的檔不覆蓋。"""
    path = tmp_path / "village.ldtk"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(SceneError):
        new_scene(path)


def test_example_add_level_rejects_duplicate_identifier(tmp_path: Path) -> None:
    """邊界：level 名稱重複，或不是合法識別名。"""
    scene = new_scene(tmp_path / "village.ldtk")
    add_level(scene, "Smithy", width_cells=8, height_cells=6)
    with pytest.raises(SceneError):
        add_level(scene, "Smithy", width_cells=8, height_cells=6)
    with pytest.raises(SceneError):
        add_level(scene, "1st-floor", width_cells=8, height_cells=6)


_OPPOSITE = {
    "n": "s",
    "s": "n",
    "w": "e",
    "e": "w",
    "nw": "se",
    "se": "nw",
    "ne": "sw",
    "sw": "ne",
    "o": "o",
    "<": ">",
    ">": "<",
}


def _expected_direction(a: Any, b: Any) -> str:
    """b 在 a 的哪個方向；用不等式寫，跟實作的等式寫法互相印證。"""
    vertical = "n" if b["worldY"] + b["pxHei"] <= a["worldY"] else ""
    vertical = vertical or ("s" if b["worldY"] >= a["worldY"] + a["pxHei"] else "")
    horizontal = "w" if b["worldX"] + b["pxWid"] <= a["worldX"] else ""
    horizontal = horizontal or ("e" if b["worldX"] >= a["worldX"] + a["pxWid"] else "")
    return (vertical + horizontal) or "o"


# 小格子上的 level，讓相接、碰角、重疊都常出現
_close_levels = st.lists(
    st.tuples(st.integers(1, 4), st.integers(1, 4), st.integers(-4, 4), st.integers(-4, 4)),
    min_size=2,
    max_size=5,
)


@pytest.mark.law("FILE-6")
@FAST
@given(_close_levels)
def test_file_6_neighbours_follow_geometry_and_are_symmetric(
    boxes: list[tuple[int, int, int, int]],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        scene = new_scene(Path(tmp) / "scene.ldtk")
        scene.project["levels"].clear()
        for i, (w, h, x, y) in enumerate(boxes):
            add_level(scene, f"L{i}", width_cells=w, height_cells=h, world_x=x * 16, world_y=y * 16)
        save_scene(scene)
        levels = load_scene(scene.path).project["levels"]
        by_iid = {lv["iid"]: lv for lv in levels}
        listed: dict[tuple[str, str], str] = {}
        for lv in levels:
            for n in cast(list[dict[str, str]], lv["__neighbours"]):
                listed[(lv["iid"], n["levelIid"])] = n["dir"]
        for (a, b), direction in listed.items():
            assert listed.get((b, a)) == _OPPOSITE[direction]
        for a, la in by_iid.items():
            for b, lb in by_iid.items():
                if a == b:
                    continue
                gap_x = max(la["worldX"], lb["worldX"]) - min(
                    la["worldX"] + la["pxWid"], lb["worldX"] + lb["pxWid"]
                )
                gap_y = max(la["worldY"], lb["worldY"]) - min(
                    la["worldY"] + la["pxHei"], lb["worldY"] + lb["pxHei"]
                )
                touching = gap_x <= 0 and gap_y <= 0
                assert ((a, b) in listed) == touching
                if touching:
                    assert listed[(a, b)] == _expected_direction(la, lb)


def test_example_edge_neighbours(tmp_path: Path) -> None:
    """典型：兩個 level 左右相接，互相列為 e 與 w。"""
    scene = new_scene(tmp_path / "village.ldtk")
    left = scene.level("Level_0")
    right = add_level(scene, "East", width_cells=4, height_cells=4, world_x=left["pxWid"])
    assert left["__neighbours"] == [{"dir": "e", "levelIid": right["iid"]}]
    assert right["__neighbours"] == [{"dir": "w", "levelIid": left["iid"]}]


def test_example_added_level_keeps_its_name(tmp_path: Path) -> None:
    """曾經壞過：自訂名稱的 level 要關掉 useAutoIdentifier，否則 LDtk 會改回 Level_N。"""
    scene = new_scene(tmp_path / "village.ldtk")
    level = add_level(scene, "Smithy", width_cells=4, height_cells=4)
    assert level["useAutoIdentifier"] is False


def test_example_add_level_rejects_case_only_difference(tmp_path: Path) -> None:
    """邊界：拆開存時檔名是 level 名稱，大小寫不同也會在 Windows 撞檔。"""
    scene = new_scene(tmp_path / "village.ldtk")
    add_level(scene, "Room", width_cells=4, height_cells=4)
    with pytest.raises(SceneError):
        add_level(scene, "room", width_cells=4, height_cells=4)


def test_example_tileset_path_survives_roundtrip(tmp_path: Path) -> None:
    """曾經壞過：有 tileset 的圖層，寫檔不能把 `__tilesetRelPath` 清成 null。"""
    scene = new_scene(tmp_path / "village.ldtk", split=False)
    defs = scene.project["defs"]
    defs["tilesets"].append(cast(Any, {"uid": 99, "relPath": "art/tiles.png"}))
    defs["layers"][0]["tilesetDefUid"] = 99
    save_scene(scene)
    layer = (load_scene(scene.path).project["levels"][0]["layerInstances"] or [])[0]
    assert layer["__tilesetRelPath"] == "art/tiles.png"


def test_example_load_reports_missing_level_file(tmp_path: Path) -> None:
    """邊界：`.ldtkl` 被刪掉，或路徑跑出資料夾，丟 SceneError 而不是 OSError。"""
    scene = new_scene(tmp_path / "village.ldtk")
    save_scene(scene)
    (tmp_path / "village" / "Level_0.ldtkl").unlink()
    with pytest.raises(SceneError):
        load_scene(scene.path)
    data = load_json(scene.path)
    data["levels"][0]["externalRelPath"] = "../outside.ldtkl"
    (tmp_path.parent / "outside.ldtkl").write_text("{}", encoding="utf-8")
    scene.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SceneError):
        load_scene(scene.path)


def test_example_save_rejects_intgrid_size_mismatch(tmp_path: Path) -> None:
    """邊界：IntGrid 格數跟 level 尺寸對不上時，寫檔前就擋下。"""
    scene = new_scene(tmp_path / "village.ldtk")
    scene.level("Level_0")["pxWid"] += 16
    with pytest.raises(SceneError):
        save_scene(scene)


def test_example_level_name_rejects_newline_and_device_names(tmp_path: Path) -> None:
    """曾經壞過：結尾換行會溜過檢查；Windows 裝置名稱不能當檔名。"""
    scene = new_scene(tmp_path / "village.ldtk")
    for bad in ["Room" + chr(10), "CON", "nul", "Com1"]:
        with pytest.raises(SceneError):
            add_level(scene, bad, width_cells=4, height_cells=4)


def test_example_failed_add_level_leaves_scene_unchanged(tmp_path: Path) -> None:
    """曾經壞過：新增失敗時，半成品的 level 不能留在場景地圖裡。"""
    scene = new_scene(tmp_path / "village.ldtk")
    scene.level("Level_0")["pxWid"] += 16
    with pytest.raises(SceneError):
        add_level(scene, "Smithy", width_cells=4, height_cells=4)
    assert [lv["identifier"] for lv in scene.project["levels"]] == ["Level_0"]


def test_example_override_tileset_uid_zero(tmp_path: Path) -> None:
    """邊界：uid 0 是合法的 uid，不能當成「沒有」。"""
    scene = new_scene(tmp_path / "village.ldtk", split=False)
    scene.project["defs"]["tilesets"].append(cast(Any, {"uid": 0, "relPath": "zero.png"}))
    layer = (scene.level("Level_0")["layerInstances"] or [])[0]
    layer["overrideTilesetUid"] = 0
    save_scene(scene)
    assert layer["__tilesetDefUid"] == 0
    assert layer["__tilesetRelPath"] == "zero.png"


def test_example_add_level_with_layer_type_missing_from_template(tmp_path: Path) -> None:
    """邊界：使用者在 LDtk 加了範本沒有的圖層型別（Tiles），照樣能新增 level。"""
    scene = new_scene(tmp_path / "village.ldtk")
    tiles = copy.deepcopy(scene.project["defs"]["layers"][1])
    tiles["type"] = "Tiles"
    tiles["identifier"] = "Decor"
    tiles["uid"] = 50
    scene.project["defs"]["layers"].append(tiles)
    level = add_level(scene, "Smithy", width_cells=4, height_cells=4)
    types = [layer["__type"] for layer in level["layerInstances"] or []]
    assert types == ["IntGrid", "Entities", "Tiles"]


def test_example_load_reports_broken_json(tmp_path: Path) -> None:
    """邊界：`.ldtkl` 寫壞（例如同步衝突），丟 SceneError。"""
    scene = new_scene(tmp_path / "village.ldtk")
    save_scene(scene)
    (tmp_path / "village" / "Level_0.ldtkl").write_text("{ broken", encoding="utf-8")
    with pytest.raises(SceneError):
        load_scene(scene.path)


def test_example_entity_grid_with_pivot_one(tmp_path: Path) -> None:
    """邊界：pivot 為 1 的 entity，`__grid` 照 LDtk 先減 1 再取整。"""
    scene = new_scene(tmp_path / "village.ldtk")
    spawn = scene.project["defs"]["entities"][0]
    spawn["pivotX"] = 1
    layer = (scene.level("Level_0")["layerInstances"] or [])[1]
    entity = copy.deepcopy(
        (_template_level_layers()[1])["entityInstances"][0]  # 範本裡的一個 Spawn
    )
    entity["px"] = [32, 0]
    layer["entityInstances"].append(entity)
    save_scene(scene)
    assert entity["__grid"] == [1, 0]


def test_example_linear_layout_has_no_neighbours(tmp_path: Path) -> None:
    """邊界：線性佈局沒有手動座標，不列相鄰、entity 沒有世界座標。"""
    scene = new_scene(tmp_path / "village.ldtk")
    scene.project["worldLayout"] = "LinearHorizontal"
    level = add_level(scene, "East", width_cells=4, height_cells=4, world_x=256)
    assert level["__neighbours"] == []
    assert scene.level("Level_0")["__neighbours"] == []


def _template_level_layers() -> list[Any]:
    return load_json(TEMPLATES / "single.ldtk")["levels"][0]["layerInstances"]
