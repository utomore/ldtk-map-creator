"""SCENE-1～SCENE-4：畫 IntGrid、擺放 entity。"""

import copy
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ldtk_map_creator.core.edit import (
    delete_entity,
    move_entity,
    paint_cells,
    paint_rect,
    place_entity,
    read_cell,
)
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.scene import SceneMap, add_level, load_scene, new_scene, save_scene

FAST = settings(max_examples=60, deadline=None)

# 範本的 IntGrid 只宣告了 1；2 是未宣告的值
values = st.sampled_from([0, 1, 2])
sizes = st.tuples(st.integers(1, 12), st.integers(1, 12))
rects = st.tuples(st.integers(-2, 13), st.integers(-2, 13), st.integers(0, 6), st.integers(0, 6))


def _scene(folder: Path, w: int, h: int) -> SceneMap:
    scene = new_scene(folder / "scene.ldtk")
    add_level(scene, "Room", width_cells=w, height_cells=h, world_x=4096)
    return scene


def _csv(scene: SceneMap) -> list[int]:
    return list((scene.level("Room")["layerInstances"] or [])[0]["intGridCsv"])


@pytest.mark.law("SCENE-1")
@pytest.mark.law("SCENE-2")
@pytest.mark.law("SCENE-3")
@FAST
@given(sizes, st.lists(st.tuples(rects, values), max_size=6))
def test_scene_1_to_3_paint_rect(
    size: tuple[int, int], strokes: list[tuple[tuple[int, int, int, int], int]]
) -> None:
    w, h = size
    with tempfile.TemporaryDirectory() as tmp:
        scene = _scene(Path(tmp), w, h)
        for (x, y, rw, rh), value in strokes:
            before = _csv(scene)
            inside = x >= 0 and y >= 0 and rw >= 1 and rh >= 1 and x + rw <= w and y + rh <= h
            if not inside or value == 2:
                with pytest.raises(SceneError):
                    paint_rect(scene, "Room", x=x, y=y, width=rw, height=rh, value=value)
                assert _csv(scene) == before
                continue
            changed = paint_rect(scene, "Room", x=x, y=y, width=rw, height=rh, value=value)
            after = _csv(scene)
            for cy in range(h):
                for cx in range(w):
                    i = cy * w + cx
                    in_rect = x <= cx < x + rw and y <= cy < y + rh
                    assert after[i] == (value if in_rect else before[i])
            assert changed == sum(a != b for a, b in zip(after, before, strict=True))
        save_scene(scene)
        assert set(_csv(load_scene(scene.path))) <= {0, 1}


@pytest.mark.law("SCENE-2")
@pytest.mark.law("SCENE-3")
@FAST
@given(sizes, st.lists(st.tuples(st.integers(-1, 12), st.integers(-1, 12)), max_size=8), values)
def test_scene_2_3_paint_cells(
    size: tuple[int, int], cells: list[tuple[int, int]], value: int
) -> None:
    w, h = size
    with tempfile.TemporaryDirectory() as tmp:
        scene = _scene(Path(tmp), w, h)
        before = _csv(scene)
        if value == 2 or any(not (0 <= x < w and 0 <= y < h) for x, y in cells):
            with pytest.raises(SceneError):
                paint_cells(scene, "Room", cells=cells, value=value)
            assert _csv(scene) == before
            return
        changed = paint_cells(scene, "Room", cells=cells, value=value)
        after = _csv(scene)
        targets = {y * w + x for x, y in cells}
        for i in range(w * h):
            assert after[i] == (value if i in targets else before[i])
        assert changed == sum(a != b for a, b in zip(after, before, strict=True))


@pytest.mark.law("SCENE-3")
@pytest.mark.law("SCENE-4")
@FAST
@given(
    sizes,
    st.sampled_from([0.0, 0.5, 1.0]),
    st.tuples(st.integers(-1, 12), st.integers(-1, 12)),
    st.tuples(st.integers(-1, 12), st.integers(-1, 12)),
)
def test_scene_3_4_entities(
    size: tuple[int, int], pivot: float, start: tuple[int, int], end: tuple[int, int]
) -> None:
    w, h = size
    with tempfile.TemporaryDirectory() as tmp:
        scene = _scene(Path(tmp), w, h)
        spawn = scene.project["defs"]["entities"][0]
        spawn["pivotX"] = pivot
        spawn["pivotY"] = pivot
        snapshot = copy.deepcopy(scene.project)

        def inside(p: tuple[int, int]) -> bool:
            return 0 <= p[0] < w and 0 <= p[1] < h

        if not inside(start):
            with pytest.raises(SceneError):
                place_entity(scene, "Room", "Spawn", x=start[0], y=start[1])
            assert scene.project == snapshot
            return
        iid = place_entity(scene, "Room", "Spawn", x=start[0], y=start[1])["iid"]
        if inside(end):
            move_entity(scene, iid, x=end[0], y=end[1])
            where = end
        else:
            moved = copy.deepcopy(scene.project)
            with pytest.raises(SceneError):
                move_entity(scene, iid, x=end[0], y=end[1])
            assert scene.project == moved
            where = start
        save_scene(scene)
        loaded = load_scene(scene.path)
        entities = (loaded.level("Room")["layerInstances"] or [])[1]["entityInstances"]
        assert [e["__grid"] for e in entities if e["iid"] == iid] == [list(where)]
        delete_entity(loaded, iid)
        save_scene(loaded)
        again = load_scene(scene.path)
        assert all(
            e["iid"] != iid
            for lv in again.project["levels"]
            for layer in lv["layerInstances"] or []
            for e in layer["entityInstances"]
        )


def test_example_paint_names_layer_when_ambiguous(tmp_path: Path) -> None:
    """邊界：同型別的圖層不只一層時，要指定圖層名稱。"""
    scene = _scene(tmp_path, 4, 4)
    extra = copy.deepcopy(scene.project["defs"]["layers"][0])
    extra["identifier"] = "Height"
    extra["uid"] = 60
    scene.project["defs"]["layers"].append(extra)
    level = add_level(scene, "Two", width_cells=4, height_cells=4, world_x=-4096)
    assert len(level["layerInstances"] or []) == 3
    with pytest.raises(SceneError):
        paint_rect(scene, "Two", x=0, y=0, value=1)
    assert paint_rect(scene, "Two", x=0, y=0, value=1, layer="Height") == 1


def test_example_entity_with_fields_is_refused(tmp_path: Path) -> None:
    """還不支援：有欄位定義的 entity 先拒絕，不寫出 LDtk 讀不懂的欄位。"""
    scene = _scene(tmp_path, 4, 4)
    scene.project["defs"]["entities"][0]["fieldDefs"].append({"identifier": "target"})
    with pytest.raises(SceneError):
        place_entity(scene, "Room", "Spawn", x=0, y=0)


def test_example_read_cell(tmp_path: Path) -> None:
    """典型與邊界：讀回畫上去的值；出界丟 SceneError。"""
    scene = _scene(tmp_path, 4, 3)
    paint_rect(scene, "Room", x=1, y=2, value=1)
    assert read_cell(scene, "Room", x=1, y=2) == 1
    assert read_cell(scene, "Room", x=0, y=0) == 0
    with pytest.raises(SceneError):
        read_cell(scene, "Room", x=4, y=0)


def test_example_failed_place_and_move_leave_scene_unchanged(tmp_path: Path) -> None:
    """曾經壞過：level 本身有問題時，擺放與移動在動資料之前就擋下。"""
    scene = _scene(tmp_path, 4, 4)
    iid = place_entity(scene, "Room", "Spawn", x=0, y=0)["iid"]
    intgrid = (scene.level("Room")["layerInstances"] or [])[0]
    intgrid["intGridCsv"].append(0)  # 格數跟尺寸對不上
    snapshot = copy.deepcopy(scene.project)
    with pytest.raises(SceneError):
        place_entity(scene, "Room", "Spawn", x=1, y=1)
    with pytest.raises(SceneError):
        move_entity(scene, iid, x=2, y=2)
    assert scene.project == snapshot


def test_example_other_broken_level_does_not_block_edits(tmp_path: Path) -> None:
    """邊界：編輯只重算自己的 level；別的 level 有問題不影響。"""
    scene = _scene(tmp_path, 4, 4)
    scene.level("Level_0")["pxWid"] += 16
    entity = place_entity(scene, "Room", "Spawn", x=1, y=1)
    assert move_entity(scene, entity["iid"], x=2, y=3)["__grid"] == [2, 3]


def test_example_stale_intgrid_is_reported_not_half_painted(tmp_path: Path) -> None:
    """曾經壞過：格數過期的圖層不能畫一半後丟 IndexError。"""
    scene = _scene(tmp_path, 4, 4)
    scene.level("Room")["pxWid"] += 16
    snapshot = copy.deepcopy(scene.project)
    with pytest.raises(SceneError):
        paint_rect(scene, "Room", x=0, y=0, width=5, height=1, value=1)
    assert scene.project == snapshot


def test_example_max_count_follows_limit_behavior(tmp_path: Path) -> None:
    """邊界：到了 maxCount 照 limitBehavior，跟 LDtk 一樣。"""
    scene = _scene(tmp_path, 4, 4)
    spawn = scene.project["defs"]["entities"][0]
    spawn["maxCount"] = 1
    first = place_entity(scene, "Room", "Spawn", x=0, y=0)
    spawn["limitBehavior"] = "MoveLastOne"
    moved = place_entity(scene, "Room", "Spawn", x=3, y=3)
    assert moved["iid"] == first["iid"] and moved["__grid"] == [3, 3]
    spawn["limitBehavior"] = "DiscardOldOnes"
    newer = place_entity(scene, "Room", "Spawn", x=1, y=1)
    layer = (scene.level("Room")["layerInstances"] or [])[1]
    assert [e["iid"] for e in layer["entityInstances"]] == [newer["iid"]]
    spawn["limitBehavior"] = "PreventAdding"
    with pytest.raises(SceneError):
        place_entity(scene, "Room", "Spawn", x=2, y=2)


def test_example_layer_tags(tmp_path: Path) -> None:
    """邊界：照圖層的 required / excluded tags 擋下。"""
    scene = _scene(tmp_path, 4, 4)
    spawn = scene.project["defs"]["entities"][0]
    entities = scene.project["defs"]["layers"][1]
    entities["requiredTags"] = ["actor"]
    with pytest.raises(SceneError):
        place_entity(scene, "Room", "Spawn", x=1, y=0)
    spawn["tags"] = ["actor"]
    place_entity(scene, "Room", "Spawn", x=1, y=0)
    entities["excludedTags"] = ["actor"]
    with pytest.raises(SceneError):
        place_entity(scene, "Room", "Spawn", x=2, y=0)


def test_example_refuses_what_is_not_supported_yet(tmp_path: Path) -> None:
    """還不支援：auto-layer 規則、要匯出到 toc 的 entity；拒絕而不是寫出過期資料。"""
    scene = _scene(tmp_path, 4, 4)
    intgrid = scene.project["defs"]["layers"][0]
    intgrid["autoRuleGroups"].append({"name": "walls"})
    with pytest.raises(SceneError):
        paint_rect(scene, "Room", x=0, y=0, value=1)
    intgrid["autoRuleGroups"].clear()
    spawn = scene.project["defs"]["entities"][0]
    entity = place_entity(scene, "Room", "Spawn", x=0, y=0)
    spawn["exportToToc"] = True
    for action in (
        lambda: place_entity(scene, "Room", "Spawn", x=1, y=1),
        lambda: move_entity(scene, entity["iid"], x=1, y=1),
        lambda: delete_entity(scene, entity["iid"]),
    ):
        with pytest.raises(SceneError):
            action()


def test_example_paint_rejects_bool_and_huge_rect(tmp_path: Path) -> None:
    """曾經壞過：True 會被當成 1；超大範圍不能先展開成格子清單。"""
    scene = _scene(tmp_path, 4, 4)
    with pytest.raises(SceneError):
        paint_rect(scene, "Room", x=0, y=0, value=True)
    with pytest.raises(SceneError):
        paint_rect(scene, "Room", x=0, y=0, width=10**8, height=10**4, value=1)


def test_example_layer_found_by_definition_name(tmp_path: Path) -> None:
    """邊界：圖層定義改名後還沒 refresh，照新名字找得到。"""
    scene = _scene(tmp_path, 4, 4)
    scene.project["defs"]["layers"][0]["identifier"] = "Height"
    assert paint_rect(scene, "Room", x=0, y=0, value=1, layer="Height") == 1


def test_example_tile_field_keeps_ldtk_tile(tmp_path: Path) -> None:
    """曾經壞過：有 Tile 欄位的 entity，`__tile` 保留 LDtk 算好的值。"""
    scene = _scene(tmp_path, 4, 4)
    entity = place_entity(scene, "Room", "Spawn", x=0, y=0)
    rect = {"tilesetUid": 7, "x": 32, "y": 0, "w": 16, "h": 16}
    entity["fieldInstances"].append({"__type": "Tile", "__value": rect})
    entity["__tile"] = rect
    save_scene(scene)
    assert entity["__tile"] == rect


def test_example_entity_tile_follows_definition(tmp_path: Path) -> None:
    """曾經壞過：`__tile` 要跟著定義的 tileRect，不能留範本的 null。"""
    scene = _scene(tmp_path, 4, 4)
    rect = {"tilesetUid": 7, "x": 16, "y": 0, "w": 16, "h": 16}
    scene.project["defs"]["entities"][0]["tileRect"] = rect
    entity = place_entity(scene, "Room", "Spawn", x=0, y=0)
    assert entity["__tile"] == rect


def test_example_move_after_definition_renamed(tmp_path: Path) -> None:
    """邊界：定義改名後還沒 refresh，移動照 defUid 找定義。"""
    scene = _scene(tmp_path, 4, 4)
    iid = place_entity(scene, "Room", "Spawn", x=0, y=0)["iid"]
    scene.project["defs"]["entities"][0]["identifier"] = "PlayerStart"
    assert move_entity(scene, iid, x=3, y=3)["__grid"] == [3, 3]
