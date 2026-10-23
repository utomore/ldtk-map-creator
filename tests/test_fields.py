"""SCENE-6、SCENE-7：entity 欄位（#16）。"""

import copy
import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ldtk_map_creator.core.edit import (
    add_entity_def,
    delete_entity,
    move_entity,
    place_entity,
    set_entity_fields,
)
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.scene import SceneMap, add_level, load_scene, new_scene, save_scene
from tests.ldtk_schema import level_file_errors, load_json, project_errors

FAST = settings(max_examples=60, deadline=None)
FIELDS = {
    "count": "Int",
    "speed": "Float",
    "open": "Bool",
    "label": "String",
    "target": "EntityRef",
}
DEFAULTS: dict[str, object] = {"count": 0, "speed": 0, "open": False, "label": None, "target": None}

# 一步：（動作, level 0 或 1, x, y, 欄位, 值的種子）
steps = st.lists(
    st.tuples(
        st.sampled_from(["place", "set", "delete", "move"]),
        st.integers(0, 1),
        st.integers(0, 7),
        st.integers(0, 7),
        st.sampled_from(sorted(FIELDS)),
        st.integers(-3, 3),
    ),
    max_size=12,
)


def _scene(folder: Path, split: bool) -> SceneMap:
    scene = new_scene(folder / "scene.ldtk", split=split)
    scene.project["levels"].clear()
    add_level(scene, "Village", width_cells=8, height_cells=8)
    add_level(scene, "House", width_cells=8, height_cells=8, world_x=512)
    add_entity_def(scene, "Portal", field_types=FIELDS)
    return scene


def _value(field: str, seed: int, iids: list[str]) -> object:
    """依欄位挑一個合法的值；seed 是 0 時清空可以是空的欄位。"""
    if field == "target":
        return iids[seed % len(iids)] if iids and seed else None
    if field == "label":
        return None if seed == 0 else f"name{seed}"
    return {"count": seed, "speed": seed / 2, "open": seed > 0}[field]


def _entities(scene: SceneMap) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """iid → （圖層 iid, level iid, entity）。"""
    found: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for level in scene.project["levels"]:
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                found[entity["iid"]] = (layer["iid"], level["iid"], dict(entity))
    return found


@pytest.mark.law("SCENE-6")
@pytest.mark.law("SCENE-7")
@pytest.mark.law("FILE-1")
@pytest.mark.law("FILE-2")
@FAST
@given(steps, st.booleans())
def test_scene_6_7_fields_round_trip_and_refs_stay_valid(
    plan: list[tuple[str, int, int, int, str, int]], split: bool
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        scene = _scene(Path(tmp), split)
        expected: dict[str, dict[str, object]] = {}  # iid → 欄位值
        for action, level_index, x, y, field, seed in plan:
            level = ["Village", "House"][level_index]
            iids = list(expected)
            if action == "place":
                values = {field: _value(field, seed, iids)}
                iid = place_entity(scene, level, "Portal", x=x, y=y, values=values)["iid"]
                expected[iid] = {**DEFAULTS, **values}
            elif not iids:
                continue
            elif action == "set":
                iid = iids[(x + y) % len(iids)]
                value = _value(field, seed, iids)
                set_entity_fields(scene, iid, {field: value})
                expected[iid][field] = value
            elif action == "delete":
                iid = iids[(x + y) % len(iids)]
                delete_entity(scene, iid)
                del expected[iid]
                for values in expected.values():  # SCENE-7：指向它的 EntityRef 一起清掉
                    if values["target"] == iid:
                        values["target"] = None
            else:
                move_entity(scene, iids[(x + y) % len(iids)], x=x, y=y)
        save_scene(scene)
        for path in Path(tmp).rglob("*.ldtk*"):
            data = load_json(path)
            errors = project_errors(data) if path.suffix == ".ldtk" else level_file_errors(data)
            assert errors == [], path.name
        loaded = load_scene(Path(tmp) / "scene.ldtk")
    # FILE-2：連欄位定義在內，uid 互不重複、都小於 nextUid
    defs = loaded.project["defs"]
    uids = [d["uid"] for d in defs["layers"]] + [d["uid"] for d in defs["entities"]]
    uids += [f["uid"] for d in defs["entities"] for f in d["fieldDefs"]]
    uids += [lv["uid"] for lv in loaded.project["levels"]]
    assert len(set(uids)) == len(uids) and max(uids) < loaded.project["nextUid"]
    where = _entities(loaded)
    assert set(where) == set(expected)
    for iid, (_, _, entity) in where.items():
        instances = entity["fieldInstances"]
        assert [(f["__identifier"], f["__type"]) for f in instances] == list(FIELDS.items())
        got = {f["__identifier"]: f["__value"] for f in instances}
        for f in instances:  # 設過值的欄位，realEditorValues 的 id 對應型別（LDtk 1.5.3 範本）
            editor_id = {"Int": "V_Int", "Float": "V_Float", "Bool": "V_Bool"}.get(
                f["__type"], "V_String"
            )
            assert all(v["id"] == editor_id for v in f["realEditorValues"]), f
        for name, value in expected[iid].items():
            if name != "target":
                assert got[name] == value, (name, got[name], value)
        target = expected[iid]["target"]
        if target is None:
            assert got["target"] is None
            ref = next(f for f in instances if f["__identifier"] == "target")
            assert ref["realEditorValues"] == []  # 清掉的引用連原始值都不留
        else:
            layer_iid, level_iid, _ = where[str(target)]
            assert got["target"] == {
                "entityIid": target,
                "layerIid": layer_iid,
                "levelIid": level_iid,
                "worldIid": loaded.project["dummyWorldIid"],
            }


@pytest.mark.law("SCENE-7")
@pytest.mark.parametrize(
    "values",
    [
        {"count": "3"},
        {"count": True},
        {"speed": "fast"},
        {"open": 1},
        {"label": 5},
        {"count": None},
        {"target": "no-such-iid"},
        {"nope": 1},
    ],
    ids=[
        "str-int",
        "bool-int",
        "str-float",
        "int-bool",
        "int-str",
        "null-int",
        "dangling",
        "unknown",
    ],
)
def test_scene_7_bad_values_are_refused(values: dict[str, object], tmp_path: Path) -> None:
    scene = _scene(tmp_path, split=True)
    iid = place_entity(scene, "Village", "Portal", x=0, y=0)["iid"]
    before = copy.deepcopy(scene.project)
    with pytest.raises(SceneError):
        set_entity_fields(scene, iid, values)
    assert scene.project == before
    with pytest.raises(SceneError):
        place_entity(scene, "House", "Portal", x=1, y=1, values=values)
    assert scene.project == before


def test_example_portal_pair_across_levels(tmp_path: Path) -> None:
    """典型：兩個 level 各一個傳送點，target 互指（跟 LDtk 1.5.3 存的範本一樣）。"""
    scene = _scene(tmp_path, split=True)
    a = place_entity(scene, "Village", "Portal", x=2, y=3)["iid"]
    b = place_entity(scene, "House", "Portal", x=4, y=4, values={"target": a})["iid"]
    set_entity_fields(scene, a, {"target": b, "count": 3, "label": "hello"})
    save_scene(scene)
    where = _entities(load_scene(tmp_path / "scene.ldtk"))
    ref = {f["__identifier"]: f for f in where[a][2]["fieldInstances"]}["target"]
    assert ref["__value"]["levelIid"] == where[b][1]
    assert ref["realEditorValues"] == [{"id": "V_String", "params": [b]}]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"identifier": "Spawn"}, "同名"),
        ({"identifier": "9bad"}, "名稱"),
        ({"identifier": "X", "color": "red"}, "顏色"),
        ({"identifier": "X", "width": 0}, "尺寸"),
        ({"identifier": "X", "field_types": {"a": "Color"}}, "型別"),
        ({"identifier": "X", "field_types": {"a": "Int", "A": "Int"}}, "重複"),
    ],
)
def test_example_bad_entity_def_is_refused(
    kwargs: dict[str, Any], message: str, tmp_path: Path
) -> None:
    """邊界：entity 定義的名稱、顏色、尺寸、欄位寫錯都拒絕。"""
    scene = new_scene(tmp_path / "scene.ldtk")
    before = copy.deepcopy(scene.project)
    with pytest.raises(SceneError, match=message):
        add_entity_def(scene, **kwargs)
    assert scene.project == before


def test_example_ref_is_right_right_after_setting(tmp_path: Path) -> None:
    """設完跨 level 的引用，不用等寫檔，記憶體裡的 `__value` 就指對 level。"""
    scene = _scene(tmp_path, split=True)
    a = place_entity(scene, "Village", "Portal", x=0, y=0)["iid"]
    b = place_entity(scene, "House", "Portal", x=0, y=0)["iid"]
    entity = set_entity_fields(scene, a, {"target": b})
    ref = next(f for f in entity["fieldInstances"] if f["__identifier"] == "target")
    assert ref["__value"] == {
        "entityIid": b,
        "layerIid": _entities(scene)[b][0],
        "levelIid": scene.level("House")["iid"],
        "worldIid": scene.project["dummyWorldIid"],
    }


def test_example_uids_unique_when_next_uid_lags(tmp_path: Path) -> None:
    """邊界：檔案裡的 nextUid 落後於欄位定義的 uid 時，新的定義也不會撞號。"""
    scene = _scene(tmp_path, split=True)
    scene.project["nextUid"] = 1
    edef = add_entity_def(scene, "Door", field_types={"to": "EntityRef"})
    defs = scene.project["defs"]
    uids = [d["uid"] for d in defs["layers"]] + [d["uid"] for d in defs["entities"]]
    uids += [f["uid"] for d in defs["entities"] for f in d["fieldDefs"]]
    uids += [lv["uid"] for lv in scene.project["levels"]]
    assert len(set(uids)) == len(uids), edef


def test_example_delete_clears_refs_to_it(tmp_path: Path) -> None:
    """刪掉傳送點的一端，另一端的 target 清空（連原始值都清），跟 LDtk 一樣。"""
    scene = _scene(tmp_path, split=True)
    a = place_entity(scene, "Village", "Portal", x=0, y=0)["iid"]
    b = place_entity(scene, "House", "Portal", x=0, y=0, values={"target": a})["iid"]
    delete_entity(scene, a)
    save_scene(scene)
    entity = _entities(load_scene(tmp_path / "scene.ldtk"))[b][2]
    ref = next(f for f in entity["fieldInstances"] if f["__identifier"] == "target")
    assert ref["__value"] is None and ref["realEditorValues"] == []
