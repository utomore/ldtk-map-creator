"""SCENE-6、SCENE-7：entity 欄位（#16）。"""

import copy
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from ldtk_map_creator.core import actions
from ldtk_map_creator.core.edit import (
    add_entity_def,
    delete_entity,
    move_entity,
    place_entity,
    set_entity_fields,
    values_from_text,
)
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.model import FieldInstance
from ldtk_map_creator.core.render import render_level
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
            # 清空寫成 [null]（LDtk 的寫法）
            assert all(v is None or v["id"] == editor_id for v in f["realEditorValues"]), f
        for name, value in expected[iid].items():
            if name != "target":
                assert got[name] == value, (name, got[name], value)
        target = expected[iid]["target"]
        if target is None:
            assert got["target"] is None
            ref = next(f for f in instances if f["__identifier"] == "target")
            # 沒設過是 []、清掉的是 [null]；原本的 iid 都不留
            assert ref["realEditorValues"] in ([], [None])
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
    assert ref["__value"] is None and ref["realEditorValues"] == [None]


@pytest.mark.parametrize("behavior", ["MoveLastOne", "DiscardOldOnes"])
def test_example_max_count_keeps_fields_and_refs_right(behavior: str, tmp_path: Path) -> None:
    """到了數量上限：挪過來的照樣寫上欄位值；丟掉的那個，指向它的引用一起清掉、也不能再被指。"""
    scene = _scene(tmp_path, split=True)
    portal = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Portal")
    portal["maxCount"], portal["limitBehavior"], portal["limitScope"] = 1, behavior, "PerLevel"
    old = place_entity(scene, "Village", "Portal", x=0, y=0)["iid"]
    other = place_entity(scene, "House", "Portal", x=0, y=0, values={"target": old})["iid"]
    if behavior == "DiscardOldOnes":
        with pytest.raises(SceneError):  # 要丟掉的那個不能再被指
            place_entity(scene, "Village", "Portal", x=1, y=1, values={"target": old})
    new = place_entity(scene, "Village", "Portal", x=2, y=2, values={"count": 5})
    assert {f["__identifier"]: f["__value"] for f in new["fieldInstances"]}["count"] == 5
    ref = next(
        f for f in _entities(scene)[other][2]["fieldInstances"] if f["__identifier"] == "target"
    )
    if behavior == "DiscardOldOnes":
        assert ref["realEditorValues"] == [None] and ref["__value"] is None
    else:
        assert new["iid"] == old and ref["__value"]["entityIid"] == old


@pytest.mark.parametrize(
    ("change", "allowed"),
    [
        ({"allowedRefs": "OnlySame"}, False),
        ({"allowedRefs": "Any"}, True),
        ({"allowedRefs": "OnlySpecificEntity", "allowedRefsEntityUid": "spawn"}, True),
        ({"allowedRefs": "OnlyTags", "allowedRefTags": ["door"]}, False),
        ({"allowedRefs": "Any", "allowOutOfLevelRef": False}, False),
    ],
)
def test_example_ref_rules_follow_the_field_definition(
    change: dict[str, Any], allowed: bool, tmp_path: Path
) -> None:
    """EntityRef 照欄位定義的 allowedRefs、allowOutOfLevelRef 檢查（跟 LDtk 一樣）。"""
    scene = _scene(tmp_path, split=True)
    spawn_def = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Spawn")
    portal = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Portal")
    target_def = next(f for f in portal["fieldDefs"] if f["identifier"] == "target")
    for key, value in change.items():
        target_def[key] = spawn_def["uid"] if value == "spawn" else value  # type: ignore[literal-required]
    spawn = place_entity(scene, "House", "Spawn", x=0, y=0)["iid"]  # 另一個 level、另一種
    a = place_entity(scene, "Village", "Portal", x=0, y=0)["iid"]
    if allowed:
        set_entity_fields(scene, a, {"target": spawn})
    else:
        with pytest.raises(SceneError):
            set_entity_fields(scene, a, {"target": spawn})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 11])
def test_example_numbers_finite_and_in_range(value: float, tmp_path: Path) -> None:
    """NaN、無限大會寫壞 JSON，拒絕；超出定義的 min、max 也拒絕。"""
    scene = _scene(tmp_path, split=True)
    portal = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Portal")
    speed = next(f for f in portal["fieldDefs"] if f["identifier"] == "speed")
    speed["min"], speed["max"] = 0, 10
    iid = place_entity(scene, "Village", "Portal", x=0, y=0)["iid"]
    with pytest.raises(SceneError):
        set_entity_fields(scene, iid, {"speed": value})


def test_example_uid_not_taken_from_enums(tmp_path: Path) -> None:
    """邊界：enum、level 欄位這些定義的 uid 也算用掉的，新的定義不會撞號。"""
    scene = _scene(tmp_path, split=True)
    defs: dict[str, Any] = dict(scene.project["defs"])
    defs["enums"] = [{"uid": 900, "values": []}]
    defs["levelFields"] = [{"uid": 901}]
    scene.project["defs"] = defs  # type: ignore[typeddict-item]
    scene.project["nextUid"] = 1
    edef = add_entity_def(scene, "Door", field_types={"to": "EntityRef"})
    assert edef["uid"] > 901 and edef["fieldDefs"][0]["uid"] > 901


def test_example_set_fields_on_broken_level_changes_nothing(tmp_path: Path) -> None:
    """level 本身有問題時，設欄位在動資料之前就擋下。"""
    scene = _scene(tmp_path, split=True)
    iid = place_entity(scene, "Village", "Portal", x=0, y=0)["iid"]
    layers = scene.level("Village")["layerInstances"] or []
    next(ly for ly in layers if ly["__type"] == "IntGrid")["intGridCsv"].append(0)
    before = copy.deepcopy(scene.project)
    with pytest.raises(SceneError):
        set_entity_fields(scene, iid, {"count": 2})
    assert scene.project == before


def test_example_unsupported_field_type_is_refused(tmp_path: Path) -> None:
    """定義裡有還不支援的欄位型別（例如 Color），擺放照樣拒絕，不寫出讀不懂的值。"""
    scene = _scene(tmp_path, split=True)
    portal = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Portal")
    portal["fieldDefs"][0]["__type"] = "Color"
    with pytest.raises(SceneError, match="不支援"):
        place_entity(scene, "Village", "Portal", x=0, y=0)


def test_example_delete_updates_other_levels_at_once(tmp_path: Path) -> None:
    """刪掉一端之後，別的 level 的 `__value` 立刻變成空的，不用等寫檔。"""
    scene = _scene(tmp_path, split=True)
    a = place_entity(scene, "Village", "Portal", x=0, y=0)["iid"]
    b = place_entity(scene, "House", "Portal", x=0, y=0, values={"target": a})["iid"]
    delete_entity(scene, a)
    ref = next(f for f in _entities(scene)[b][2]["fieldInstances"] if f["__identifier"] == "target")
    assert ref["__value"] is None


def test_example_duplicate_field_instance_is_kept(tmp_path: Path) -> None:
    """手改過的檔同一個欄位有兩個實例：多的那個原樣留著，不悄悄丟掉（D-006）。"""
    scene = _scene(tmp_path, split=True)
    entity = place_entity(scene, "Village", "Portal", x=0, y=0)
    extra = copy.deepcopy(entity["fieldInstances"][0])
    entity["fieldInstances"].append(extra)
    save_scene(scene)
    assert len(entity["fieldInstances"]) == len(FIELDS) + 1


@pytest.mark.law("SCENE-5")
def test_scene_5_field_actions_keep_disk_and_preview_current(tmp_path: Path) -> None:
    """新的操作 add_entity_def、set_entity_fields 也是小交易：寫回磁碟、更新預覽圖。"""
    path = tmp_path / "scene.ldtk"
    actions.create_scene(path)
    result = actions.add_entity_def(path, "Door", field_types={"to": "EntityRef", "n": "Int"})
    assert result.previews and "Door" in result.summary
    a = actions.place_entity(path, "Level_0", "Door", x=1, y=1, values={"n": 2}).iid
    b = actions.place_entity(path, "Level_0", "Door", x=3, y=3, values={"to": a}).iid
    assert a is not None and b is not None
    result = actions.set_entity_fields(path, a, {"to": b, "n": 7})
    scene = load_scene(path)
    entity = _entities(scene)[a][2]
    got = {f["__identifier"]: f["__value"] for f in entity["fieldInstances"]}
    assert got["n"] == 7 and got["to"]["entityIid"] == b
    with Image.open(result.previews[0]) as image:
        assert image.tobytes() == render_level(scene, "Level_0").tobytes()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(SceneError):
        actions.set_entity_fields(path, a, {"n": "x"})
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_example_ldtk_null_value_and_default_override(tmp_path: Path) -> None:
    """LDtk 清掉設過的值會存成 `[null]`：讀得懂；有預設值的欄位清掉後是空的，不是預設值。"""
    scene = _scene(tmp_path, split=True)
    portal = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Portal")
    label = next(f for f in portal["fieldDefs"] if f["identifier"] == "label")
    label["defaultOverride"] = {"id": "V_String", "params": ["door"]}
    entity = place_entity(scene, "Village", "Portal", x=0, y=0)
    assert {f["__identifier"]: f["__value"] for f in entity["fieldInstances"]}["label"] == "door"
    set_entity_fields(scene, entity["iid"], {"label": None})
    got = {f["__identifier"]: f for f in entity["fieldInstances"]}["label"]
    assert got["__value"] is None and got["realEditorValues"] == [None]
    save_scene(scene)  # LDtk 存的 [null] 照樣讀得進來、寫得出去
    assert load_scene(tmp_path / "scene.ldtk") is not None


def test_example_write_goes_to_the_instance_refresh_uses(tmp_path: Path) -> None:
    """同一個欄位有兩個實例時，設定寫進 refresh 認的那個（第一個），值才會生效。"""
    scene = _scene(tmp_path, split=True)
    entity = place_entity(scene, "Village", "Portal", x=0, y=0)
    entity["fieldInstances"].append(copy.deepcopy(entity["fieldInstances"][0]))
    set_entity_fields(scene, entity["iid"], {"count": 5})
    first = next(f for f in entity["fieldInstances"] if f["__identifier"] == "count")
    assert first["__value"] == 5


def test_example_move_last_one_keeps_same_level_refs(tmp_path: Path) -> None:
    """數量上限挪 entity 到別的 level 時，只能指同一個 level 的引用不能被弄成跨 level。"""
    scene = _scene(tmp_path, split=True)
    portal = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Portal")
    portal["maxCount"], portal["limitBehavior"], portal["limitScope"] = 2, "MoveLastOne", "PerWorld"
    target = next(f for f in portal["fieldDefs"] if f["identifier"] == "target")
    target["allowOutOfLevelRef"] = False
    a = place_entity(scene, "Village", "Portal", x=0, y=0)["iid"]
    place_entity(scene, "Village", "Portal", x=1, y=1, values={"target": a})
    before = copy.deepcopy(scene.project)
    with pytest.raises(SceneError, match="level"):
        place_entity(scene, "House", "Portal", x=2, y=2)  # 會把指向 a 的那個挪到 House
    assert scene.project == before


def test_example_new_ref_field_can_point_to_any_entity(tmp_path: Path) -> None:
    """add_entity_def 建的 EntityRef 哪一種 entity 都能指（範本預設只能指同一種）。"""
    scene = _scene(tmp_path, split=True)
    add_entity_def(scene, "Door", field_types={"switch": "EntityRef"})
    switch = place_entity(scene, "Village", "Spawn", x=0, y=0)["iid"]
    door = place_entity(scene, "Village", "Door", x=1, y=1, values={"switch": switch})
    ref = next(f for f in door["fieldInstances"] if f["__identifier"] == "switch")
    assert cast(dict[str, object], ref["__value"])["entityIid"] == switch


def test_example_describe_lists_fields(tmp_path: Path) -> None:
    """describe_scene 列出定義有哪些欄位、每個 entity 的欄位值，agent 才知道能設什麼。"""
    path = tmp_path / "scene.ldtk"
    actions.create_scene(path)
    actions.add_entity_def(path, "Sign", field_types={"label": "String"})
    actions.place_entity(path, "Level_0", "Sign", x=0, y=0, values={"label": "hi"})
    text = actions.describe_scene(path).summary
    assert "label：String" in text and "label = 'hi'" in text


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        ({"label": "123", "n": "4"}, {"label": "123", "n": 4}),
        ({"label": "true", "on": "true", "x": "1.5"}, {"label": "true", "on": True, "x": 1.5}),
        ({"label": "null", "n": "null"}, {"label": "null", "n": None}),
    ],
)
def test_example_cli_text_follows_field_type(
    texts: dict[str, str], expected: dict[str, object], tmp_path: Path
) -> None:
    """CLI 的 名稱=值 照欄位型別轉：String 欄位的 123、true 是字串；其他型別的 null 是清空。"""
    scene = _scene(tmp_path, split=True)
    edef = add_entity_def(
        scene, "Sign", field_types={"label": "String", "n": "Int", "on": "Bool", "x": "Float"}
    )
    assert values_from_text(edef, texts) == expected
    with pytest.raises(SceneError):
        values_from_text(edef, {"n": "four"})


def test_example_delete_with_instance_missing_def_uid(tmp_path: Path) -> None:
    """手改過的檔有沒有 defUid 的欄位實例：刪 entity 照樣能刪，不當掉。"""
    scene = _scene(tmp_path, split=True)
    entity = place_entity(scene, "Village", "Portal", x=0, y=0)
    entity["fieldInstances"].append(cast(FieldInstance, {"__identifier": "odd", "__value": 1}))
    other = place_entity(scene, "House", "Portal", x=0, y=0)["iid"]
    delete_entity(scene, other)
